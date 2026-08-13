# NGP2 SPC Dashboard — Fabric refresh: project breakdown

Durable summary of the migration, current as of 2026-08-13. For live status and
run instructions see [`README.md`](README.md); this file is the "how we got
here and why" record.

## The problem

The NPG2 SPC Dashboard's semantic model got its data through Power BI's
`Python.Execute` connector against the on-prem Ignition historian
(`db_ProcessData` on `10.62.27.4`) over pyodbc. That works in Desktop but
cannot be scheduled — the enterprise gateway rejects "Python script" as a data
source type. IT policy, not a bug, and no gateway configuration fixes it.

Goal: refresh automatically every 15 minutes, producing numbers identical to
what the existing pipeline produces today.

## Constraints hit, in the order they were hit

| Constraint | Consequence |
|---|---|
| Gateway forbids Python data sources | Original design is a dead end |
| **Fabric notebooks cannot use gateways at all** (Spark or Python) | Hard platform limit — the only alternative is Managed Private Endpoints, a separate network project |
| Only Dataflow Gen2 and Pipeline Copy activity can use a gateway | Architecture must split at that seam |
| No local machine may stay on | Rules out local compute writing to OneDrive/SharePoint |
| No Azure subscriptions | No Storage Account, so no Gen2 staging to a Warehouse |
| Gateway is `3000.286.14` (Sept 2025) | Gen2 CI/CD needs `3000.290` → `UnsupportedGatewayVersion` |
| Refresh caps: Pro 8/day, PPU 48/day; 15 min needs 96/day | Import mode can't hit the target → Direct Lake required → Fabric capacity required |
| Shared workspace, shared capacity | Must not disturb other teams |

## Architecture (as built and running)

```
On-prem SQL Server (Ignition historian)
        |
        |  5x Copy activity  --(enterprise gateway)-->
        |  stateless 6-hour tail, Overwrite
        v
  stage_tbl_*  (5 Delta tables, NGP2SPCLakehouse)
        |
        |  notebook step 1: bronze_merge.py
        |  Delta replaceWhere over the tail window + retention prune
        v
  tbl_*  (5 Bronze Delta tables, 80-day rolling, ~3.9M rows)
        |
        |  notebook step 2: spc_gold_refresh.py
        |  DuckDB reads Bronze -> the real SPC pipeline, unmodified
        v
  gold_Lines / gold_Shifts / gold_SPC / gold_Snapshots
        |
        v
  Direct Lake semantic model (NPG2 SPC Dashboard, live/published)
        |
        v
  NPG2 SPC Dashboard report

Orchestration: Data Pipeline "NGP2 SPC 15min", 5 Copy -> 1 Notebook,
scheduled every 15 minutes (GMT Standard Time).
```

No Python runs on the gateway; no gateway is involved past the extract.

## Key decisions and why

**Copy activity over Dataflow Gen2.** Gateway floor `3000.214.2` vs `3000.290`
— only Copy activity clears the installed `3000.286.14`. Also cheaper (no
Mashup/staging engines) and sends the original T-SQL rather than a Power Query
translation of it. Gen2 is kept fully deployable and would become a live
alternative (not a requirement) if the gateway is ever updated — both routes
land identical Bronze table names in the same Lakehouse.

**Python notebook, not Spark.** ~10^4 rows output; Spark's 1-2 minute cold
start would eat a large slice of a 15-minute window for no benefit, and burns
roughly 46% more CU.

**DuckDB over Bronze, reusing the existing SQL, not a rewrite.** The
counter-reset detection and Laney p' maths in `src/metrics/spc.py` are
intricate and were already correct; the only dialect difference was `TOP` vs
`LIMIT`.

**`replaceWhere`, not append-and-dedupe or full rewrite.** Bronze is too big
(~3.9M rows) to rewrite every 15 minutes; append-only would accumulate
duplicates. Replacing exactly the tail window is idempotent, so the Copy
activity can stay completely stateless — see `fabric_jobs/bronze_merge.py`.

**Windows anchored on `MAX(watermark)` in the source table, not the clock.**
The historian's timestamps are local wall-clock time (BST), not UTC — this
also drove the timezone-normalisation fix in `src/core/db.py`. Anchoring on
the data itself means a stalled historian re-reads the same rows harmlessly.

**A dedicated Lakehouse (`NGP2SPCLakehouse`), not the existing
`smart_factory_lh`.** The latter is another team's medallion project and is
schemas-enabled (`Tables/<schema>/<table>`), which breaks the flat layout
every part of this pipeline assumes. Schema enablement cannot be disabled
after creation.

**Gold column types declared explicitly, not inferred.** An empty pandas
column gives Arrow a `null` type, which delta-rs writes as Delta
`"type":"void"` — not a valid Delta type, so the table becomes unreadable by
anything. `gold_SPC` legitimately has 0 rows whenever fewer than 3 shifts in
the window produced data, so this is normal operation, not an edge case.
`GOLD_SCHEMAS` in `spc_gold_refresh.py` fixes the schema regardless of row
count, verified byte-identical between a 0-row and 350-row run.

## What is verified

| Check | Result |
|---|---|
| DuckDB vs live pyodbc, full SPC deep-diff | identical, zero differences |
| Real Fabric Bronze vs local reference, clipped to a common window | identical, zero differences |
| Merge: tail already present, twice | no rows added, bounds preserved, 0 duplicates |
| Merge: 3h hole punched in Bronze | fully healed next run, 0 duplicates |
| Merge: no stage table | clean skip (so the notebook still works behind Gen2) |
| Gold schema, 0 rows vs 350 rows | byte-identical, no void columns either way |
| Copy activity through gateway `3000.286.14` | works — 3.9M rows in 2m35s |
| Full pipeline end to end in Fabric | completed, scheduled |
| Direct Lake live query (TOPN, COUNTROWS, measures) against `NPG2 SPC Dashboard` | correct data returned, matches Gold row counts |

## Bugs found, and the process failure behind most of them

My local verification tested the SQL and maths — always correct — but missed
an entire class of environment-only failures: filesystem semantics, library
versions, relative-path resolution, a Delta type edge case, and (separately) a
hard product restriction on migrating an existing model's storage mode. None
of these were reachable from a workstation; all four Fabric-only bugs below
were found only by running the pipeline in Fabric and reading the notebook's
own run log, which was consistently more diagnostic than the REST API.

1. **delta-rs cannot write through the Lakehouse mount.** It commits via an
   atomic rename; OneLake's FUSE mount rejects rename with EPERM. Fixed by
   routing every write through `abfss://` (`fabric_jobs/onelake.py`). Reads
   through the mount are fine; the Copy activity was never affected since it
   writes via OneLake's REST API.
2. **The snapshot written was not the snapshot read.** `weekly_analysis.py`
   writes snapshots via a CWD-relative path; `export_powerbi_csv.py` reads them
   via a code-relative absolute path. Same directory from the repo root;
   different in a notebook. A latent bug in pre-existing code, not something
   this migration introduced — just something it exposed.
3. **A regression introduced by the timezone fix itself.** Registering every
   Delta table with a `DESCRIBE` call forced eager schema resolution where
   views were previously lazy, so one unreadable table (a `void`-typed Gold
   table) took down the whole DuckDB connection. Fixed by making per-table
   registration fault-tolerant.
4. **Empty Gold tables were unreadable (`void` type).** See "Gold column types
   declared explicitly" above.
5. **Import → Direct Lake cannot be done as a Desktop publish onto an existing
   model.** `PFE_TM_SWITCH_PARTITION_FROM_NONDIRECTLAKE_TO_DIRECTLAKE_NOT_ALLOWED`
   — a hard Analysis Services restriction, not a bug in the conversion script.
   Fixed by connecting to the live model over XMLA and doing
   delete-then-recreate on the table objects directly (see README's Phase D
   note for the exact sequence), which is the Microsoft-documented pattern for
   this exact error.

Also found: `az` is `az.bat` on Windows (`WinError 2` if invoked as `az`); `az
rest --query` is broken by cmd.exe quote-mangling and cp1252 output encoding
(worked around by `fabric_jobs/deploy/fab.py`, which only borrows `az` for the
token); Git Bash rewrites a leading `/workspaces/...` API path into a Windows
path (`MSYS_NO_PATHCONV=1` fixes it); `getDefinition` on a Fabric item is a 202
long-running operation, not a synchronous response; and immediately after
creating a Direct Lake table via TOM, `COUNTROWS` returns `null` until
`RefreshWithXMLA(Full)` forces the table to *frame* — table metadata existing
is not the same as its data being loaded.

## Cost

Published Fabric meters: Gen2 Mashup 16 CU-hr/hr, Warehouse SQL 6, data
movement 1.5. The commonly quoted ~4.6x Copy-activity advantage is
**ingestion-only**. For this workload the Copy activities finish in seconds;
the notebook takes 7-8 of each ~15-minute run. So end-to-end the saving is
closer to 1.5-2.5x, and **the notebook, not the extract, is now the dominant
cost** — the place to optimise if capacity ever becomes tight (the 6-hour tail
is 24x redundant; session startup is paid 96 times a day).

## Open items

See the "Open items" section of [`README.md`](README.md) — kept there since it
changes faster than this document should.
