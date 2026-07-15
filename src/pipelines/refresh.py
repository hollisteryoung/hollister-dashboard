#!/usr/bin/env python3
"""
refresh.py — One-command weekly + all-shifts refresh.

Steps:
  1. Query the DB and report the latest available timestamp per key table.
  2. Run the weekly dashboard pipeline (auto-detects the latest complete week).
  3. Render the weekly dashboard HTML.
  4. Run the shift pipeline + render for every non-skipped shift in the weekly JSON.

Usage (from project root):
    MSSQL_SA_PASSWORD=<pw> python3 src/pipelines/refresh.py [--line ngp2|hu3]
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
from core.db import get_connection, query
from core.lines import get_line_config, get_table, get_col, output_dir

CFG = get_line_config()


def check_latest_dates(conn):
    c_start = get_col(CFG, "start_ts")
    c_end   = get_col(CFG, "end_ts")
    c_ts    = get_col(CFG, "t_stamp")

    print(f"\n=== Latest data in [{CFG['database']}] for {CFG['label']} ===")

    # StatusBlocks — show last closed block and open-block count separately
    try:
        tbl = get_table(CFG, "status_blocks")
        df = query(conn, f"""
            SELECT
                MAX(CASE WHEN {c_end} IS NOT NULL THEN {c_end} END) AS last_closed,
                SUM(CASE WHEN {c_end} IS NULL THEN 1 ELSE 0 END)    AS open_blocks
            FROM {tbl}
        """)
        last_closed = df["last_closed"].iloc[0]
        open_cnt    = int(df["open_blocks"].iloc[0])
        suffix = f"  ({open_cnt} open block{'s' if open_cnt != 1 else ''} with NULL End_TS)" if open_cnt else ""
        print(f"  {'status_blocks (last closed)':<30} {last_closed}{suffix}")
    except Exception as exc:
        print(f"  {'status_blocks':<30} ERROR: {exc}")

    # DowntimeOverlays — last overlay whose Start_TS is not a future placeholder
    try:
        tbl = get_table(CFG, "downtime_overlays")
        df = query(conn, f"""
            SELECT
                MAX(CASE WHEN {c_start} <= GETDATE() THEN {c_start} END) AS last_real_start,
                COUNT(*) AS total
            FROM {tbl}
        """)
        last_start = df["last_real_start"].iloc[0]
        total      = int(df["total"].iloc[0])
        print(f"  {'downtime_overlays (last Start_TS)':<30} {last_start}  ({total} total overlays)")
    except Exception as exc:
        print(f"  {'downtime_overlays':<30} ERROR: {exc}")

    # Historian tables — straightforward MAX(t_stamp)
    for label, logical in [
        ("output_stats", "output_stats"),
        ("ppm",          "ppm"),
        ("fof_changes",  "fof_changes"),
    ]:
        try:
            tbl = get_table(CFG, logical)
            val = query(conn, f"SELECT MAX({c_ts}) AS v FROM {tbl}").iloc[0, 0]
            print(f"  {label:<30} {val}")
        except Exception as exc:
            print(f"  {label:<30} ERROR: {exc}")


def run(cmd):
    print(f"\n>>> {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    line = CFG["line_id"]

    # Step 1 — report latest DB state
    conn = get_connection(database=CFG["database"])
    check_latest_dates(conn)
    conn.close()

    # Step 2 — weekly pipeline (auto-detects window from DB)
    run(["python3", "src/pipelines/weekly_analysis.py", "--line", line])

    # Step 3 — render weekly HTML
    run(["python3", "src/rendering/weekly_report.py", "--line", line])

    # Step 4 — all shift dashboards via the canonical render script
    # render_all_shifts.py takes the line as a positional arg, not --line
    run(["python3", "bin/render_all_shifts.py", line])

    print(f"\nDone — weekly + all shift dashboards for {CFG['label']}.")
    print(f"  Weekly: output/reports/{line}/weekly-dashboard.html")
    print(f"  Shifts: output/reports/{line}/shifts/")


if __name__ == "__main__":
    main()
