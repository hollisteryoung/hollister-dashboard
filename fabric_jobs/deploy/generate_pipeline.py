#!/usr/bin/env python3
"""Generate the Fabric Data Pipeline definitions for the Copy-activity Bronze route.

Why Copy activity rather than the Dataflow Gen2 in generate_dataflow.py: Gen2 with
CI/CD requires gateway 3000.290 and HOLL_PBI_GATEWAY runs 3000.286.14, which is
what `UnsupportedGatewayVersion` was telling us. Copy activity's floor is
3000.214.2, comfortably below the installed version, so this route does not depend
on the pending gateway update landing. It is also markedly cheaper in CU (no
Mashup/SQL/staging engines) and sends the *original T-SQL* rather than the
LIKE-to-Power-Query translation `generate_dataflow._like_to_m()` has to do — so
what runs is literally what bin/verify_duckdb_parity.py verified.

Two pipelines are emitted, both from bronze_plan():

  NGP2 SPC Bronze Backfill   5 Copy activities, full retention window, Overwrite
                             straight into the Bronze tables. Run once, or any
                             time Bronze needs rebuilding from source.

  NGP2 SPC 15min             ONE Copy activity landing a recent tail of all five
                             sources into stage_union (Overwrite, therefore
                             stateless), then one notebook activity that merges
                             the tail into Bronze and recomputes Gold. Runs
                             hourly, despite the name.

                             It was five copies, one per table. Data movement
                             bills duration x intelligent-throughput-optimization
                             x 1.5 CU-hours, and a copy over the gateway costs
                             ~90s of that whatever it carries, so five copies of
                             ~4.5k rows between them paid the fixed cost five
                             times per run — measured at ~6x the notebook it
                             feeds. Pass --stage-mode per-table for the old shape;
                             bronze_merge.py accepts either, which is what makes
                             that a complete rollback.

Both source queries bound the window on `MAX(watermark)` *in the source table*
rather than on the clock, matching bin/export_bronze_local.py — the historian's
timestamps are not known to be UTC, and anchoring on the data avoids having to
find out.

The notebook is what makes the tail safe: see fabric_jobs/bronze_merge.py for why
a fixed re-read plus a Delta replaceWhere is used instead of a high-water mark.

Usage:
    python fabric_jobs/deploy/generate_pipeline.py \
        --workspace-id <guid> --lakehouse-id <guid> --lakehouse-name NGP2SPCLakehouse \
        --sql-connection-id <guid> --notebook-id <guid> \
        --out fabric_jobs/deploy/build/pipeline
"""

import argparse
import json
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from export_bronze_local import (                            # noqa: E402
    UNION_SLOTS, UNION_STAGE_TABLE, bronze_plan, union_slot_map,
)
from core.lines import get_line_config                       # noqa: E402

CFG = get_line_config("ngp2")

BACKFILL_NAME = "NGP2 SPC Bronze Backfill"
PIPELINE_NAME = "NGP2 SPC 15min"

# Must stay at or above _SPC_LOOKBACK_DAYS in src/metrics/spc.py (default 75) and
# in step with RETENTION_DAYS in fabric_jobs/bronze_merge.py, or the merge will
# prune away rows the backfill just landed.
BACKFILL_DAYS = 80

# How much recent data each 15-minute run re-reads. The pipeline must run at least
# this often or a gap opens; 6h against a 15-minute schedule is 24x redundant,
# which costs ~5k rows per run and buys tolerance for a few hours of outage.
TAIL_HOURS = 6

# "union" = one Copy activity for all five sources; "per-table" = the original
# five. See build_incremental() — per-table is kept as the rollback shape.
STAGE_MODE = "union"

# Bounded well under the 15-minute cadence so a stuck run cannot overlap the next.
COPY_TIMEOUT = "0.00:10:00"
NOTEBOOK_TIMEOUT = "0.00:10:00"
# A Direct Lake reframe is metadata work, not a data load, so this is
# generous rather than tight.
REFRESH_TIMEOUT = "0.00:20:00"

NOTEBOOK_ACTIVITY_NAME = "Merge Bronze and compute Gold"


def source_query(tbl, cols, like, window_expr):
    """The exact T-SQL the Copy activity sends. Mirrors export_bronze_local.main()."""
    select = ", ".join(cols) if cols else "*"
    where = [f"{window_expr.format(tbl=tbl)}"]
    if like:
        name_col, pats = like
        where.append("(" + " OR ".join(f"{name_col} LIKE '{p}'" for p in pats) + ")")
    return f"SELECT {select} FROM dbo.{tbl} WHERE " + " AND ".join(where)


def union_source_query(window_expr):
    """The single SELECT the one-copy stage sends: every bronze_plan() entry
    projected into UNION_SLOTS and tagged with its plan key in `src`.

    Each branch keeps its own window bound and LIKE filters, so what crosses the
    gateway is the same row set the five separate copies fetched — just in one
    result set, and so on one billed data-movement duration instead of five.
    fabric_jobs/bronze_merge.py splits it back apart on `src`.
    """
    branches = []
    for key, tbl, watermark, cols, like in bronze_plan():
        expr_for = {slot: col for col, slot in union_slot_map(key, cols)}
        # `src` first and always a literal; the rest either carry a real column or
        # a typed NULL placeholder. The type on the placeholder matters only when
        # no branch carries the slot at all, which union_slot_map() prevents, but
        # being explicit keeps the Copy activity's inferred sink schema stable
        # rather than dependent on branch order.
        select = [f"CAST('{key}' AS varchar(32)) AS src"]
        for slot, sqltype in UNION_SLOTS[1:]:
            col = expr_for.get(slot)
            select.append(f"{col} AS {slot}" if col
                          else f"CAST(NULL AS {sqltype}) AS {slot}")
        where = [window_expr.replace("{col}", watermark).format(tbl=tbl)]
        if like:
            name_col, pats = like
            where.append("(" + " OR ".join(f"{name_col} LIKE '{p}'" for p in pats) + ")")
        branches.append("SELECT " + ", ".join(select)
                        + f" FROM dbo.{tbl} WHERE " + " AND ".join(where))
    return "\nUNION ALL\n".join(branches)


def _sql_source(tbl, sql, connection_id):
    return {
        "type": "SqlServerSource",
        "sqlReaderQuery": sql,
        "queryTimeout": "0.00:30:00",
        "partitionOption": "None",
        "datasetSettings": {
            "type": "SqlServerTable",
            "typeProperties": {"schema": "dbo", "table": tbl},
            "externalReferences": {"connection": connection_id},
            "annotations": [],
            "schema": [],
        },
    }


def _lakehouse_sink(table, action, ws_id, lh_id, lh_name):
    return {
        "type": "LakehouseTableSink",
        "tableActionOption": action,
        "datasetSettings": {
            "type": "LakehouseTable",
            "typeProperties": {"table": table},
            "linkedService": {
                "name": lh_name,
                "properties": {
                    "type": "Lakehouse",
                    "typeProperties": {
                        "workspaceId": ws_id,
                        "artifactId": lh_id,
                        "rootFolder": "Tables",
                    },
                    "annotations": [],
                },
            },
            "annotations": [],
            "schema": [],
        },
    }


def _copy_activity(name, tbl, sql, sink_table, ws_id, lh_id, lh_name, sql_conn):
    return {
        "name": name,
        "type": "Copy",
        "dependsOn": [],
        "policy": {
            "timeout": COPY_TIMEOUT,
            "retry": 2,
            "retryIntervalInSeconds": 60,
            "secureInput": False,
            "secureOutput": False,
        },
        "typeProperties": {
            "source": _sql_source(tbl, sql, sql_conn),
            # Overwrite for both pipelines: Bronze is rebuilt wholesale by the
            # backfill, and the stage tables are meant to hold only the current
            # tail. Append is deliberately unused — it is what would make a retry
            # duplicate rows.
            "sink": _lakehouse_sink(sink_table, "Overwrite", ws_id, lh_id, lh_name),
            # Staging is only needed for Warehouse destinations; a Lakehouse sink
            # copies straight from the on-premises source, which is what lets this
            # work with no Azure Storage Account.
            "enableStaging": False,
            "translator": {
                "type": "TabularTranslator",
                "typeConversion": True,
                "typeConversionSettings": {
                    "allowDataTruncation": False,
                    "treatBooleanAsNumber": False,
                },
            },
        },
    }


def _semantic_refresh_activity(name, ws_id, model_id, depends_on):
    """Reframe the Direct Lake semantic model once Gold has been rewritten.

    Two reasons this is here rather than left to Direct Lake's automatic framing:

      * **Activator cannot see changes in a Direct Lake model until it is
        refreshed.** It keeps reporting the values it first framed however many
        times the underlying Delta tables change, so an alert on `Out of Control`
        never fires no matter how many bars breach the UCL. That is a documented
        Microsoft bug, not a rule misconfiguration, and an explicit refresh is the
        workaround. (Activator also only polls Power BI hourly, which matches this
        pipeline's cadence anyway.)
      * It removes the framing question from the report path entirely: by the time
        the pipeline reports success, the model has been told to reload.

    waitOnCompletion so a failed reframe fails the run visibly rather than leaving
    the report silently on stale rows. Retries because the Power BI refresh API
    errors outright if a refresh is already in flight — a manual trigger landing on
    top of the schedule would otherwise fail the whole pipeline for a reason that
    resolves itself in a minute.
    """
    return {
        "name": name,
        "type": "PBISemanticModelRefresh",
        "dependsOn": [{"activity": d, "dependencyConditions": ["Succeeded"]}
                      for d in depends_on],
        "policy": {"timeout": REFRESH_TIMEOUT, "retry": 2,
                   "retryIntervalInSeconds": 60,
                   "secureInput": False, "secureOutput": False},
        "typeProperties": {
            "method": "post",
            "waitOnCompletion": True,
            "commitMode": "Transactional",
            "operationType": "SemanticModelRefresh",
            "groupId": ws_id,
            "datasetId": model_id,
        },
    }


def build_backfill(ws_id, lh_id, lh_name, sql_conn, days=BACKFILL_DAYS):
    window = ("{col} >= DATEADD(day, -" + str(days)
              + ", (SELECT MAX({col}) FROM dbo.{tbl}))")
    activities = []
    for _key, tbl, watermark, cols, like in bronze_plan():
        sql = source_query(tbl, cols, like, window.replace("{col}", watermark))
        activities.append(_copy_activity(
            f"Backfill {tbl}", tbl, sql, tbl, ws_id, lh_id, lh_name, sql_conn))
    return {"properties": {"activities": activities}}


def build_incremental(ws_id, lh_id, lh_name, sql_conn, notebook_id,
                      tail_hours=TAIL_HOURS, stage_mode=STAGE_MODE,
                      semantic_model_id=None):
    """The scheduled pipeline: land the tail, then merge and recompute Gold.

    stage_mode "union" emits ONE Copy activity carrying all five sources (see
    export_bronze_local.union_slot_map); "per-table" emits the original five, one
    per Bronze table. Both land data the notebook can consume — bronze_merge.py
    prefers the union stage and falls back to the per-table ones — so this is a
    safe rollback switch: redeploy with per-table and nothing else changes.

    Union is the default because data movement bills per copy activity duration,
    and a gateway copy costs ~90s of it regardless of how few rows it carries.
    """
    window = ("{col} >= DATEADD(hour, -" + str(tail_hours)
              + ", (SELECT MAX({col}) FROM dbo.{tbl}))")
    activities, names = [], []

    if stage_mode == "union":
        name = "Stage all sources"
        names.append(name)
        # The dataset's table name is nominal — sqlReaderQuery supplies the real
        # statement, and this source spans five tables. Naming the largest of them
        # keeps the activity readable in the portal.
        activities.append(_copy_activity(
            name, CFG["tables"]["output_stats"], union_source_query(window),
            UNION_STAGE_TABLE, ws_id, lh_id, lh_name, sql_conn))
    else:
        for _key, tbl, watermark, cols, like in bronze_plan():
            sql = source_query(tbl, cols, like, window.replace("{col}", watermark))
            name = f"Stage {tbl}"
            names.append(name)
            activities.append(_copy_activity(
                name, tbl, sql, f"stage_{tbl}", ws_id, lh_id, lh_name, sql_conn))

    # Succeeded-only, on every copy: the notebook overwrites the Gold tables the
    # report reads, so a partial extract must not be allowed to publish SPC limits
    # computed from an incomplete Bronze tail.
    activities.append({
        "name": NOTEBOOK_ACTIVITY_NAME,
        "type": "TridentNotebook",
        "dependsOn": [{"activity": n, "dependencyConditions": ["Succeeded"]} for n in names],
        "policy": {"timeout": NOTEBOOK_TIMEOUT, "retry": 0,
                   "secureInput": False, "secureOutput": False},
        "typeProperties": {"notebookId": notebook_id, "workspaceId": ws_id},
    })

    # Only if a model id was supplied, so the generator still works for anyone who
    # has not got one to hand — and so this stays one flag to drop if the Activator
    # bug it works around is ever fixed.
    if semantic_model_id:
        activities.append(_semantic_refresh_activity(
            "Reframe semantic model", ws_id, semantic_model_id,
            [NOTEBOOK_ACTIVITY_NAME]))
    return {"properties": {"activities": activities}}


def main():
    get_line_config("ngp2")
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace-id", required=True)
    ap.add_argument("--lakehouse-id", required=True)
    ap.add_argument("--lakehouse-name", default="NGP2SPCLakehouse")
    ap.add_argument("--sql-connection-id", required=True)
    ap.add_argument("--notebook-id", help="omit to emit the backfill pipeline only")
    ap.add_argument("--backfill-days", type=int, default=BACKFILL_DAYS)
    ap.add_argument("--tail-hours", type=int, default=TAIL_HOURS)
    ap.add_argument("--semantic-model-id",
                    help="Direct Lake model to reframe after Gold is written. "
                         "Required for Activator alerts to see new data at all — "
                         "see _semantic_refresh_activity().")
    ap.add_argument("--stage-mode", choices=("union", "per-table"), default=STAGE_MODE,
                    help="union: one Copy activity for all five sources (cheap, "
                         "default). per-table: the original five, kept as the "
                         "rollback shape — bronze_merge.py handles either.")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "build", "pipeline"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    common = (args.workspace_id, args.lakehouse_id, args.lakehouse_name,
              args.sql_connection_id)

    written = []
    backfill = build_backfill(*common, days=args.backfill_days)
    path = os.path.join(args.out, "backfill.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(backfill, f, indent=2)
    written.append((BACKFILL_NAME, path, len(backfill["properties"]["activities"])))

    if args.notebook_id:
        inc = build_incremental(*common, notebook_id=args.notebook_id,
                                tail_hours=args.tail_hours,
                                stage_mode=args.stage_mode,
                                semantic_model_id=args.semantic_model_id)
        path = os.path.join(args.out, "incremental.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(inc, f, indent=2)
        written.append((PIPELINE_NAME, path, len(inc["properties"]["activities"])))

    print(f"Wrote {len(written)} pipeline definition(s) -> {args.out}\n")
    for name, path, n in written:
        print(f"  {name:28s} {n} activities   {os.path.basename(path)}")
    if not args.notebook_id:
        print("\n  --notebook-id not given, so the 15-minute pipeline was skipped.\n"
              "  Create the notebook first (fabric_jobs/notebook_bootstrap.py).")

    print(f"\nSource SQL ({args.tail_hours}h tail, stage mode {args.stage_mode}):\n")
    window = ("{col} >= DATEADD(hour, -" + str(args.tail_hours)
              + ", (SELECT MAX({col}) FROM dbo.{tbl}))")
    if args.stage_mode == "union":
        print(union_source_query(window))
    else:
        for _key, tbl, watermark, cols, like in bronze_plan():
            print("  " + source_query(tbl, cols, like, window.replace("{col}", watermark)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
