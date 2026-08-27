#!/usr/bin/env python3
"""Deploy the Copy-activity Bronze pipelines. Replaces deploy_pipeline.sh.

The bash version drove the API through `az rest --query`, which is broken on this
machine in two reproducible ways (cmd.exe mangles the JMESPath quotes; az emits
cp1252-encoded responses). This uses fab.py, which borrows az only for the token.
See the "Why fab.py exists" section of fabric_jobs/README.md.

Nothing here depends on the pending gateway update: Copy activity's gateway floor
is 3000.214.2 and HOLL_PBI_GATEWAY runs 3000.286.14. It also leaves the Dataflow
Gen2 route in place and untouched, so both remain available.

Ordered cheapest-first so a missing prerequisite fails before anything is created:

    --check-only    read-only; report which items resolve and which do not
    --backfill      deploy the one-time Bronze backfill pipeline only
    (default)       deploy both pipelines
    --run NAME      trigger one pipeline run (writes to the Lakehouse)
    --schedule      enable the 15-minute schedule

Deliberately does not run or schedule anything by default. Both write production
data into a shared workspace, and scheduling something unproven every 15 minutes
just multiplies a failure.

Usage:
    az login --allow-no-subscriptions --tenant db08e4ba-c0c6-4893-8bbe-8f3b86b87652
    python fabric_jobs/deploy/deploy_pipeline.py --check-only
    python fabric_jobs/deploy/deploy_pipeline.py --backfill
    python fabric_jobs/deploy/deploy_pipeline.py --run "NGP2 SPC Bronze Backfill"
"""

import argparse
import base64
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fab                                                      # noqa: E402
from deploy_bronze import find_sql_connection, resolve_workspace  # noqa: E402
from generate_pipeline import BACKFILL_NAME, PIPELINE_NAME       # noqa: E402

WORKSPACE = "Smart Factory"
LAKEHOUSE = "NGP2SPCLakehouse"
NOTEBOOK = "NGP2 SPC Gold Refresh"
# Reframed at the end of every run. Without that, Activator cannot see new
# data in a Direct Lake model at all and alerts never fire — see
# generate_pipeline._semantic_refresh_activity().
SEMANTIC_MODEL = "NPG2 SPC Dashboard"
BUILD = os.path.join(HERE, "build", "pipeline")

INTERVAL_MIN = 15
TIMEZONE = "GMT Standard Time"


def step(msg):
    print(f"\n=== {msg} ===")


def lro(res, tries=40, delay=3):
    """Resolve a 202 Long Running Operation to its result.

    Item creation returns 202 with a Location header rather than the created item,
    which is what made the earlier scripts appear to succeed while reading an empty
    body (see the getDefinition note in README).
    """
    if not isinstance(res, dict) or res.get("_status") != 202:
        return res
    loc = res.get("_location")
    if not loc:
        return res
    for _ in range(tries):
        time.sleep(delay)
        state = fab.get(loc)
        status = str(state.get("status", "")).lower()
        if status == "succeeded":
            try:
                return fab.get(loc.rstrip("/") + "/result")
            except RuntimeError:
                return state
        if status in ("failed", "cancelled"):
            sys.exit(f"operation {status}: {json.dumps(state)[:600]}")
    sys.exit("operation did not complete in time — check the portal")


def find_item(ws_id, name, item_type=None):
    for it in fab.get(f"/workspaces/{ws_id}/items").get("value", []):
        if it.get("displayName") == name and (not item_type or it.get("type") == item_type):
            return it
    return None


def generate(ws_id, lh_id, sql_conn_id, notebook_id, semantic_model_id=None):
    cmd = [sys.executable, os.path.join(HERE, "generate_pipeline.py"),
           "--workspace-id", ws_id, "--lakehouse-id", lh_id,
           "--lakehouse-name", LAKEHOUSE,
           "--sql-connection-id", sql_conn_id, "--out", BUILD]
    if notebook_id:
        cmd += ["--notebook-id", notebook_id]
    # Passed through rather than left to the generator's default: this function
    # regenerates the definitions immediately before deploying them, so anything it
    # omits is silently dropped from what goes live.
    if semantic_model_id:
        cmd += ["--semantic-model-id", semantic_model_id]
    subprocess.run(cmd, check=True)


def upsert(ws_id, name, definition_path):
    """Create or update a DataPipeline item from a generated pipeline-content.json."""
    with open(definition_path, "rb") as f:
        payload = base64.b64encode(f.read()).decode()
    definition = {"parts": [{"path": "pipeline-content.json",
                             "payload": payload,
                             "payloadType": "InlineBase64"}]}
    existing = find_item(ws_id, name, "DataPipeline")
    if existing:
        lro(fab.post(f"/workspaces/{ws_id}/items/{existing['id']}/updateDefinition",
                     {"definition": definition}))
        print(f"  updated  {name}  {existing['id']}")
        return existing["id"]
    created = lro(fab.post(f"/workspaces/{ws_id}/items",
                           {"displayName": name, "type": "DataPipeline",
                            "definition": definition}))
    pid = created.get("id") or (find_item(ws_id, name, "DataPipeline") or {}).get("id")
    print(f"  created  {name}  {pid}")
    return pid


def trigger(ws_id, pipeline_id):
    res = fab.post(f"/workspaces/{ws_id}/items/{pipeline_id}/jobs/instances?jobType=Pipeline")
    loc = res.get("_location") if isinstance(res, dict) else None
    print(f"  triggered — poll: python fabric_jobs/deploy/fab.py get "
          f"/workspaces/{ws_id}/items/{pipeline_id}/jobs/instances")
    return loc


def schedule(ws_id, pipeline_id, interval=INTERVAL_MIN, timezone=TIMEZONE):
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    body = {"enabled": True,
            "configuration": {"type": "Cron",
                              "interval": interval,
                              "startDateTime": now.isoformat(),
                              "endDateTime": (now + datetime.timedelta(days=730)).isoformat(),
                              "localTimeZoneId": timezone}}
    res = fab.post(f"/workspaces/{ws_id}/items/{pipeline_id}/jobs/Pipeline/schedules", body)
    print(f"  schedule enabled every {interval} min ({timezone})")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=WORKSPACE)
    ap.add_argument("--sql-connection", help="display name of the gateway SQL connection")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--backfill", action="store_true",
                    help="deploy only the backfill pipeline (no notebook needed)")
    ap.add_argument("--run", metavar="NAME", help="trigger a run of this pipeline")
    ap.add_argument("--schedule", action="store_true",
                    help=f"enable the {INTERVAL_MIN}-minute schedule on {PIPELINE_NAME!r}")
    args = ap.parse_args()

    step(f"Resolve workspace: {args.workspace}")
    ws_id = resolve_workspace(args.workspace)

    step("Resolve prerequisites")
    sql = find_sql_connection(args.sql_connection)
    lh = fab.find(fab.get(f"/workspaces/{ws_id}/lakehouses"), LAKEHOUSE)
    nb = find_item(ws_id, NOTEBOOK, "Notebook")
    sm = find_item(ws_id, SEMANTIC_MODEL, "SemanticModel")
    print(f"  lakehouse {LAKEHOUSE!r}: {lh['id'] if lh else 'NOT FOUND'}")
    print(f"  notebook  {NOTEBOOK!r}: {nb['id'] if nb else 'NOT FOUND'}")
    print(f"  model     {SEMANTIC_MODEL!r}: {sm['id'] if sm else 'NOT FOUND'}")

    if args.check_only:
        step("Check complete (nothing created)")
        ok = bool(sql and lh)
        print(f"  sql connection: {'yes' if sql else 'NO'}\n"
              f"  lakehouse:      {'yes' if lh else 'NO — run deploy_bronze.py --lakehouse-only'}\n"
              f"  notebook:       {'yes' if nb else 'no (backfill can still deploy)'}\n"
              f"  ready to deploy backfill: {'yes' if ok else 'NO'}")
        return 0 if ok else 2

    if not sql:
        sys.exit("no usable gateway SQL connection — see fabric_jobs/IT_REQUEST_gateway.md")
    if not lh:
        sys.exit("lakehouse missing — run: python fabric_jobs/deploy/deploy_bronze.py "
                 "--lakehouse-only")

    if args.run or args.schedule:
        name = args.run or PIPELINE_NAME
        item = find_item(ws_id, name, "DataPipeline")
        if not item:
            sys.exit(f"pipeline {name!r} not deployed yet")
        if args.run:
            step(f"Trigger: {name}")
            trigger(ws_id, item["id"])
        if args.schedule:
            step(f"Schedule: {name}")
            schedule(ws_id, item["id"])
        return 0

    want_incremental = not args.backfill
    if want_incremental and not nb:
        sys.exit(f"notebook {NOTEBOOK!r} not found — create it from "
                 f"fabric_jobs/notebook_bootstrap.py, or pass --backfill")

    step("Generate pipeline definitions")
    generate(ws_id, lh["id"], sql["id"], nb["id"] if want_incremental else None,
             sm["id"] if (want_incremental and sm) else None)

    step("Create or update pipelines")
    ids = {BACKFILL_NAME: upsert(ws_id, BACKFILL_NAME,
                                 os.path.join(BUILD, "backfill.json"))}
    if want_incremental:
        ids[PIPELINE_NAME] = upsert(ws_id, PIPELINE_NAME,
                                    os.path.join(BUILD, "incremental.json"))

    step("Next steps (nothing has run yet)")
    print(f"""  1. Backfill Bronze once — this is also the make-or-break gateway test:
       python fabric_jobs/deploy/deploy_pipeline.py --run "{BACKFILL_NAME}"

  2. Confirm it succeeded and the 5 Bronze tables have rows, then prove one
     incremental run before scheduling:
       python fabric_jobs/deploy/deploy_pipeline.py --run "{PIPELINE_NAME}"

  3. Only once that has succeeded twice:
       python fabric_jobs/deploy/deploy_pipeline.py --schedule

  WS_ID={ws_id}
  LH_ID={lh['id']}""")
    for name, pid in ids.items():
        print(f"  {name} = {pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
