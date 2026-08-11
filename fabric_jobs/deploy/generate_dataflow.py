#!/usr/bin/env python3
"""Generate the Dataflow Gen2 definition that mirrors the SPC source tables to Bronze.

Dataflow Gen2 is the only Fabric item that can reach the on-prem Ignition SQL
Server through the existing enterprise gateway — Fabric notebooks have no gateway
support at all, which is why the pipeline is split (gateway-bound extract here,
gateway-free compute in fabric_jobs/spc_gold_refresh.py).

The table/column/filter footprint comes from bin/export_bronze_local.py's
bronze_plan(), so what this deploys is exactly what bin/verify_duckdb_parity.py
proved numerically equivalent to the live pyodbc pipeline.

Emits the three parts the Fabric items API expects (mashup.pq, queryMetadata.json,
.platform) into an output directory; fabric_jobs/deploy/deploy_bronze.sh uploads
them. Generation is offline and side-effect free, so the M can be reviewed and
diffed without touching the tenant.

Usage:
    python fabric_jobs/deploy/generate_dataflow.py \
        --workspace-id <guid> --lakehouse-id <guid> \
        --sql-connection-id <guid> --sql-cluster-id <guid> \
        --lakehouse-connection-id <guid> --lakehouse-cluster-id <guid> \
        --out fabric_jobs/deploy/build/bronze
"""

import argparse
import json
import os
import sys
import uuid

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from export_bronze_local import bronze_plan                  # noqa: E402
from core.lines import get_line_config                       # noqa: E402

DATAFLOW_NAME = "NGP2 SPC Bronze"

# Rolling window the DuckDB compute needs, mirroring SPC_LOOKBACK_DAYS in
# src/metrics/spc.py. Used only in the printed incremental-refresh instructions —
# Dataflow Gen2 stores that setting outside the definition parts (see the note in
# deploy_bronze.sh), so it cannot be scripted here.
STORE_DAYS = 75
REFRESH_DAYS = 2


def _m_string(s):
    return '"' + s.replace('"', '""') + '"'


def normalize_server(server):
    """Drop the default-port suffix pyodbc needs but the gateway connection omits.

    DB_SERVER is written for ODBC ("host,1433"). Sql.Database has to be handed the
    server string *exactly* as the gateway connection registered it, or the
    dataflow binds to nothing and refresh fails with a credentials error. Pass
    --server explicitly if the registration differs from the bare host.
    """
    host, _, port = server.partition(",")
    if port.strip() == "1433":
        return host.strip()
    return server.strip()


def _like_to_m(name_col, pattern):
    """Translate a T-SQL LIKE pattern into a foldable Power Query predicate.

    Only the two shapes the SPC config actually uses are handled; anything else
    raises rather than silently mirroring the wrong rows. Note T-SQL treats `_` as
    a single-character wildcard while the folded Power Query form escapes it —
    verified against the live server as selecting identical rows for every pattern
    here, because the underscores are all literal in practice.
    """
    col = f"[{name_col}]"
    if pattern.startswith("%") and pattern.endswith("%"):
        return f"Text.Contains({col}, {_m_string(pattern[1:-1])})"
    if pattern.endswith("%") and "%" not in pattern[:-1]:
        return f"Text.StartsWith({col}, {_m_string(pattern[:-1])})"
    raise ValueError(f"unsupported LIKE pattern for M translation: {pattern!r}")


def build_mashup(server, database, workspace_id, lakehouse_id):
    """The complete `section Section1;` document: one source + one destination query per table."""
    parts = ["section Section1;"]
    for _key, tbl, _ts, cols, like in bronze_plan():
        steps = [f"    Source = Sql.Database({_m_string(server)}, {_m_string(database)})",
                 f'    Navigation = Source{{[Schema = "dbo", Item = {_m_string(tbl)}]}}[Data]']
        last = "Navigation"
        if cols:
            collist = ", ".join(_m_string(c) for c in cols)
            steps.append(f"    Projected = Table.SelectColumns({last}, {{{collist}}})")
            last = "Projected"
        if like:
            name_col, pats = like
            pred = " or ".join(_like_to_m(name_col, p) for p in pats)
            steps.append(f"    Filtered = Table.SelectRows({last}, each {pred})")
            last = "Filtered"

        parts.append(f"""
[DataDestinations = {{[
  Definition = [Kind = "Reference", QueryName = {_m_string(tbl + '_DataDestination')}, IsNewTarget = true],
  Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}}]
shared {tbl} = let
{",\n".join(steps)}
in
    {last};

shared {tbl}_DataDestination = let
    Pattern = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Navigation_1 = Pattern{{[workspaceId = {_m_string(workspace_id)}]}}[Data],
    Navigation_2 = Navigation_1{{[lakehouseId = {_m_string(lakehouse_id)}]}}[Data],
    TableNavigation = Navigation_2{{[Id = {_m_string(tbl)}, ItemKind = "Table"]}}?[Data]?
in
    TableNavigation;
""".rstrip() + "\n")
    return "\n".join(parts)


def build_query_metadata(sql_conn, sql_cluster, lake_conn, lake_cluster):
    """queryMetadata.json — destination queries MUST carry loadEnabled: false.

    Both connections have to be listed: the source query needs the gateway-bound
    SQL connection and the destination query needs the Lakehouse connection, and a
    missing entry fails credential resolution at refresh time rather than at save.
    """
    queries = {}
    for _key, tbl, _ts, _cols, _like in bronze_plan():
        queries[tbl] = {"queryName": tbl, "queryId": str(uuid.uuid4()), "loadEnabled": True}
        dest = f"{tbl}_DataDestination"
        queries[dest] = {"queryName": dest, "queryId": str(uuid.uuid4()),
                         "isHidden": True, "loadEnabled": False}

    def composite(cluster, datasource):
        return json.dumps({"ClusterId": cluster, "DatasourceId": datasource})

    return {
        "formatVersion": "202502",
        "computeEngineSettings": {},
        "name": DATAFLOW_NAME,
        "allowNativeQueries": False,
        "queriesMetadata": queries,
        "connections": [
            {"connectionId": composite(sql_cluster, sql_conn), "kind": "SQL", "path": "SQL"},
            {"connectionId": composite(lake_cluster, lake_conn), "kind": "Lakehouse",
             "path": "Lakehouse"},
        ],
    }


def build_platform():
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/"
                   "platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Dataflow", "displayName": DATAFLOW_NAME},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }


def main():
    cfg = get_line_config("ngp2")
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace-id", required=True)
    ap.add_argument("--lakehouse-id", required=True)
    ap.add_argument("--sql-connection-id", required=True)
    ap.add_argument("--sql-cluster-id", required=True)
    ap.add_argument("--lakehouse-connection-id", required=True)
    ap.add_argument("--lakehouse-cluster-id", required=True)
    ap.add_argument("--server", default=os.environ.get("DB_SERVER", ""),
                    help="SQL Server host as the gateway connection registers it")
    ap.add_argument("--database", default=cfg.get("database"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "build", "bronze"))
    args = ap.parse_args()
    if not args.server:
        ap.error("--server is required (or set DB_SERVER)")

    os.makedirs(args.out, exist_ok=True)
    server = normalize_server(args.server)
    if server != args.server:
        print(f"note: using server {server!r} (from {args.server!r}) — this must match "
              f"the gateway connection's registered server exactly\n")
    mashup = build_mashup(server, args.database, args.workspace_id, args.lakehouse_id)
    qm = build_query_metadata(args.sql_connection_id, args.sql_cluster_id,
                              args.lakehouse_connection_id, args.lakehouse_cluster_id)

    with open(os.path.join(args.out, "mashup.pq"), "w", encoding="utf-8", newline="\n") as f:
        f.write(mashup)
    with open(os.path.join(args.out, "queryMetadata.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(qm, f, indent=2)
    with open(os.path.join(args.out, ".platform"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(build_platform(), f, indent=2)

    tables = [t for _k, t, _ts, _c, _l in bronze_plan()]
    print(f"Wrote 3 definition parts -> {args.out}")
    print(f"  {len(tables)} source queries + {len(tables)} destination queries\n")
    print("After deploying, enable incremental refresh on each query in the Dataflow "
          "Gen2 editor\n(it is stored outside the definition parts, so it cannot be "
          "scripted):\n")
    for _key, tbl, ts, _cols, _like in bronze_plan():
        print(f"  {tbl:48s} filter column: {ts}")
    print(f"\n  Extract data from the past:      {STORE_DAYS} days   "
          f"(covers SPC_LOOKBACK_DAYS)")
    print(f"  Only extract new data from last: {REFRESH_DAYS} days   "
          f"(bounds each 15-minute run)")


if __name__ == "__main__":
    main()
