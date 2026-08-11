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

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def _prepare_paths(bronze, gold):
    os.environ["DB_BACKEND"] = "duckdb"
    os.environ["DELTA_TABLES_PATH"] = bronze
    os.environ.setdefault("LINE", "ngp2")
    sys.path.insert(0, os.path.join(ROOT, "src"))
    sys.path.insert(0, os.path.join(ROOT, "bin"))
    os.makedirs(gold, exist_ok=True)


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
    return compute_all(refresh=False, lines=("ngp2",), tables=GOLD_TABLES, spc_only=True)


def _gold_path(gold, name):
    return os.path.join(gold, f"{GOLD_PREFIX}{name}")


def _read_gold(gold, name):
    from deltalake import DeltaTable

    path = _gold_path(gold, name)
    if not DeltaTable.is_deltatable(path):
        return None
    df = DeltaTable(path).to_pandas()
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
    snaps = new["Snapshots"].copy()
    snaps["SnapshotTime"] = pd.to_datetime(snaps["SnapshotTime"])
    keys = set(snaps["SnapshotKey"])

    merged = {"Lines": new["Lines"].copy()}   # single row, no history to keep

    for name in ("Snapshots", "Shifts", "SPC"):
        incoming = snaps if name == "Snapshots" else new[name].copy()
        prior = _read_gold(gold, name)
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

    return merged


def write_gold(merged, gold):
    from deltalake import write_deltalake

    for name in GOLD_TABLES:
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
        write_deltalake(path, df, mode="overwrite", schema_mode="overwrite")
        print(f"  {GOLD_PREFIX}{name:10s} {len(df):>7,} rows x {len(df.columns)} cols")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bronze", default="/lakehouse/default/Tables",
                    help="Delta dir holding the mirrored source tables")
    ap.add_argument("--gold", default="/lakehouse/default/Tables",
                    help=f"Delta dir for the {GOLD_PREFIX}* tables the semantic model reads")
    args = ap.parse_args(argv)

    _prepare_paths(args.bronze, args.gold)
    print(f"Bronze: {args.bronze}\nGold:   {args.gold}\n")

    new = compute_snapshot()
    for name, df in new.items():
        print(f"  computed {name:10s} {len(df):>7,} rows")

    merged = merge_history(new, args.gold)
    print()
    write_gold(merged, args.gold)

    snaps = merged["Snapshots"]
    print(f"\nRetained {len(snaps)} snapshot(s), "
          f"{snaps['SnapshotTime'].min()} .. {snaps['SnapshotTime'].max()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
