#!/usr/bin/env python3
"""Export the Bronze SPC footprint from live SQL Server to local Delta tables.

Purpose: let the DuckDB backend (src/core/db.py, DB_BACKEND=duckdb) be exercised
and verified on this machine before any of it is deployed to Fabric. The
directory this produces has the same shape as a Fabric Lakehouse `Tables/`
folder, so `DELTA_TABLES_PATH` can point at either one.

It also doubles as the specification for the Dataflow Gen2 Bronze queries: the
tables, columns, filters and watermark columns printed here are exactly what the
dataflow needs to land.

Usage:
    source .env
    python bin/export_bronze_local.py [--days 30] [--out DIR]
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src")))
from core.db import get_connection, query                       # noqa: E402
from core.lines import get_line_config, get_col, get_status_block_cols  # noqa: E402

CFG = get_line_config()

# Columns the SPC code path actually reads off a counter table. Narrowing to
# these (and to the counter-name patterns below) is what keeps the Bronze mirror
# ~10x smaller than a full raw copy — see the module docstring in core/db.py.
_COUNTER_KEYS = ("block_id", "t_stamp", "row_id")


def _counter_specs():
    """{table_key: (name_col, counter_col, [LIKE patterns])} from spc_panels config."""
    specs = {}
    for panel in CFG.get("spc_panels", []):
        p = panel.get("params", panel)
        key = p.get("table_key")
        if not key:
            continue
        name_col, counter_col = p["name_col"], p["counter_col"]
        pats = [p[k] for k in ("pattern", "rear_pattern", "front_pattern") if p.get(k)]
        if key in specs:
            specs[key][2].extend(pats)
        else:
            specs[key] = (name_col, counter_col, list(pats))
    return specs


def bronze_plan():
    """The Bronze footprint: [(logical_key, table, watermark_col, select_cols, like_pairs)].

    Single source of truth, shared with fabric_jobs/deploy/generate_dataflow.py so
    the mirror this script verifies against live SQL is the same mirror the
    Dataflow Gen2 deployment lands. select_cols None means every column;
    like_pairs None means no row filter.
    """
    sb = get_status_block_cols(CFG)
    ts = get_col(CFG, "t_stamp")
    plan = [
        ("status_blocks", CFG["tables"]["status_blocks"], sb.C_END, None, None),
        ("ppm", CFG["tables"]["ppm"], ts, [ts], None),
        ("output_stats", CFG["tables"]["output_stats"], ts,
         [ts, get_col(CFG, "counter_total")], None),
    ]
    for key, (name_col, counter_col, pats) in _counter_specs().items():
        cols = list(_COUNTER_KEYS) + [name_col, counter_col]
        plan.append((key, CFG["tables"][key], ts, cols, (name_col, sorted(set(pats)))))
    return plan


# ──────────────────────────────────────────────
# One-copy union stage
# ──────────────────────────────────────────────
#
# The 15-minute pipeline originally ran one Copy activity per Bronze table. Data
# movement bills `duration x intelligent-throughput-optimization x 1.5 CU-hours`,
# and a copy over the gateway costs ~90s of that whatever it carries — connection,
# query and commit overhead dominate at this size. Five copies moving ~4.5k rows
# between them therefore paid that toll five times for no more data. One Copy
# activity carrying all five sources as a single UNION ALL pays it once.
#
# Every source is projected into the slot list below and tagged with its
# bronze_plan() key in `src`; fabric_jobs/bronze_merge.py splits it back out and
# projects each group onto that table's own Bronze schema. Slots are shared where
# name and type already coincide (the two counter tables, plus Counter_Total
# riding in ctr_val) and dedicated where they don't (StatusBlocks, prefixed sb_).
UNION_STAGE_TABLE = "stage_union"

# StatusBlocks is the one plan entry whose select list is None (SELECT *). A union
# branch needs an explicit column list, so it is named here — in the table's own
# ordinal order, which is also the Bronze column order the backfill created.
STATUS_BLOCK_COLUMNS = (
    "ID", "RunningStatus", "Start_TS", "StopType", "End_TS",
    "Faults", "StartID", "EndID", "StopTypeNote", "Notes",
)

# Slot list, in the order the union emits it. The type is needed only for the
# `CAST(NULL AS ...)` placeholder a branch emits for a slot it does not carry —
# the real type always comes from whichever branch does carry it. Captured from
# INFORMATION_SCHEMA on db_ProcessData, 2026-08-26.
UNION_SLOTS = (
    ("src",              "varchar(32)"),
    ("block_id",         "int"),
    ("t_stamp",          "datetime"),
    ("row_id",           "int"),
    ("ctr_name",         "nvarchar(255)"),
    ("ctr_val",          "bigint"),
    ("sb_ID",            "bigint"),
    ("sb_RunningStatus", "bit"),
    ("sb_Start_TS",      "datetime"),
    ("sb_StopType",      "nvarchar(8)"),
    ("sb_End_TS",        "datetime"),
    ("sb_Faults",        "nvarchar(1024)"),
    ("sb_StartID",       "bigint"),
    ("sb_EndID",         "bigint"),
    ("sb_StopTypeNote",  "nvarchar(64)"),
    ("sb_Notes",         "nvarchar(128)"),
)


def union_slot_map(key, cols):
    """[(bronze_column, union_slot)] for one bronze_plan() entry, in Bronze order.

    Read left-to-right this is how the Copy activity projects a source into the
    union; read right-to-left it is how bronze_merge.py reconstructs the Bronze
    table. Both directions come from this one function so they cannot drift.

    A plan column with no slot is an error rather than a silent drop: adding a
    column to bronze_plan() must also give it somewhere to travel, and finding
    that out when the pipeline is generated is much cheaper than finding it out
    as a missing Bronze column three runs later.
    """
    if key == "status_blocks":
        return [(c, "sb_" + c) for c in STATUS_BLOCK_COLUMNS]

    ts = get_col(CFG, "t_stamp")
    counter_total = get_col(CFG, "counter_total")
    slots = {s for s, _ in UNION_SLOTS}
    out = []
    for col in cols:
        if col == ts:
            slot = "t_stamp"
        elif col in ("block_id", "row_id"):
            slot = col
        elif col.endswith("_Counter_Names"):
            slot = "ctr_name"
        elif col.endswith("_Counters") or col == counter_total:
            slot = "ctr_val"
        else:
            raise RuntimeError(
                f"bronze_plan() column {key}.{col} has no union slot. Add one to "
                f"UNION_SLOTS and a rule to union_slot_map(), or the one-copy "
                f"stage would drop it."
            )
        assert slot in slots, f"union slot {slot!r} missing from UNION_SLOTS"
        out.append((col, slot))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30,
                    help="lookback window; must exceed SPC_LOOKBACK_DAYS used at compute time")
    ap.add_argument("--out", default=os.path.join(
        os.environ.get("TEMP", "/tmp"), "claude", "c--hollister-dashboard", "bronze"))
    args = ap.parse_args()

    from deltalake import write_deltalake

    os.makedirs(args.out, exist_ok=True)
    conn = get_connection()
    print(f"Exporting last {args.days} days -> {args.out}\n")

    for key, tbl, ts_col, cols, like in bronze_plan():
        select = ", ".join(cols) if cols else "*"
        where = [f"{ts_col} >= DATEADD(day, -?, (SELECT MAX({ts_col}) FROM dbo.{tbl}))"]
        params = [args.days]
        if like:
            name_col, pats = like
            where.append("(" + " OR ".join(f"{name_col} LIKE ?" for _ in pats) + ")")
            params.extend(pats)
        sql = f"SELECT {select} FROM dbo.{tbl} WHERE " + " AND ".join(where)

        df = query(conn, sql, params=params)
        # pyodbc hands back Decimal for numeric/bigint columns; Delta needs a
        # concrete arrow type, and the compute path treats these as ints anyway.
        for c in df.columns:
            if df[c].dtype == object and not df[c].empty:
                sample = df[c].dropna()
                if not sample.empty and hasattr(sample.iloc[0], "as_integer_ratio"):
                    df[c] = pd.to_numeric(df[c], errors="ignore")
        write_deltalake(os.path.join(args.out, tbl), df, mode="overwrite",
                        schema_mode="overwrite")
        print(f"  {tbl:48s} {len(df):>10,} rows   watermark={ts_col}"
              + (f"   filtered on {like[0]}" if like else ""))

    conn.close()
    print(f"\nDone. Point the duckdb backend at it:\n"
          f'  export DB_BACKEND=duckdb DELTA_TABLES_PATH="{args.out}"')


if __name__ == "__main__":
    main()
