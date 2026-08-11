# NGP2 SPC Dashboard — Fabric-native refresh

## Status (2026-08-10)

**Blocked on one IT dependency.** Everything is built and numerically verified;
the only thing missing is a SQL Server connection on a standard (enterprise)
gateway. See [`IT_REQUEST_gateway.md`](IT_REQUEST_gateway.md) — that is the
request to send, and it is a routine one.

Discovered against the live tenant:

| Item | Value |
|---|---|
| Workspace | Smart Factory — `daff049b-5e21-4d61-8cf2-465032703de5` |
| Capacity | `b66dc66a-d4a2-4451-8ef4-22241fdb9689` (assigned; not readable by this account, so SKU unconfirmed — it is *not* the PPU one) |
| Enterprise gateways visible | **0** |
| SQL Server connections visible | **0** (32 connections, all File / SharePoint / ODBC / Folder) |
| On-prem connections | all `OnPremisesGatewayPersonal` — personal mode, which Dataflow Gen2 cannot use |
| Python visuals in the service | confirmed working (the 3 SPC charts are `pythonVisual`) |

## Why this exists

The NPG2 SPC Dashboard semantic model got its data through Power BI's
`Python.Execute` connector. That works in Desktop but cannot be scheduled: the
enterprise gateway rejects "Python script" as a data source type (IT policy, not
a bug).

Fabric notebooks cannot substitute directly — **notebooks have no gateway support
at all**, for Spark or Python. The only notebook route to an on-prem source is
Managed Private Endpoints, a separate network-engineering project with the same
IT friction that blocked Python in the first place.

Dataflow Gen2, however, has full standard support for on-prem SQL Server over a
gateway. So the pipeline is split at that seam:

```
Dataflow Gen2  ──(enterprise gateway)──>  Bronze Delta tables in the Lakehouse
                                                │
                                          (no gateway)
                                                ▼
                          Python notebook: DuckDB reads Bronze, runs the real
                          SPC pipeline unchanged, writes gold_* Delta tables
                                                │
                                                ▼
                          Direct Lake semantic model (Fabric-to-Fabric read)
                                                │
                          Data Pipeline: dataflow → notebook, every 15 minutes
```

No Python ever runs on the gateway, and no gateway is involved past the extract.
That is why the IT ask is now a plain SQL Server connection rather than a
security exception.

## The pipeline code is reused, not reimplemented

The notebook calls the same `weekly_analysis.compute_spc_only()` and
`export_powerbi_csv.build_tables()` that produce the numbers today. The only
substitution is at the connection layer: `DB_BACKEND=duckdb` in
[`src/core/db.py`](../src/core/db.py) swaps pyodbc for DuckDB over Delta, and the
Bronze tables keep their source names (under a `dbo` schema in DuckDB) so query
text needs no rewriting. Two dialect differences are bridged by `top_clause()` /
`limit_clause()` — that is the entire SQL delta.

## What has been verified

| Check | Result |
|---|---|
| `bin/verify_duckdb_parity.py` — DuckDB vs live pyodbc, full SPC output deep-diff | **identical**, zero differences |
| DuckDB run time vs live SQL | 57.9s vs 174.8s (3x faster) |
| Bronze footprint vs full raw mirror | 1.59M rows vs 56M (35x smaller) |
| `LIKE` pattern folding (T-SQL `_` wildcard vs escaped Power Query form) | selects identical rows for all 4 patterns |
| Gold refresh end-to-end against local Delta | ran, and accumulated across two real 15-minute buckets |
| Snapshot retention prune / single `IsLatestSnapshot` / replay idempotency | all assertions pass |
| Gold Delta column names and types vs semantic model columns | all match; timestamps written as `timestamp`, not `timestamp_ntz` |
| Direct Lake TMDL conversion + `--revert` | applies and restores cleanly |
| Original Hollister Dashboard PBI unaffected | 16 tables, both lines, full 21-column Shifts |
| Fabric API reachable (`deploy_bronze.py --check-only`) | workspace resolves, preconditions reported |

**Not verified:** anything requiring the gateway — the dataflow, the end-to-end
15-minute run, and Direct Lake against real Bronze data.

## Run order

```bash
az login --allow-no-subscriptions --tenant db08e4ba-c0c6-4893-8bbe-8f3b86b87652

# Precondition check — read-only, safe to run any time
python fabric_jobs/deploy/deploy_bronze.py --check-only

# --- blocked here until IT provisions the SQL gateway connection ---

# Phase A — Lakehouse + Bronze mirror
python fabric_jobs/deploy/deploy_bronze.py --sql-connection "<display name>"
#   then, in the portal: enable incremental refresh on each of the 5 queries
#   (75 days stored / 2 days refreshed), and run the one-time backfill

# Phase B — ship the code the notebook imports
python fabric_jobs/deploy/sync_code_to_lakehouse.py --workspace "Smart Factory"
#   then create a *Python* notebook "NGP2 SPC Gold Refresh" with the Lakehouse
#   attached, pasting the two cells from fabric_jobs/notebook_bootstrap.py

# Phase C — repoint the semantic model (only after gold_* tables exist)
python fabric_jobs/deploy/convert_model_to_directlake.py \
    --workspace-id daff049b-5e21-4d61-8cf2-465032703de5 --lakehouse-id <guid>
#   reversible with --revert

# Phase D — orchestrate and schedule
./fabric_jobs/deploy/deploy_pipeline.sh "Smart Factory"
```

## Why `fab.py` exists

Driving the Fabric API through `az rest` on Windows failed reproducibly, twice:
cmd.exe mangles quotes in `--query` JMESPath expressions, and az writes responses
through the cp1252 console codec, so non-ASCII display names come back as
undecodable bytes. [`deploy/fab.py`](deploy/fab.py) calls the API directly over
HTTPS and borrows az only for the bearer token.

`deploy_bronze.sh` was replaced by `deploy_bronze.py` for this reason.
**`deploy_pipeline.sh` still uses the `az rest --query` pattern and will hit the
same defect** — it needs converting to `fab.py` before Phase D is run. It is left
as-is for now because Phase D is blocked behind Phase A anyway and any rewrite
would be untestable until then.

## Deliberate deviations from the original plan

- **Bronze is a narrowed projection, not a raw mirror.** Only the columns and
  counter-name patterns the SPC path reads are landed. This is what makes the
  initial gateway backfill tractable (1.59M rows instead of 56M) and it is the
  exact footprint the parity check verified. The trade-off is that Bronze is
  coupled to the SPC panel config — `bronze_plan()` in
  [`bin/export_bronze_local.py`](../bin/export_bronze_local.py) is the single
  source of truth shared by the local export and the Dataflow generator, so the
  two cannot drift.
- **`expressions.tmdl` is rewritten, not deleted.** The plan said delete it; that
  was wrong. Direct Lake still needs one shared named expression — the
  `AzureStorage.DataLake` connector pointing at the Lakehouse.
- **Incremental refresh is a manual portal step.** Dataflow Gen2 stores that
  setting outside the definition parts, so it cannot be scripted.
- **The notebook item is created by hand.** One paste of two cells, versus
  hand-building a format that fails silently when malformed.

## Open items

- **The gateway.** [`IT_REQUEST_gateway.md`](IT_REQUEST_gateway.md).
- The `Sql.Database` server string must match the gateway connection's
  registration exactly. `generate_dataflow.py` strips the ODBC `,1433` suffix and
  says so, but confirm it against whatever IT creates.
- Confirm the Smart Factory capacity is an F or P SKU once someone with capacity
  admin rights can check — Direct Lake does not work on Pro or PPU.
- The historian's latest data was **2026-08-05** (StatusBlocks only to 08-03) as
  of 2026-08-10 — 5 days stale. Unrelated to this migration, but a 15-minute
  cadence is pointless if the source itself has stopped advancing.
- The interactive "rewind through snapshots" UI is still deferred. The Gold
  tables retain the 3-day history, so it remains buildable on a separate,
  unlocked page.
