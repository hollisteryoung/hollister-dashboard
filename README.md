# Hollister Dashboard

Self-contained shift and weekly OEE dashboards for the NGP2 and HU3 production lines.

All generated HTML files are fully self-contained — no internet connection, no cloud service, no server needed to view them.

---

## Two ways to run

**Option A — no database access (view or re-render the bundled snapshot)**

The repository ships with a committed JSON snapshot of the last pipeline run. You can generate the HTML dashboards from it without any database connection — no `.env` or password needed, because nothing in the rendering step touches the DB.

```bash
python3 -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash / MINGW64)
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

python bin/render_snapshot.py ngp2   # or hu3
# use 'python3' instead of 'python' on macOS / Linux
```

Open `output/reports/ngp2/weekly-dashboard.html` in any browser.

**Option B — with database access (pull fresh data, then render)**

If you have access to the local SQL Server instance, you can sync the latest historian data and regenerate everything with a single command:

```bash
python3 -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash / MINGW64)
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

cp .env.example .env          # fill in MSSQL_SA_PASSWORD
source .env

python src/pipelines/refresh.py --line ngp2   # or hu3
# use 'python3' instead of 'python' on macOS / Linux
```

`refresh.py` is the only command you need. It runs everything in order automatically: queries the DB, recomputes all metrics, writes updated JSON, and renders fresh HTML for the weekly dashboard and every shift. Commit the updated JSON files afterwards to keep the snapshot current.

---

## How data flows

The pipeline runs in two stages. Only Stage 1 touches the database.

```
  python src/pipelines/refresh.py --line ngp2        ← one command runs everything below
  │
  ├─ Stage 1: data extraction (needs DB) ────────────────────────────────────
  │   weekly_analysis.py   → weekly OEE, SPC, downtime → output/analyses/
  │   shift_analysis.py    → per-shift OEE, stops       → output/analyses/
  │                                      │
  │                                      │  *.json
  │                                      ▼
  └─ Stage 2: rendering (no DB needed) ──────────────────────────────────────
      weekly_report.py     → output/reports/<line>/weekly-dashboard.html
      shift_report.py      → output/reports/<line>/shifts/*.html
```

`render_snapshot.py` runs Stage 2 only (reads committed JSON, no DB needed).

**One-off: production schedule detection**

Run this once per analysis window, before the first `refresh.py`, whenever the schedule changes:

```bash
python src/pipelines/schedule_validation.py --line ngp2
```

This auto-detects non-production days and writes `output/validation/production-schedule-<line>.json`. The weekly pipeline reads it to scale OEE denominators correctly.

All SQL joins and query logic live in `src/pipelines/`. Table and column names are configured per line in `src/core/lines.py`.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10 or higher |
| ODBC Driver for SQL Server | 17 or later — only needed for Option B (DB access) |
| SQL Server | accessible at `localhost,1433` (running in Docker) — only needed for Option B |

---

## Getting fresh data (Option B only)

The local SQL Server database is populated by running the sync scripts against the source historian.

**Preview what will be synced (no changes made):**

```bash
# NGP2
sqlcmd -S localhost,1433 -U sa -P $MSSQL_SA_PASSWORD -i sql/ngp2_sync_preview.sql

# HU3
sqlcmd -S localhost,1433 -U sa -P $MSSQL_SA_PASSWORD -i sql/hu3_sync_preview.sql
```

**Run the actual sync:**

```bash
# NGP2
sqlcmd -S localhost,1433 -U sa -P $MSSQL_SA_PASSWORD -i sql/ngp2_sync.sql

# HU3
sqlcmd -S localhost,1433 -U sa -P $MSSQL_SA_PASSWORD -i sql/hu3_sync.sql
```

The sync scripts update the `@cutoff` timestamp at the top of each file — set it to the date you want data through before running.

**First-time setup only — linked server:**

If this is a fresh SQL Server instance, run the linked server setup first so the sync scripts can reach the source historian:

```bash
sqlcmd -S localhost,1433 -U sa -P $MSSQL_SA_PASSWORD -i sql/ngp2_linked_server_setup.sql
```

---

## Incremental re-render (Option B only)

Skip shifts whose JSON is already newer than the latest DB record:

```bash
python3 bin/render_all_shifts.py ngp2 --incremental
```

---

## Output

| Dashboard | Path |
|---|---|
| Weekly (NGP2) | `output/reports/ngp2/weekly-dashboard.html` |
| Weekly (HU3) | `output/reports/hu3/weekly-dashboard.html` |
| Shift (NGP2) | `output/reports/ngp2/shifts/shift-dashboard-YYYY-MM-DD-day.html` |
| Shift (HU3) | `output/reports/hu3/shifts/shift-dashboard-YYYY-MM-DD-day.html` |

The weekly dashboard links to all shift dashboards.

---

## How tables are queried and combined

There is **one SQL JOIN** in the entire codebase, several **Python-side merges**, and one **text parse** that acts like a join. Understanding these three patterns covers all the data assembly.

### 1. Table and column names — `src/core/lines.py`

Start here before reading any query. Every logical name used in the pipeline (`"status_blocks"`, `"output_stats"`, `"parts_per_minute"`, …) is mapped to the physical DB table name, and every column alias to the actual column name. This is what allows the same query code to run unchanged on NGP2 and HU3.

```python
get_table(CFG, "status_blocks")   # → e.g. "NGP2_StatusBlocks"
get_col(CFG, "start_ts")          # → e.g. "Start_TS"
```

### 2. The one SQL JOIN — `src/metrics/oee.py`

`StatusBlocks` holds every production interval (running or stopped). `FOFchanges` holds the operator-entered reason codes for stops. They are linked by a block ID:

```sql
FROM StatusBlocks sb
LEFT JOIN FOFchanges fof ON sb.StartBlockID = fof.NDX
WHERE sb.Start_TS >= ? AND sb.Start_TS < ?
  AND sb.RunningStatus = 0   -- stopped intervals only
```

This query appears twice: in `_query_stop_blocks()` (downtime analysis) and `compute_shift_hourly_breakdown()` (hourly breakdown). LEFT JOIN is used because not every stop has an operator-entered reason code.

### 3. Python-side merges — `src/metrics/oee.py`

All other table combinations are done in Python after running separate queries, not in SQL. For example, `compute_shift_performance()` queries `PartsPerMinute` and `StatusBlocks` independently, then uses `pd.merge` to mask out throughput readings that fall inside stopped intervals. Keeping them separate avoids complex SQL and makes each query easy to read and test in isolation.

### 4. Text parsing as a soft join — `src/domain/faults.py`

The `Faults` column in `StatusBlocks` stores alarm context as a free-text string:

```
FC=@Fault_PLC1_0042@(Gripper no part) RC=1007
```

`faults.py` parses this with regex to extract the fault code (`FC`) and reason code (`RC`) — effectively resolving alarm labels without a foreign-key join to a separate lookup table. The parsed labels feed into `domain/clusters.py` for segment and cause-cluster classification.

---

## Line configuration

Each production line is configured as an entry in `src/core/lines.py`. The config declares:

- Database name and physical table names
- Column name mappings (historian-specific column aliases)
- Shift boundaries (default 07:00 / 19:00)
- Segment definitions and cause-cluster keywords for downtime categorisation
- SPC panel declarations (quality counter tables, lane patterns)
- Excluded non-production days

To add a new line, copy an existing entry (e.g. `hu3`) and update the values. Then run:

```bash
python3 src/pipelines/refresh.py --line <new_line_id>
```

---

## Troubleshooting

**Connection error:** Verify `MSSQL_SA_PASSWORD` is set (`echo $MSSQL_SA_PASSWORD`) and the SQL Server container is running (`docker ps`). The server defaults to `localhost,1433`; override with `export DB_SERVER=<host>,1433` if needed.

**"weekly-dashboard.json not found":** Run the weekly pipeline before the shift pipeline:
```bash
python3 src/pipelines/weekly_analysis.py --line ngp2
```

**Charts not rendering:** All Plotly JS is embedded inline — if charts are blank, open the browser console (F12) and check for JS errors. The file must be opened as a local file, not via a server that strips script tags.

**Stale data:** Re-run the sync SQL scripts to pull the latest records, then re-run `refresh.py`.
