#!/usr/bin/env python3
"""Fold the freshly-copied tail of each source table into the Bronze Delta tables.

This exists because the Pipeline Copy activity route has no equivalent of Dataflow
Gen2's incremental refresh. Gen2 took two settings ("store 75 days, refresh 2
days") and handled watermarking, partition replacement and retention itself. Copy
activity does none of that: it can only append or overwrite a whole table. So the
incremental behaviour is reproduced here, in Python, where it can be tested.

Shape of the deal with the pipeline:

    Copy activity  ->  stage_<table>   (mode Overwrite; the last N hours of source
                                        data, re-read in full every run)
    this module     ->  <table>        (Bronze; the stage window replaces the same
                                        window in Bronze, atomically)

The replacement is a Delta `replaceWhere`: every Bronze row at or after the
stage's own earliest watermark is replaced by every stage row. That makes a run
idempotent, so re-running, overlapping runs, or a pipeline retry cannot duplicate
rows — which is why the Copy activity can stay completely stateless and just
re-read a fixed tail rather than tracking a high-water mark.

Two properties worth knowing:

  * The tail is anchored on `MAX(watermark)` *in the source table*, not on the
    clock, exactly as bin/export_bronze_local.py does. That sidesteps the question
    of whether the historian writes local or UTC timestamps, and means a stalled
    historian re-reads the same rows harmlessly instead of silently reading none.
  * Consequently the pipeline must run at least once per tail window (default 6h)
    or a gap opens. Recover by re-running the backfill pipeline; nothing here
    needs resetting.

Rows are passed through as Arrow straight from the stage table's Delta schema, so
Bronze column types stay whatever the Copy activity wrote them as. Round-tripping
through pandas would risk re-typing timestamps as `timestamp_ntz` and reopening
the Direct Lake reader-version question settled in spc_gold_refresh.write_gold().

Local test run (against the export bin/export_bronze_local.py produces):
    python fabric_jobs/bronze_merge.py --bronze "$TEMP/claude/c--hollister-dashboard/bronze"
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onelake                                                  # noqa: E402

# Copy activity writes here; this module reads and then leaves them in place. They
# share the Lakehouse Tables/ folder with Bronze and Gold, so the prefix is what
# keeps them distinguishable in the Lakehouse explorer.
STAGE_PREFIX = "stage_"

# Bronze must cover _SPC_LOOKBACK_DAYS in src/metrics/spc.py, which defaults to 75.
# The headroom above that is deliberate: the lookback is anchored on the newest
# t_stamp, so trimming Bronze to exactly 75 days would leave the oldest subgroup
# scan reading the very edge of the table.
RETENTION_DAYS = 80


def _plan():
    sys.path.insert(0, os.path.join(ROOT, "bin"))
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from export_bronze_local import bronze_plan
    return bronze_plan()


def _duck():
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL delta; LOAD delta;")
    return con


def _arrow(result):
    """Materialise a DuckDB result as a pyarrow Table, whatever the duckdb version.

    duckdb has renamed this three times: `.arrow()` (which on 1.5.x hands back a
    RecordBatchReader, not a Table), `.fetch_arrow_table()` (deprecated on 1.5.x),
    and `.to_arrow_table()`. The first Fabric run failed here with
    `AttributeError: 'DuckDBPyConnection' object has no attribute 'to_arrow_table'`
    because the notebook's preinstalled duckdb is older than the pinned one and
    `%pip install` did not displace it. Probing is more reliable than pinning, since
    the pin is not enforceable in that environment.
    """
    for name in ("to_arrow_table", "fetch_arrow_table"):
        fn = getattr(result, name, None)
        if fn is not None:
            return fn()
    out = result.arrow()
    return out.read_all() if hasattr(out, "read_all") else out


def _posix(path):
    return path.replace("\\", "/").replace("'", "''")


def merge_table(con, root, tbl, watermark_col, retention_days=RETENTION_DAYS,
                write_root=None):
    """Replace Bronze's tail window with the staged copy of it. Returns a summary dict.

    `root` is read through DuckDB (the Lakehouse mount is fine for reads);
    `write_root` is where delta-rs commits, which on Fabric must be an abfss URI —
    see the module docstring in fabric_jobs/onelake.py.
    """
    from deltalake import DeltaTable, write_deltalake

    write_root = write_root or root
    opts = onelake.storage_options(write_root)
    bronze_write = onelake.join(write_root, tbl)
    bronze_path = os.path.join(root, tbl)
    stage_path = os.path.join(root, f"{STAGE_PREFIX}{tbl}")

    if not DeltaTable.is_deltatable(stage_path):
        return {"table": tbl, "action": "skipped", "note": "no stage table yet"}

    # Arrow rather than pandas so the column types stay exactly as the Copy activity
    # wrote them — a pandas round-trip would re-type naive datetimes as
    # `timestamp_ntz` and reopen the Direct Lake reader-version question that
    # spc_gold_refresh.write_gold() settled.
    stage = _arrow(con.execute(f"SELECT * FROM delta_scan('{_posix(stage_path)}')"))
    if stage.num_rows == 0:
        return {"table": tbl, "action": "skipped", "note": "stage empty"}

    # The replaced window is defined by the staged data itself, so the rows removed
    # and the rows written cover exactly the same range — no gap, no overlap.
    tail_start = con.execute(
        f'SELECT MIN("{watermark_col}") FROM delta_scan(\'{_posix(stage_path)}\')'
    ).fetchone()[0]

    if not DeltaTable.is_deltatable(bronze_path):
        # No backfill has run. Seeding Bronze from the tail alone would look
        # successful while leaving the SPC baseline far short of its lookback, so
        # say so rather than quietly producing thin control limits.
        write_deltalake(bronze_write, stage, mode="overwrite", schema_mode="overwrite",
                        storage_options=opts)
        return {"table": tbl, "action": "created", "rows": stage.num_rows,
                "note": "Bronze did not exist — run the backfill pipeline, this is "
                        "only the tail window"}

    write_deltalake(
        bronze_write, stage, mode="overwrite",
        predicate=f'"{watermark_col}" >= TIMESTAMP \'{tail_start}\'',
        storage_options=opts,
    )

    pruned = _prune(con, bronze_path, watermark_col, retention_days,
                    bronze_write, opts)
    total = con.execute(f"SELECT COUNT(*) FROM delta_scan('{_posix(bronze_path)}')").fetchone()[0]
    return {"table": tbl, "action": "merged", "rows": stage.num_rows,
            "from": tail_start, "pruned": pruned, "total": total}


def _prune(con, bronze_path, watermark_col, retention_days, write_target=None,
           opts=None):
    """Drop rows older than the retention window, anchored on the newest row.

    Checked before deleting because a Delta delete rewrites every file it touches;
    on most runs nothing has aged out and the check is a metadata-only read.
    """
    from deltalake import DeltaTable

    lo, hi = con.execute(
        f'SELECT MIN("{watermark_col}"), MAX("{watermark_col}") '
        f"FROM delta_scan('{_posix(bronze_path)}')"
    ).fetchone()
    if lo is None or hi is None:
        return 0
    import datetime

    cutoff = hi - datetime.timedelta(days=retention_days)
    if lo >= cutoff:
        return 0
    dt = DeltaTable(write_target or bronze_path, storage_options=opts)
    res = dt.delete(predicate=f'"{watermark_col}" < TIMESTAMP \'{cutoff}\'')
    return int(res.get("num_deleted_rows", 0) or 0)


def merge_all(root, retention_days=RETENTION_DAYS, write_root=None):
    import time

    con = _duck()
    results = []
    try:
        for _key, tbl, watermark, _cols, _like in _plan():
            t0 = time.perf_counter()
            r = merge_table(con, root, tbl, watermark, retention_days, write_root)
            r["seconds"] = round(time.perf_counter() - t0, 1)
            results.append(r)
    finally:
        con.close()
    return results


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bronze", default="/lakehouse/default/Tables",
                    help="dir DuckDB reads stage_* and Bronze from (the mount is fine)")
    ap.add_argument("--write-root",
                    help="where delta-rs commits; on Fabric this must be the abfss "
                         "OneLake URI, since the mount does not support rename. "
                         "Defaults to --bronze for local runs.")
    ap.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    args = ap.parse_args(argv)

    # Printed because %pip install cannot be relied on in a Fabric notebook — the
    # first run there used an older duckdb than the pinned one. If anything
    # version-sensitive breaks again, the run log says which versions were in play.
    import duckdb
    import deltalake
    print(f"duckdb {duckdb.__version__} / deltalake {deltalake.__version__} / "
          f"python {sys.version.split()[0]}")
    write_root = args.write_root or args.bronze
    print(f"Bronze merge: read {args.bronze}\n"
          f"              write {write_root} (retention {args.retention_days}d)\n")
    skipped = 0
    for r in merge_all(args.bronze, args.retention_days, args.write_root):
        secs = f"  [{r.get('seconds', 0):5.1f}s]"
        if r["action"] == "skipped":
            skipped += 1
            print(f"  {r['table']:48s} skipped — {r['note']}{secs}")
        elif r["action"] == "created":
            print(f"  {r['table']:48s} CREATED {r['rows']:>9,} rows — {r['note']}{secs}")
        else:
            print(f"  {r['table']:48s} +{r['rows']:>8,} staged   "
                  f"total {r['total']:>10,}   pruned {r['pruned']:>7,}   "
                  f"from {r['from']}{secs}")
    if skipped:
        print(f"\n  {skipped} table(s) had no staged data — check the Copy activities ran.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
