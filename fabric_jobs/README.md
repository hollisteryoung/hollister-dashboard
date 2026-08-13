# NGP2 SPC Dashboard — Fabric-native refresh

## Status (2026-08-13) — LIVE

**The migration is complete and running.** The published `NPG2 SPC Dashboard`
semantic model reads Direct Lake from `NGP2SPCLakehouse`; the `Python.Execute`
source (`PBISource`) has been deleted from the live model, not just superseded.
The `NGP2 SPC 15min` pipeline is scheduled every 15 minutes.

| Step | State |
|---|---|
| Bronze backfill (Copy activity through the gateway) | ✅ ran, 3.9M rows, 79 days |
| 15-min incremental pipeline (Copy → merge → Gold) | ✅ completed end to end, scheduled |
| Gold table schemas | ✅ declared explicitly, stable at 0 rows and populated ([`spc_gold_refresh.py`](spc_gold_refresh.py) `GOLD_SCHEMAS`) |
| Semantic model → Direct Lake | ✅ **done directly against the live/published model** via TOM (not via Desktop publish — see below), verified with live DAX queries |
| Schedule | ✅ enabled, 15 min, GMT Standard Time |
| `gold_SPC` populated with real rows | ⏳ pending — needs 3 producing shifts in the 7-day window (`N_DAYS`); the 2026-08-04..08-11 outage left only 2. Should clear once Day 2026-08-13 completes at 19:00 |
| Fabric-side execution identity | ⚠️ open — pipeline/notebook run as a personal AAD account; a service principal / workspace identity should own them for production stability (raised with IT separately; unrelated to the SQL login, which is already a proper service account) |

**Two ingestion routes exist. Copy activity is live; Dataflow Gen2 remains a
deployable, unused alternative** that would only become relevant if the gateway
is updated past `3000.290` (installed: `3000.286.14`). Both land the same Bronze
table names in the same Lakehouse, so nothing downstream depends on which one
extracts. See [`OPTIONS.md`](OPTIONS.md) for the cost comparison — the ~4.6x
figure is ingestion-only; end-to-end the notebook now dominates run time (~7–8
of each ~15 min), so it is the better target if capacity ever becomes tight.

### The Direct Lake conversion was NOT a Desktop publish

The first attempt (via `convert_model_to_directlake.py` + Desktop's PBIP
"set up remote model" flow) failed with
`PFE_TM_SWITCH_PARTITION_FROM_NONDIRECTLAKE_TO_DIRECTLAKE_NOT_ALLOWED` — a hard
Analysis Services restriction: you cannot alter an **existing** table's storage
mode from Import to Direct Lake in place, only create it fresh as Direct Lake.
The failure aborted cleanly; nothing was left partially applied (confirmed via
XMLA — all 4 tables still read `storageMode: Import` afterward).

The fix was to connect directly to the **live, published** semantic model over
XMLA (`ConnectFabric`) and do the migration Microsoft's restriction actually
requires: capture every column/measure/relationship definition, **delete** the
4 tables (cascades measures/relationships), **recreate** them as `directLake`
entities against `gold_*`, then restore the measures and relationships from the
captured definitions. `PBISource` was deleted afterward since nothing
referenced it. Verified with live `TOPN`/`COUNTROWS`/measure DAX queries — not
just that the TMDL parses, but that real rows come back correctly shaped.

One quirk worth knowing: immediately after `Create`, `COUNTROWS` on a table
returned `null` instead of a number until `RefreshWithXMLA(Full)` was run to
force Direct Lake to *frame* (load) the underlying Parquet data — table
metadata existing is not the same as data being loaded.

The local PBIP's TMDL files match what's live (same conversion script's output),
so opening the project in Desktop should sync without conflict now.

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

## Run order (historical — all phases below are complete; kept as the reference
## for rebuilding this from scratch, e.g. on a different line)

```bash
az login --allow-no-subscriptions --tenant db08e4ba-c0c6-4893-8bbe-8f3b86b87652

# Precondition check — read-only, safe to run any time
python fabric_jobs/deploy/deploy_pipeline.py --check-only

# Phase A — Lakehouse
python fabric_jobs/deploy/deploy_bronze.py --lakehouse-only

# Phase B — ship the code the notebook imports
python fabric_jobs/deploy/sync_code_to_lakehouse.py --workspace "Smart Factory"
#   then create a *Python* notebook "NGP2 SPC Gold Refresh" with the Lakehouse
#   attached, pasting the cell from fabric_jobs/notebook_bootstrap.py

# Phase C — deploy both pipelines (creates nothing else, runs nothing)
python fabric_jobs/deploy/deploy_pipeline.py

# Phase C.1 — the make-or-break gateway test: does Copy activity work on 3000.286?
python fabric_jobs/deploy/deploy_pipeline.py --run "NGP2 SPC Bronze Backfill"
#   check the 5 Bronze tables have rows before going on

# Phase C.2 — prove one incremental run (stage -> merge -> Gold)
python fabric_jobs/deploy/deploy_pipeline.py --run "NGP2 SPC 15min"

# Phase D — repoint the semantic model. convert_model_to_directlake.py only
# rewrites the LOCAL TMDL files — publishing that through Desktop against an
# EXISTING model fails with PFE_TM_SWITCH_PARTITION_FROM_NONDIRECTLAKE_TO_DIRECTLAKE_NOT_ALLOWED,
# a hard AS restriction (cannot alter storage mode on an existing table, only
# create fresh). What actually worked: connect to the *live* model over XMLA
# (powerbi-modeling-mcp ConnectFabric), capture every column/measure/relationship,
# DELETE the 4 tables (cascades), recreate them as directLake entities against
# gold_*, restore measures/relationships from the capture, RefreshWithXMLA(Full)
# to force Direct Lake to frame the data (COUNTROWS returns null on a table
# that exists but has never been framed), then delete PBISource. No script for
# this yet — it was done interactively; a script would need the same
# capture/delete/recreate/restore/frame sequence.
python fabric_jobs/deploy/convert_model_to_directlake.py \
    --workspace-id daff049b-5e21-4d61-8cf2-465032703de5 --lakehouse-id <guid>
#   (rewrites local TMDL only; reversible with --revert; does not by itself fix
#   an already-published model — see above)

# Phase E — schedule, only after a successful run
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

- **`gold_SPC` has 0 rows.** Not a bug — `compute_weekly_spc()` in
  [`weekly_analysis.py`](../src/pipelines/weekly_analysis.py) refuses to draw
  control limits with fewer than 3 producing shifts in the `N_DAYS` window,
  and the 2026-08-04..08-11 outage left only 2. Should clear once Day
  2026-08-13 completes at 19:00 and the next scheduled run picks it up. If it
  hasn't after that, something other than the shift count is wrong.
- **Fabric-side execution identity.** The pipeline and notebook are owned by a
  personal AAD account, so the schedule depends on that account staying valid.
  Raised with IT as a request for a service principal or workspace identity to
  own these items — unrelated to the SQL login (`ssu_DataViewer`), which is
  already `credentialType: Basic` with no user passthrough and is not the gap.
- **Duty cycle / overlap risk, accepted but not proven safe.** Each run has
  taken 7–8 minutes against the 15-minute schedule (~50%). Delta's optimistic
  concurrency should make an overlapping run fail rather than corrupt data if
  Fabric's schedule doesn't itself prevent overlap, but that specific behaviour
  hasn't been observed or verified.
- **The gateway update is no longer blocking anything**, since Copy activity
  clears its floor already. [`EVIDENCE_gateway_blocker.md`](EVIDENCE_gateway_blocker.md)
  has the original evidence if the update is still pursued to bring the gateway
  inside Microsoft's support window, and it would re-open the Dataflow Gen2
  route as a live alternative rather than a documented one.
- **Confirmed, not still open:** the Smart Factory capacity supports Direct
  Lake — proven empirically by the live `RefreshWithXMLA`/DAX verification
  above, which would not have worked on a Pro/PPU-only capacity. The
  `Sql.Database` server string match was confirmed by the successful backfill.
  The historian is live and advancing (extracted timestamps were minutes old
  at backfill time), so the earlier 5-day staleness concern is resolved.
- The interactive "rewind through snapshots" UI is still deferred. The Gold
  tables retain the 3-day history, so it remains buildable on a separate,
  unlocked page.
