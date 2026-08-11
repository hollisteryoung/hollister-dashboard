#!/usr/bin/env bash
# Phase D — chain the Bronze dataflow to the Gold notebook and schedule it every 15 min.
#
# Prerequisites (in order):
#   1. ./deploy_bronze.sh ran, incremental refresh configured, backfill succeeded
#   2. python sync_code_to_lakehouse.py ran
#   3. A **Python** notebook named "$NOTEBOOK_NAME" exists in the workspace with the
#      NGP2 SPC Lakehouse attached as its default lakehouse, containing the two cells
#      from fabric_jobs/notebook_bootstrap.py. Create it in the portal — the notebook
#      item format is fussy enough that hand-building it via API risks a
#      silently-malformed item, and this is a one-minute paste.
#
# Usage: ./deploy_pipeline.sh "Smart Factory"
#
# Not yet executed against the tenant. Run it supervised the first time and check
# the run history before trusting the schedule.
set -euo pipefail

WS_NAME="${1:?usage: deploy_pipeline.sh <workspace-name>}"
DATAFLOW_NAME="NGP2 SPC Bronze"
NOTEBOOK_NAME="${NOTEBOOK_NAME:-NGP2 SPC Gold Refresh}"
PIPELINE_NAME="${PIPELINE_NAME:-NGP2 SPC 15min}"
INTERVAL_MIN="${INTERVAL_MIN:-15}"
TIMEZONE="${TIMEZONE:-GMT Standard Time}"

API="https://api.fabric.microsoft.com/v1"
RESOURCE="https://api.fabric.microsoft.com"
WORK="$(mktemp -d)"

step() { printf '\n=== %s ===\n' "$1"; }
resolve() {  # $1 = collection path, $2 = displayName
  az rest --method get --resource "$RESOURCE" --url "$API/workspaces/$WS_ID/$1" \
    --query "value[?displayName=='$2'] | [0].id" -o tsv
}

step "Resolve items"
WS_ID=$(az rest --method get --resource "$RESOURCE" --url "$API/workspaces" \
  --query "value[?displayName=='$WS_NAME'] | [0].id" -o tsv)
[ -n "$WS_ID" ] && [ "$WS_ID" != "null" ] || { echo "workspace not found"; exit 1; }

DF_ID=$(resolve dataflows "$DATAFLOW_NAME")
NB_ID=$(resolve notebooks "$NOTEBOOK_NAME")
[ -n "$DF_ID" ] && [ "$DF_ID" != "null" ] || { echo "dataflow '$DATAFLOW_NAME' not found — run deploy_bronze.sh first"; exit 1; }
[ -n "$NB_ID" ] && [ "$NB_ID" != "null" ] || { echo "notebook '$NOTEBOOK_NAME' not found — see prerequisite 3 above"; exit 1; }
echo "  workspace $WS_ID"
echo "  dataflow  $DF_ID"
echo "  notebook  $NB_ID"

step "Build pipeline definition"
# The notebook depends on the dataflow having landed the new Bronze rows, so the
# dependency is Succeeded-only: a failed extract must not let the notebook publish
# Gold tables computed from stale data.
cat > "$WORK/pipeline-content.json" <<JSON
{
  "properties": {
    "activities": [
      {
        "name": "Refresh Bronze",
        "type": "RefreshDataflow",
        "dependsOn": [],
        "policy": { "timeout": "0.00:12:00", "retry": 1, "retryIntervalInSeconds": 60 },
        "typeProperties": {
          "dataflowId": "$DF_ID",
          "workspaceId": "$WS_ID",
          "notifyOption": "NoNotification"
        }
      },
      {
        "name": "Compute Gold",
        "type": "TridentNotebook",
        "dependsOn": [
          { "activity": "Refresh Bronze", "dependencyConditions": [ "Succeeded" ] }
        ],
        "policy": { "timeout": "0.00:12:00", "retry": 0 },
        "typeProperties": { "notebookId": "$NB_ID", "workspaceId": "$WS_ID" }
      }
    ]
  }
}
JSON

python - "$WORK" "$PIPELINE_NAME" <<'PY'
import base64, json, sys
work, name = sys.argv[1:3]
with open(f"{work}/pipeline-content.json", "rb") as f:
    payload = base64.b64encode(f.read()).decode()
body = {"displayName": name, "type": "DataPipeline",
        "definition": {"parts": [{"path": "pipeline-content.json",
                                  "payload": payload,
                                  "payloadType": "InlineBase64"}]}}
with open(f"{work}/body.json", "w", encoding="utf-8") as f:
    json.dump(body, f)
PY

step "Create or update pipeline: $PIPELINE_NAME"
PL_ID=$(resolve items "$PIPELINE_NAME")
if [ -n "$PL_ID" ] && [ "$PL_ID" != "null" ]; then
  az rest --method post --resource "$RESOURCE" \
    --url "$API/workspaces/$WS_ID/items/$PL_ID/updateDefinition" --body "@$WORK/body.json"
  echo "  updated $PL_ID"
else
  PL_ID=$(az rest --method post --resource "$RESOURCE" \
    --url "$API/workspaces/$WS_ID/items" --body "@$WORK/body.json" --query "id" -o tsv)
  echo "  created $PL_ID"
fi

step "Verify one manual run before scheduling"
# Scheduling something untested every 15 minutes just multiplies a failure; prove a
# single run first.
RUN=$(az rest --method post --resource "$RESOURCE" \
  --url "$API/workspaces/$WS_ID/items/$PL_ID/jobs/instances?jobType=Pipeline" \
  --headers "Content-Length=0" --query "id" -o tsv 2>/dev/null || true)
echo "  triggered run ${RUN:-<check portal>}"
echo "  poll: az rest --method get --resource $RESOURCE \\"
echo "    --url \"$API/workspaces/$WS_ID/items/$PL_ID/jobs/instances\" --query 'value[0].status'"

cat <<EOF

=== Enable the schedule once that run has succeeded ===
az rest --method post --resource "$RESOURCE" \\
  --url "$API/workspaces/$WS_ID/items/$PL_ID/jobs/Pipeline/schedules" \\
  --body '{"enabled":true,"configuration":{"type":"Cron","interval":$INTERVAL_MIN,"startDateTime":"$(date -u +%Y-%m-%dT%H:%M:%S)","endDateTime":"$(date -u -d '+2 years' +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%S)","localTimeZoneId":"$TIMEZONE"}}'

Then let it run twice consecutively and confirm the report shows fresh data both
times before calling this done.

  PL_ID=$PL_ID
EOF
