# NGP2 SPC Dashboard — Fabric-native refresh

## Status (2026-08-13)

**Two ingestion routes exist. The Copy-activity one does not need the gateway
update; the Dataflow Gen2 one does.**

| Route | Gateway floor | Installed `3000.286.14` | State |
|---|---|---|---|
| **Pipeline Copy activity** ★ | `3000.214.2` | ✓ clears it | Deployed to the tenant; **never executed** |
| Dataflow Gen2 (CI/CD) | `3000.290` | ✗ one release short | Built; blocked on `UnsupportedGatewayVersion` |

The backfill pipeline's definition was accepted by the API and read back intact
(5 Copy activities, correct connection and Lakehouse GUIDs, staging off), so the
JSON shape is no longer an open question. What remains untested is whether Copy
activity actually reaches the historian through gateway `3000.286.14` — that is
what the first run answers, and nothing downstream can be trusted until it does.

Both land the **same Bronze table names in the same Lakehouse**, so the notebook,
Gold tables, Direct Lake model and 15-minute schedule are shared. Choosing one
does not discard the other — once the gateway is updated, Gen2 becomes a working
alternative rather than a requirement, and Copy activity remains preferable on
cost (see [`OPTIONS.md`](OPTIONS.md): ~4.6x cheaper in CU, no staging engines).

Remaining blocker for either route is Fabric capacity, not code. See
[`EVIDENCE_gateway_blocker.md`](EVIDENCE_gateway_blocker.md) for the gateway
escalation.

Discovered against the live tenant:

| Item | Value |
|---|---|
| Workspace | Smart Factory — `daff049b-5e21-4d61-8cf2-465032703de5` |
| Capacity | `b66dc66a-d4a2-4451-8ef4-22241fdb9689` (assigned; not readable by this account, so SKU unconfirmed — it is *not* the PPU one) |
| SQL gateway connection | `081b83d6-70db-41bc-ae0f-787ac7e67403` — `10.62.27.4;db_ProcessData`, standard gateway, **usable** |
| Lakehouse | `NGP2SPCLakehouse` — `49675f59-eded-4e77-bbb9-75f9a1fecf97` (created 2026-08-13, flat/non-schema) |
| Backfill pipeline | `NGP2 SPC Bronze Backfill` — `5b049bf0-1626-4e3c-8d10-b113de9b33f0` |
| Bronze dataflow (Gen2 route) | `NGP2 SPC Bronze` — `0f7c96be-16f5-4dee-a0fe-d78435a1a49a` |
| Published report / model | `621a428c-360a-4315-8ccb-422f4d922c26` / `1c5363ab-60d3-4a82-a673-4f1e6f7d00d8` |
| Gateway | `HOLL_PBI_GATEWAY (Primary)` — `23abfba2-f963-4c8d-ac82-acfbbf7e33e2`, version `3000.286.14` |
| Python visuals in the service | confirmed working (the 3 SPC charts are `pythonVisual`) |

**Do not use `smart_factory_lh`.** It is another team's medallion project
(`nb_load_bronze`, `nb_silver_build`, …) *and* it is a **schemas-enabled**
Lakehouse, which stores tables at `Tables/<schema>/<table>` rather than
`Tables/<table>`. The flat layout is assumed by `_discover_delta_tables()` in
[`src/core/db.py`](../src/core/db.py), by [`bronze_merge.py`](bronze_merge.py) and
by the Direct Lake conversion, and schema enablement cannot be turned off after
creation. `deploy_bronze.py --lakehouse-only` creates a flat one; the tables
endpoint returning `400 UnsupportedOperationForSchemasEnabledLakehouse` is how to
tell them apart.

## Why this exists

The NPG2 SPC Dashboard semantic model got its data through Power BI's
`Python.Execute` connector. That works in Desktop but cannot be scheduled: the
enterprise gateway rejects "Python script" as a data source type (IT policy, not
a bug).

Fabric notebooks cannot substitute directly — **notebooks have no gateway support
at all**, for Spark or Python. The only notebook route to an on-prem source is
Managed Private Endpoints, a separate network-engineering project with the same
IT friction that blocked Python in the first place.

Two Fabric items *can* reach on-prem SQL over a gateway — a Dataflow Gen2 and a
Data Pipeline's Copy activity. So the pipeline is split at that seam:

```
  Copy activity ──(gateway)──> stage_* ─┐        Bronze Delta tables
   (or Dataflow Gen2 ──(gateway)────────┴──────> in the Lakehouse
                                                │
                                          (no gateway)
                                                ▼
                          Python notebook: merges the staged tail into Bronze,
                          then DuckDB runs the real SPC pipeline unchanged and
                          writes gold_* Delta tables
                                                │
                                                ▼
                          Direct Lake semantic model (Fabric-to-Fabric read)
                                                │
                          Data Pipeline: extract → notebook, every 15 minutes
```

No Python ever runs on the gateway, and no gateway is involved past the extract.
That is why the IT ask is now a plain SQL Server connection rather than a
security exception.

### What the Copy-activity route has to do for itself

Copy activity can only append or overwrite a whole table — it has no equivalent of
Gen2's incremental refresh, which handled watermarking, partition replacement *and*
retention from two settings. That is reproduced in
[`bronze_merge.py`](bronze_merge.py), and deliberately not by tracking a high-water
mark:

- Each run's Copy activity re-reads a fixed **6-hour tail** into `stage_<table>`
  with Overwrite, so it holds no state and a retry cannot duplicate anything.
- The notebook then replaces the same window in Bronze with the staged rows using a
  Delta `replaceWhere`, bounded by the stage's own earliest watermark — so the rows
  removed and the rows written cover exactly the same range.
- Retention is pruned to 80 days (`_SPC_LOOKBACK_DAYS` defaults to 75), checked
  against table metadata first so the usual run does no rewrite.

The tail is anchored on `MAX(watermark)` **in the source table**, not the clock,
matching [`export_bronze_local.py`](../bin/export_bronze_local.py). That sidesteps
whether the historian writes local or UTC time, and means a stalled historian
re-reads the same rows harmlessly rather than silently reading none.

The consequence to know: **the pipeline must run at least once every 6 hours** or a
gap opens. Recovery is re-running the backfill pipeline; nothing needs resetting.

`replaceWhere` is also why [`src/core/db.py`](../src/core/db.py) needed **no change
at all** for this route — Bronze at rest is byte-equivalent either way, so the
verified compute path is untouched.

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
| Copy-activity source T-SQL vs the parity-verified queries | character-identical apart from the window bound |
| `bronze_merge.py` — merge an already-present tail, twice | no row added, bounds preserved, zero duplicates |
| `bronze_merge.py` — 3h hole punched in Bronze's tail | fully healed by the next merge, zero duplicates |
| `bronze_merge.py` — no `stage_*` table present | clean skip, so the notebook still works behind Gen2 |
| SPC output from merged Bronze vs pristine export (duckdb both sides) | **identical**, zero differences |

**Not verified:** anything requiring the gateway or capacity — neither extract
route has landed a row in Fabric, no Direct Lake render, no scheduled run. The
Copy-activity JSON in particular has never been accepted by the API; the
`externalReferences.connection` and `LakehouseTableSink` shapes in
[`generate_pipeline.py`](deploy/generate_pipeline.py) are written from
documentation, not from a portal-authored reference, so expect to align them once
the first deploy returns an error. (Lesson from the Gen2 round: only
portal-authored items reach real gateway execution, so a portal-built Copy activity
is the reference to diff against if it misbehaves.)

## Run order

```bash
az login --allow-no-subscriptions --tenant db08e4ba-c0c6-4893-8bbe-8f3b86b87652

# Precondition check — read-only, safe to run any time
python fabric_jobs/deploy/deploy_pipeline.py --check-only

# Phase A — Lakehouse
python fabric_jobs/deploy/deploy_bronze.py --lakehouse-only

# Phase B — ship the code the notebook imports
python fabric_jobs/deploy/sync_code_to_lakehouse.py --workspace "Smart Factory"
#   then create a *Python* notebook "NGP2 SPC Gold Refresh" with the Lakehouse
#   attached, pasting the two cells from fabric_jobs/notebook_bootstrap.py

# Phase C — deploy both pipelines (creates nothing else, runs nothing)
python fabric_jobs/deploy/deploy_pipeline.py

# Phase C.1 — the make-or-break gateway test: does Copy activity work on 3000.286?
python fabric_jobs/deploy/deploy_pipeline.py --run "NGP2 SPC Bronze Backfill"
#   check the 5 Bronze tables have rows before going on

# Phase C.2 — prove one incremental run (stage -> merge -> Gold)
python fabric_jobs/deploy/deploy_pipeline.py --run "NGP2 SPC 15min"

# Phase D — repoint the semantic model (only after gold_* tables exist)
python fabric_jobs/deploy/convert_model_to_directlake.py \
    --workspace-id daff049b-5e21-4d61-8cf2-465032703de5 --lakehouse-id <guid>
#   reversible with --revert

# Phase E — schedule, only after two consecutive successful runs
python fabric_jobs/deploy/deploy_pipeline.py --schedule
```

### Alternative: the Dataflow Gen2 route

Unchanged and still deployable, once the gateway clears `3000.290`. Substitute for
Phase C/C.1/C.2:

```bash
python fabric_jobs/deploy/deploy_bronze.py --sql-connection "<display name>"
#   then, in the portal: enable incremental refresh on each of the 5 queries
#   (75 days stored / 2 days refreshed), and run the one-time backfill
```

The notebook needs no change — `bronze_merge` is a no-op when no `stage_*` tables
exist, and Gen2 writes Bronze directly.

## Why `fab.py` exists

Driving the Fabric API through `az rest` on Windows failed reproducibly, twice:
cmd.exe mangles quotes in `--query` JMESPath expressions, and az writes responses
through the cp1252 console codec, so non-ASCII display names come back as
undecodable bytes. [`deploy/fab.py`](deploy/fab.py) calls the API directly over
HTTPS and borrows az only for the bearer token.

`deploy_bronze.sh` was replaced by `deploy_bronze.py` for this reason, and
`deploy_pipeline.sh` by `deploy_pipeline.py`. The `.sh` is kept only because it is
the one place the *Gen2* dataflow gets chained to the notebook; it still has the
`az rest --query` defect, so if that route is ever taken, port it first.

### One more Windows gotcha: export `MSYS_NO_PATHCONV=1`

Calling `fab.py` from Git Bash with a leading-slash API path silently rewrites it
into a Windows path, producing a 404 against a URL like
`.../v1C:/Users/youngcz/AppData/Local/Programs/Git/workspaces/...`. MSYS path
conversion treats `/workspaces/...` as a filesystem path. The deploy scripts are
unaffected because they build paths internally, but any ad-hoc `fab.py get /...`
needs `export MSYS_NO_PATHCONV=1` first.

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
- **Incremental refresh is a manual portal step** *on the Gen2 route only*.
  Dataflow Gen2 stores that setting outside the definition parts, so it cannot be
  scripted. The Copy-activity route has no such setting and implements the
  behaviour itself in [`bronze_merge.py`](bronze_merge.py) — which is scripted,
  version-controlled and testable, and is the main reason to prefer it beyond cost.
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
