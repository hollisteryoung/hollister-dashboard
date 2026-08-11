#!/usr/bin/env python3
"""Phase A — create the NGP2 SPC Lakehouse and the Bronze Dataflow Gen2.

Replaces the earlier bash version, which drove the API through `az rest`. That
turned out to be unusable on this machine for two reproducible reasons: cmd.exe
mangles quotes in `--query` JMESPath expressions, and az writes responses through
the cp1252 console codec, so any non-ASCII in a display name comes back as
undecodable bytes. This uses fab.py, which borrows az only for the bearer token.

Steps are idempotent and ordered cheapest-first, so a missing prerequisite fails
before anything is created:

    --check-only     read-only discovery; reports what is present and what is not
    --lakehouse-only create/reuse the Lakehouse, skip the dataflow
    (default)        everything, requires the SQL gateway connection to exist

Usage:
    az login --allow-no-subscriptions --tenant <tenant-id>
    python fabric_jobs/deploy/deploy_bronze.py --check-only
    python fabric_jobs/deploy/deploy_bronze.py --sql-connection "<display name>"
"""

import argparse
import base64
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fab                                                      # noqa: E402

WORKSPACE = "Smart Factory"
LAKEHOUSE = "NGP2SPCLakehouse"
DATAFLOW = "NGP2 SPC Bronze"
BUILD = os.path.join(HERE, "build", "bronze")


def step(msg):
    print(f"\n=== {msg} ===")


def resolve_workspace(name):
    ws = fab.find(fab.get("/workspaces"), name)
    if not ws:
        sys.exit(f"workspace {name!r} not found")
    print(f"  workspace  {ws['id']}  capacity={ws.get('capacityId')}")
    if not ws.get("capacityId"):
        sys.exit("workspace has no capacity — Direct Lake requires a Fabric capacity SKU")
    return ws["id"]


def find_sql_connection(name=None):
    """Locate the gateway-bound SQL Server connection, or explain what's missing.

    Dataflow Gen2 cannot use a personal-mode gateway, so a PersonalCloud or
    OnPremisesGatewayPersonal connection is reported as unusable rather than
    silently bound — it would fail later at refresh with an opaque credentials error.
    """
    conns = fab.get("/connections").get("value", [])
    sql = [c for c in conns
           if str((c.get("connectionDetails") or {}).get("type", "")).lower() == "sql"]
    if name:
        match = next((c for c in conns if c.get("displayName") == name), None)
        if not match:
            print(f"  no connection named {name!r}")
            return None
        sql = [match]
    usable = [c for c in sql if c.get("connectivityType") in ("OnPremisesGateway", "VirtualNetwork")]
    for c in sql:
        d = c.get("connectionDetails") or {}
        flag = "usable" if c in usable else f"UNUSABLE ({c.get('connectivityType')})"
        print(f"  sql connection {c.get('displayName')!r} path={d.get('path')} -> {flag}")
    if not sql:
        print(f"  no SQL Server connections visible ({len(conns)} connections of other types)")
    return usable[0] if usable else None


def find_lakehouse_connection(ws_id, lh_id):
    for c in fab.get("/connections").get("value", []):
        d = c.get("connectionDetails") or {}
        if str(d.get("type", "")).lower() == "lakehouse" and lh_id in str(d.get("path", "")):
            return c
    return None


def cluster_id(connection_id):
    """Resolve the PBI ClusterId that pairs with a connection GUID in the
    dataflow definition's composite connectionId."""
    d = fab.call("GET", "https://api.powerbi.com/v2.0/myorg/me/gatewayClusterDatasources",
                 resource=fab.PBI_RESOURCE)
    for v in d.get("value", []):
        if v.get("id") == connection_id:
            return v.get("clusterId")
    return None


def ensure_lakehouse(ws_id):
    lh = fab.find(fab.get(f"/workspaces/{ws_id}/lakehouses"), LAKEHOUSE)
    if lh:
        print(f"  reusing lakehouse {lh['id']}")
        return lh["id"]
    lh = fab.post(f"/workspaces/{ws_id}/lakehouses", {"displayName": LAKEHOUSE})
    print(f"  created lakehouse {lh.get('id')}")
    return lh.get("id")


def build_parts(ws_id, lh_id, sql_conn, sql_cluster, lh_conn, lh_cluster):
    subprocess.run([sys.executable, os.path.join(HERE, "generate_dataflow.py"),
                    "--workspace-id", ws_id, "--lakehouse-id", lh_id,
                    "--sql-connection-id", sql_conn, "--sql-cluster-id", sql_cluster,
                    "--lakehouse-connection-id", lh_conn, "--lakehouse-cluster-id", lh_cluster,
                    "--out", BUILD], check=True)
    parts = []
    for path in ("queryMetadata.json", "mashup.pq", ".platform"):
        with open(os.path.join(BUILD, path), "rb") as f:
            parts.append({"path": path,
                          "payload": base64.b64encode(f.read()).decode(),
                          "payloadType": "InlineBase64"})
    return parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=WORKSPACE)
    ap.add_argument("--sql-connection", help="display name of the gateway SQL Server connection")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--lakehouse-only", action="store_true")
    args = ap.parse_args()

    step(f"Resolve workspace: {args.workspace}")
    ws_id = resolve_workspace(args.workspace)

    step("Look for a usable SQL gateway connection")
    sql = find_sql_connection(args.sql_connection)
    if not sql:
        print("\n  Dataflow Gen2 needs a standard/VNet gateway connection to the historian.")
        print("  See fabric_jobs/IT_REQUEST_gateway.md for the exact request to send IT.")

    if args.check_only:
        step("Check complete (nothing created)")
        print(f"  workspace ready: yes\n  sql connection ready: {'yes' if sql else 'NO'}")
        return 0 if sql else 2

    step(f"Ensure lakehouse: {LAKEHOUSE}")
    lh_id = ensure_lakehouse(ws_id)

    if args.lakehouse_only or not sql:
        step("Stopping before the dataflow")
        print("  Lakehouse is ready; the dataflow needs the SQL gateway connection.")
        return 0 if args.lakehouse_only else 2

    step("Resolve connection cluster IDs")
    lh_conn = find_lakehouse_connection(ws_id, lh_id)
    if not lh_conn:
        sys.exit("no Lakehouse connection yet — open the Lakehouse in the portal once, then re-run")
    sql_cluster, lh_cluster = cluster_id(sql["id"]), cluster_id(lh_conn["id"])
    if not (sql_cluster and lh_cluster):
        sys.exit(f"could not resolve ClusterId (sql={sql_cluster}, lakehouse={lh_cluster})")
    print(f"  sql {sql['id']} / {sql_cluster}\n  lakehouse {lh_conn['id']} / {lh_cluster}")

    step("Generate definition and create dataflow")
    parts = build_parts(ws_id, lh_id, sql["id"], sql_cluster, lh_conn["id"], lh_cluster)
    body = {"displayName": DATAFLOW, "definition": {"parts": parts}}
    existing = fab.find(fab.get(f"/workspaces/{ws_id}/dataflows"), DATAFLOW)
    if existing:
        fab.post(f"/workspaces/{ws_id}/dataflows/{existing['id']}"
                 f"/updateDefinition?updateMetadata=true", body)
        df_id = existing["id"]
        print(f"  updated {df_id}")
    else:
        df_id = fab.post(f"/workspaces/{ws_id}/dataflows", body).get("id")
        print(f"  created {df_id}")

    step("Verify connection bindings survived the save")
    defn = fab.post(f"/workspaces/{ws_id}/dataflows/{df_id}/getDefinition")
    for p in defn.get("definition", {}).get("parts", []):
        if p["path"] == "queryMetadata.json":
            qm = json.loads(base64.b64decode(p["payload"]))
            print(json.dumps(qm.get("connections"), indent=2))

    print(f"\nNext: enable incremental refresh on each of the 5 queries in the portal\n"
          f"(75 days stored / 2 days refreshed), then run the one-time backfill.\n"
          f"  WS_ID={ws_id}\n  LH_ID={lh_id}\n  DF_ID={df_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
