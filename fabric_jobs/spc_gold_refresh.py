#!/usr/bin/env python3
"""NGP2 SPC Gold refresh — the body of the Fabric notebook.

Runs every 15 minutes inside a Fabric Python notebook (no Spark, no gateway):

  Bronze Delta (landed by Dataflow Gen2 over the existing SQL gateway)
    -> DuckDB, via the DB_BACKEND=duckdb shim in src/core/db.py
    -> weekly_analysis.compute_spc_only()   [the real pipeline, unmodified]
    -> export_powerbi_csv.build_tables(spc_only=True)
    -> Gold Delta tables read by the Direct Lake semantic model

Why this file is a plain script rather than notebook cells: it can be run and
diffed on a workstation against the same local Delta export used by
bin/verify_duckdb_parity.py, so the Fabric-bound code is testable before deploy.
The Fabric notebook is a thin wrapper around main() — see
fabric_jobs/notebook_bootstrap.py. (The directory is `fabric_jobs`, not `fabric`,
because `fabric` is already taken by a PyPI package and would shadow it on the
notebook's import path.)

Snapshot history lives in the Gold tables, not on disk: the notebook filesystem
is discarded after every run, so each run computes exactly one snapshot and
merges it into the retained Gold history (same 3-day window the local
file-based store used).

Local test run:
    python fabric_jobs/spc_gold_refresh.py \
        --bronze "$TEMP/claude/c--hollister-dashboard/bronze" \
        --gold   "$TEMP/claude/c--hollister-dashboard/bronze"
"""

import argparse
import os
import shutil
import sys
import tempfile

import pandas as pd
import pyarrow as pa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onelake                                                  # noqa: E402

GOLD_TABLES = ("Lines", "Shifts", "SPC", "Snapshots")

# Gold and Bronze share one Lakehouse Tables/ folder — Direct Lake can only bind
# to Lakehouse tables, so the outputs have to live there too. The prefix keeps
# them from colliding with the mirrored source tables (and makes it obvious in the
# Lakehouse explorer which tables the report actually reads).
GOLD_PREFIX = "gold_"

# Columns the Direct Lake semantic model types as dateTime. Delta column types
# must line up with the model's column types, so cast here rather than leaving
# the pipeline's "%Y-%m-%d %H:%M:%S" strings in place.
DATETIME_COLS = {
    "Shifts": ["ShiftDate"],
    "Snapshots": ["SnapshotTime"],
}

# Retained history, matching SPC_SNAPSHOT_RETENTION_DAYS in weekly_analysis.py.
RETENTION_DAYS = 3

# The Direct Lake model to reframe once Gold is rewritten. Hardcoded for the same
# reason notebook_bootstrap.py hardcodes the workspace and lakehouse ids: the
# notebook cell is not generated from anything, so there is nowhere to pass them
# in from, and a wrong id here fails loudly on the first run.
REFRAME_WORKSPACE_ID = "daff049b-5e21-4d61-8cf2-465032703de5"   # Smart Factory
REFRAME_MODEL_ID = "1c5363ab-60d3-4a82-a673-4f1e6f7d00d8"       # NPG2 SPC Dashboard

# Gold column types, DECLARED rather than inferred from the DataFrame.
#
# Inference is not usable here for two compounding reasons. An empty pandas object
# column gives Arrow a `null` type, which delta-rs writes as Delta `"type":"void"` —
# not a valid Delta data type, so the table cannot be read again by anything
# (delta-rs, DuckDB's delta extension, or Direct Lake) and every later run dies with
# `data did not match any variant of untagged enum DataType`. Substituting a
# placeholder type avoids the corruption but leaves a worse problem: the schema would
# then *change* between runs depending on whether the SPC panels produced rows, and a
# Direct Lake model binds to column types. A run with 0 SPC rows would break a model
# bound to a run with 350.
#
# The SPC panels legitimately return nothing whenever the line has been down for most
# of the shift window, so 0-row Gold is normal operation, not an error path.
#
# Captured from a populated run (350 SPC rows). _gold_arrow() casts every write to
# these, so an empty table and a full one are byte-compatible.
GOLD_SCHEMAS = {
    "Lines": pa.schema([
        ("LineId", pa.string()),
        ("Line", pa.string()),
        ("GeneratedAt", pa.string()),
    ]),
    "Shifts": pa.schema([
        ("ShiftKey", pa.string()),
        ("SnapshotKey", pa.string()),
        ("LineId", pa.string()),
        ("Line", pa.string()),
        ("ShiftDate", pa.timestamp("us", tz="UTC")),
        ("ShiftType", pa.string()),
        ("Label", pa.string()),
        ("LineShiftLabel", pa.string()),
        ("IsoWeek", pa.string()),
        ("WeekLabel", pa.string()),
        ("IsLatest", pa.int64()),
    ]),
    "SPC": pa.schema([
        ("SnapshotKey", pa.string()),
        ("LineId", pa.string()),
        ("Line", pa.string()),
        ("Panel", pa.string()),
        ("ShiftLabel", pa.string()),
        ("LineShiftLabel", pa.string()),
        ("Lane", pa.int64()),
        ("Rejects", pa.int64()),
        ("RatePct", pa.float64()),
        ("UclPct", pa.float64()),
        ("OOC", pa.int64()),
        ("NInspected", pa.int64()),
        ("OOCColor", pa.string()),
        ("ShiftIdx", pa.int64()),
        ("PBar", pa.float64()),
        ("IsLatest", pa.int64()),
        ("RatePctLabel", pa.string()),
        ("LaneLabel", pa.string()),
    ]),
    "Snapshots": pa.schema([
        ("SnapshotKey", pa.string()),
        ("SnapshotTime", pa.timestamp("us", tz="UTC")),
        ("SnapshotLabel", pa.string()),
        ("IsLatestSnapshot", pa.int64()),
    ]),
}


def _prepare_paths(bronze, gold, workdir=None):
    """Point the pipeline's intermediate files at one directory on real local disk.

    The pipeline *writes* its snapshots through a path relative to the process CWD
    (`weekly_analysis.SPC_SNAPSHOT_DIR`) but *reads* them back through a path
    absolute to the code directory (`export_powerbi_csv.ANALYSES`). Run from the repo
    root those are the same directory, which is why this has never mattered — but a
    notebook's CWD is not the code directory, so the snapshot written was not the
    snapshot read and Shifts/SPC/Snapshots came out empty while Lines, which is not
    derived from snapshots, looked fine.

    Anchoring both on one scratch directory fixes it, and keeping that directory on
    local disk rather than the Lakehouse mount avoids relying on mount write
    semantics for the intermediates. Nothing is lost by discarding it after the run:
    the retained snapshot history lives in the Gold Delta tables.
    """
    # Resolved before the chdir below, or a relative path passed on the command line
    # would silently point somewhere else afterwards.
    bronze = bronze if onelake.is_remote(bronze) else os.path.abspath(bronze)
    gold = gold if onelake.is_remote(gold) else os.path.abspath(gold)

    os.environ["DB_BACKEND"] = "duckdb"
    os.environ["DELTA_TABLES_PATH"] = bronze
    os.environ.setdefault("LINE", "ngp2")
    sys.path.insert(0, os.path.join(ROOT, "src"))
    sys.path.insert(0, os.path.join(ROOT, "bin"))

    work = workdir or os.path.join(tempfile.gettempdir(), "ngp2_spc_gold")
    os.makedirs(work, exist_ok=True)
    os.chdir(work)                      # makes the relative writes land in `work`
    import export_powerbi_csv           # noqa: PLC0415
    export_powerbi_csv.ANALYSES = os.path.join(work, "output", "analyses")
    print(f"Work:   {work}")

    # os.makedirs would create a literal 'abfss:' directory; a remote Gold root needs
    # no pre-creation, write_deltalake creates the table path itself.
    if not onelake.is_remote(gold):
        os.makedirs(gold, exist_ok=True)
    return work, bronze, gold


def compute_snapshot():
    """Run the real SPC pipeline against Bronze; return {table_name: DataFrame}.

    compute_spc_only() writes the analysis JSON plus one snapshot file, then
    build_tables() flattens that into the four report tables. refresh=False keeps
    compute_all() from re-invoking the pipeline as a subprocess — it has already
    run in-process here, which keeps the notebook to a single Python process.
    """
    from pipelines import weekly_analysis
    from powerbi_compute import compute_all

    # A fresh notebook has no leftover snapshots, but a re-run in the same session
    # would double-count; start from a clean store so this run produces exactly one.
    if os.path.isdir(weekly_analysis.SPC_SNAPSHOT_DIR):
        shutil.rmtree(weekly_analysis.SPC_SNAPSHOT_DIR)

    weekly_analysis.compute_spc_only()
    data = compute_all(refresh=False, lines=("ngp2",), tables=GOLD_TABLES, spc_only=True)
    _add_spc_report_labels(data["SPC"])
    return data


def _add_spc_report_labels(spc_df):
    """Add report-only display columns the native (non-Python) SPC visuals bind to.

    Direct Lake does not support calculated columns with standard DAX row-context
    expressions (confirmed by the Fabric API: "Standard expression context may not
    be used for calculated columns in Direct Lake tables"), so text like the
    OOC-suffixed label or the per-panel lane prefix has to be computed here, as a
    real physical column, rather than as DAX in the model.

    Mutates spc_df in place; a no-op on an empty frame (RatePct/OOC/Panel/Lane
    still exist as columns even with 0 rows, since compute_all() always returns
    the full column set).
    """
    spc_df["RatePctLabel"] = (
        spc_df["RatePct"].map(lambda x: f"{x:.2f}%")
        + spc_df["OOC"].map(lambda o: "  OOC" if o == 1 else "")
    )
    spc_df["LaneLabel"] = [
        f"N{lane}" if panel == "Nozzle" else f"Lane {lane}"
        for panel, lane in zip(spc_df["Panel"], spc_df["Lane"])
    ]


def _gold_path(gold, name):
    # onelake.join, not os.path.join: on Fabric `gold` is an abfss URI because
    # delta-rs cannot commit through the Lakehouse mount (see fabric_jobs/onelake.py).
    return onelake.join(gold, f"{GOLD_PREFIX}{name}")


def _read_gold(gold, name):
    from deltalake import DeltaTable

    path = _gold_path(gold, name)
    opts = onelake.storage_options(gold)
    if not DeltaTable.is_deltatable(path, storage_options=opts):
        return None
    df = DeltaTable(path, storage_options=opts).to_pandas()
    # Strip the UTC tag write_gold() applies, so retained history and this run's
    # freshly computed rows (naive, straight out of the pipeline) merge in one
    # representation instead of colliding as mixed-dtype object columns.
    for col in DATETIME_COLS.get(name, []):
        if col in df.columns and getattr(df[col].dtype, "tz", None) is not None:
            df[col] = df[col].dt.tz_localize(None)
    return df


def merge_history(new, gold):
    """Fold this run's snapshot into the retained Gold history.

    Replaces (not appends) rows sharing this run's SnapshotKey so a re-run inside
    the same 15-minute bucket is idempotent, prunes anything past the retention
    window, then recomputes IsLatestSnapshot across the survivors so the report's
    locked "latest snapshot" filter always resolves to the newest run.
    """
    import time

    snaps = new["Snapshots"].copy()
    snaps["SnapshotTime"] = pd.to_datetime(snaps["SnapshotTime"])
    keys = set(snaps["SnapshotKey"])

    merged = {"Lines": new["Lines"].copy()}   # single row, no history to keep
    read_timings = {}

    for name in ("Snapshots", "Shifts", "SPC"):
        incoming = snaps if name == "Snapshots" else new[name].copy()
        t0 = time.perf_counter()
        prior = _read_gold(gold, name)
        read_timings[name] = round(time.perf_counter() - t0, 1)
        if prior is not None and not prior.empty:
            prior = prior[~prior["SnapshotKey"].isin(keys)]
            frame = pd.concat([prior, incoming], ignore_index=True)
        else:
            frame = incoming
        merged[name] = frame

    cutoff = merged["Snapshots"]["SnapshotTime"].max() - pd.Timedelta(days=RETENTION_DAYS)
    kept = merged["Snapshots"][merged["Snapshots"]["SnapshotTime"] >= cutoff].copy()
    latest = kept["SnapshotTime"].max()
    kept["IsLatestSnapshot"] = (kept["SnapshotTime"] == latest).astype("int64")
    merged["Snapshots"] = kept.sort_values("SnapshotTime").reset_index(drop=True)

    live = set(kept["SnapshotKey"])
    for name in ("Shifts", "SPC"):
        merged[name] = merged[name][merged[name]["SnapshotKey"].isin(live)].reset_index(drop=True)

    # RatePctLabel/LaneLabel are pure functions of RatePct/OOC/Panel/Lane. Recompute
    # them across the whole merged frame (not just this run's new rows) so retained
    # history written before these columns existed doesn't carry nulls forward —
    # those showed up as spurious "(Blank)" categories on the native SPC charts.
    _add_spc_report_labels(merged["SPC"])

    merged["_read_timings"] = read_timings   # not a Gold table; write_gold() only loops GOLD_TABLES
    return merged


def _gold_arrow(name, df):
    """Cast a Gold frame to its declared schema. See GOLD_SCHEMAS for why.

    Column-set drift is raised rather than absorbed: if build_tables() starts
    emitting a different set of columns, the semantic model needs updating too, and
    silently writing a different shape would only surface later as a broken report.
    """
    schema = GOLD_SCHEMAS[name]
    missing = [c for c in schema.names if c not in df.columns]
    extra = [c for c in df.columns if c not in schema.names]
    if missing or extra:
        raise RuntimeError(
            f"gold_{name} column drift vs GOLD_SCHEMAS: "
            f"missing={missing} unexpected={extra}. Update GOLD_SCHEMAS and the "
            f"Direct Lake model together."
        )
    return pa.Table.from_pandas(df[schema.names], preserve_index=False).cast(schema)


def write_gold(merged, gold):
    import time

    from deltalake import write_deltalake

    opts = onelake.storage_options(gold)
    for name in GOLD_TABLES:
        t0 = time.perf_counter()
        df = merged[name].copy()
        for col in DATETIME_COLS.get(name, []):
            if col in df.columns:
                # Tag as UTC so Delta stores `timestamp` rather than `timestamp_ntz`.
                # timestamp_ntz needs a newer Delta reader feature; `timestamp` has
                # been supported since Delta 1.0, so this avoids a Direct Lake
                # compatibility question for no behavioural cost — Power BI renders
                # the stored clock value without timezone conversion either way, and
                # these columns are only used for sorting and latest-snapshot
                # comparison, never for arithmetic across zones.
                df[col] = (pd.to_datetime(df[col])
                             .dt.tz_localize("UTC")
                             .astype("datetime64[us, UTC]"))
        path = _gold_path(gold, name)
        # Full overwrite: the largest of these is the 3-day SPC history (order
        # 10^4 rows), so rewriting is cheaper and far simpler to reason about
        # than merge/append plus a separate prune.
        write_deltalake(path, _gold_arrow(name, df), mode="overwrite",
                        schema_mode="overwrite", storage_options=opts)
        print(f"  {GOLD_PREFIX}{name:10s} {len(df):>7,} rows x {len(df.columns)} cols   "
              f"[{time.perf_counter() - t0:.1f}s]")


def reframe_semantic_model(workspace_id=REFRAME_WORKSPACE_ID,
                           model_id=REFRAME_MODEL_ID):
    """Tell the Direct Lake model to reload the Gold tables this run just wrote.

    Why this exists at all: **Activator cannot see changes in a Direct Lake model
    until the model is refreshed.** It keeps reporting whatever values it first
    framed, however many times the underlying Delta tables change, so an alert on
    `Out of Control` never fires no matter how many bars breach the UCL. That is a
    documented Microsoft bug rather than a rule misconfiguration.

    Why it is here and not a pipeline activity: the pipeline's
    `PBISemanticModelRefresh` activity requires an `externalReferences` connection
    that can only be minted by configuring the activity in the portal — deploying
    it from a generated definition fails every run with "The external references
    connection cannot be found in the trident payload". The notebook already holds
    an identity that can call the Power BI API directly, so it needs no connection
    object.

    Deliberately non-fatal. By the time this runs, Gold is written and correct; a
    reframe that fails costs freshness in the report, not data, and failing the
    pipeline for it would turn a cosmetic problem into a red run that masks real
    ones. Returns True only if the refresh was accepted.
    """
    import json                                                    # noqa: PLC0415
    import urllib.error                                            # noqa: PLC0415
    import urllib.request                                          # noqa: PLC0415

    try:
        import notebookutils                                       # noqa: PLC0415
    except ImportError:
        print("  reframe: skipped (not in a Fabric notebook)")
        return False

    # The audience string for the Power BI API has moved around between runtime
    # versions, so probe rather than pin — same reasoning as bronze_merge._arrow().
    token = None
    for audience in ("pbi", "powerbi", "analysis"):
        try:
            token = notebookutils.credentials.getToken(audience)
            if token:
                print(f"  reframe: got token for audience {audience!r}")
                break
        except Exception:                                          # noqa: BLE001
            continue
    if not token:
        print("  reframe: FAILED — no Power BI token from notebookutils; the report "
              "will still be correct but Activator alerts will not fire")
        return False

    url = (f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"
           f"/datasets/{model_id}/refreshes")
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps({"type": "Full"}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            print(f"  reframe: accepted (HTTP {r.status})")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        # 409 means a refresh is already running, which is a no-op for us, not a fault.
        level = "note" if e.code == 409 else "FAILED"
        print(f"  reframe: {level} — HTTP {e.code} {body}")
        return e.code == 409
    except Exception as e:                                         # noqa: BLE001
        print(f"  reframe: FAILED — {type(e).__name__}: {e}")
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bronze", default="/lakehouse/default/Tables",
                    help="Delta dir holding the mirrored source tables")
    ap.add_argument("--gold", default="/lakehouse/default/Tables",
                    help=f"Delta dir for the {GOLD_PREFIX}* tables the semantic model reads")
    args = ap.parse_args(argv)

    # Use the returned paths, not args: _prepare_paths resolves them to absolute
    # before it changes directory, so the raw args would point elsewhere afterwards.
    import time

    _work, bronze, gold = _prepare_paths(args.bronze, args.gold)
    print(f"Bronze: {bronze}\nGold:   {gold}\n")

    t0 = time.perf_counter()
    new = compute_snapshot()
    t1 = time.perf_counter()
    for name, df in new.items():
        print(f"  computed {name:10s} {len(df):>7,} rows")

    merged = merge_history(new, gold)
    t2 = time.perf_counter()
    read_timings = merged.pop("_read_timings", {})
    print(f"  read prior gold: " + "  ".join(f"{k} {v}s" for k, v in read_timings.items()))
    print()
    write_gold(merged, gold)
    t3 = time.perf_counter()

    snaps = merged["Snapshots"]
    print(f"\nRetained {len(snaps)} snapshot(s), "
          f"{snaps['SnapshotTime'].min()} .. {snaps['SnapshotTime'].max()}")
    print(f"[timing] compute_snapshot: {t1 - t0:6.1f}s   merge_history: {t2 - t1:6.1f}s   "
          f"write_gold: {t3 - t2:6.1f}s")

    print()
    reframe_semantic_model()
    return 0


if __name__ == "__main__":
    sys.exit(main())
