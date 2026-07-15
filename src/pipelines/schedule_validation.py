#!/usr/bin/env python3
"""
Production-Schedule Validator
Detects days where the line was effectively NOT scheduled to produce, so they
can be excluded from the OEE denominator. Without this step, calendar-time
based availability gets crushed by commissioning, weekends, or shutdown days
that should never have been counted as "should be running" time.

Two classifications:
  - non_production: run_hours < MIN_RUN_HOURS AND parts < MIN_PARTS
                    -> exclude entirely from OEE denominator
  - partial:        data span < FULL_DAY_HOURS but has real production
                    -> scale denominator to actual data-coverage span

Output: output/validation/production-schedule-<line>.json

Usage:
    MSSQL_SA_PASSWORD=<pw> python3 src/pipelines/schedule_validation.py [--line ngp2|hu3]
"""

import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from core.db import get_connection
from core.lines import get_line_config, get_table
import json

CFG = get_line_config()

# Per-line tunable thresholds. Override via lines.py "schedule_thresholds":
#   {"min_run_hours": float, "min_parts": int, "full_day_hours": float}
_DEFAULT_THRESHOLDS = {"min_run_hours": 2.0, "min_parts": 5000, "full_day_hours": 22.0}
_THRESHOLDS = {**_DEFAULT_THRESHOLDS, **CFG.get("schedule_thresholds", {})}
MIN_RUN_HOURS = _THRESHOLDS["min_run_hours"]
MIN_PARTS = _THRESHOLDS["min_parts"]
FULL_DAY_HOURS = _THRESHOLDS["full_day_hours"]

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "output", "validation")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, f"production-schedule-{CFG['line_id']}.json")


def query_daily_run_hours(conn, time_from, time_to):
    tbl = get_table(CFG, "status_blocks")
    sql = f"""
    SELECT
        CAST(Start_TS AS DATE) AS day,
        SUM(CASE WHEN RunningStatus IN ('True', '1', 1)
                 THEN DATEDIFF(SECOND, Start_TS, End_TS) ELSE 0 END) / 3600.0 AS run_hours
    FROM {tbl}
    WHERE Start_TS >= ? AND Start_TS < ? AND End_TS IS NOT NULL
    GROUP BY CAST(Start_TS AS DATE)
    """
    df = pd.read_sql(sql, conn, params=[time_from, time_to])
    df["day"] = df["day"].astype(str)
    return df


def query_daily_output(conn, time_from, time_to):
    tbl = get_table(CFG, "output_stats")
    sql = f"""
    SELECT
        CAST(t_stamp AS DATE) AS day,
        MAX(Counter_Total) - MIN(Counter_Total) AS parts_produced,
        MIN(t_stamp) AS first_ts,
        MAX(t_stamp) AS last_ts,
        COUNT(*) AS n_samples
    FROM {tbl}
    WHERE t_stamp >= ? AND t_stamp < ?
    GROUP BY CAST(t_stamp AS DATE)
    """
    df = pd.read_sql(sql, conn, params=[time_from, time_to])
    df["day"] = df["day"].astype(str)
    df["first_ts"] = pd.to_datetime(df["first_ts"])
    df["last_ts"] = pd.to_datetime(df["last_ts"])
    df["coverage_hours"] = (df["last_ts"] - df["first_ts"]).dt.total_seconds() / 3600.0
    return df


def build_schedule(run_df, out_df, time_from, time_to):
    all_days = pd.date_range(time_from, time_to, freq="D", inclusive="left").strftime("%Y-%m-%d")
    schedule = pd.DataFrame({"day": all_days})
    schedule = schedule.merge(run_df, on="day", how="left").merge(
        out_df[["day", "parts_produced", "coverage_hours", "first_ts", "last_ts"]],
        on="day", how="left",
    )
    schedule["run_hours"] = schedule["run_hours"].fillna(0.0)
    schedule["parts_produced"] = schedule["parts_produced"].fillna(0)
    schedule["coverage_hours"] = schedule["coverage_hours"].fillna(0.0)

    def classify(row):
        if row["run_hours"] < MIN_RUN_HOURS and row["parts_produced"] < MIN_PARTS:
            return "non_production"
        if row["coverage_hours"] < FULL_DAY_HOURS:
            return "partial"
        return "full"

    schedule["classification"] = schedule.apply(classify, axis=1)
    schedule["effective_hours"] = schedule.apply(
        lambda r: 0.0 if r["classification"] == "non_production"
        else (round(r["coverage_hours"], 2) if r["classification"] == "partial" else 24.0),
        axis=1,
    )
    return schedule


def _resolve_time_range(args_from, args_to):
    """Resolve analysis window with priority: CLI args > session.json > line_config default.

    Returns (time_from, time_to) as YYYY-MM-DD strings.
    Raises SystemExit if no source provides a valid range.
    """
    if args_from and args_to:
        return args_from, args_to

    # Try session.json (byod-hollister pipeline; not present in client-only deployments)
    try:
        from domain.schedule import load_time_range
        tf, tt, _ = load_time_range(line_id=CFG["line_id"])
        if tf and tt:
            return tf, tt
    except Exception:
        pass

    # Fall back to line_config.default_time_range
    dr = CFG.get("default_time_range")
    if dr and dr[0] and dr[1]:
        print(f"[validator] No session window found — using line_config default_time_range: {dr[0]} to {dr[1]}")
        return dr[0], dr[1]

    print(
        "ERROR: No time range available. Provide --from YYYY-MM-DD --to YYYY-MM-DD, "
        "or set default_time_range in lines.py.",
        file=sys.stderr,
    )
    sys.exit(1)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--line", help="Line ID (overrides LINE env var)")
    parser.add_argument("--from", dest="time_from", metavar="YYYY-MM-DD")
    parser.add_argument("--to",   dest="time_to",   metavar="YYYY-MM-DD")
    args, _ = parser.parse_known_args()

    time_from, time_to = _resolve_time_range(args.time_from, args.time_to)

    print("=" * 60)
    print(f"Production Schedule Validator — {CFG['label']}")
    print(f"Window: {time_from} to {time_to}")
    print("=" * 60)

    conn = get_connection(database=CFG.get("database"))
    run_df = query_daily_run_hours(conn, time_from, time_to)
    out_df = query_daily_output(conn, time_from, time_to)
    conn.close()

    schedule = build_schedule(run_df, out_df, time_from, time_to)

    total_days = len(schedule)
    non_prod = schedule[schedule["classification"] == "non_production"]
    partial = schedule[schedule["classification"] == "partial"]
    full = schedule[schedule["classification"] == "full"]

    calendar_hours = total_days * 24.0
    effective_hours = float(schedule["effective_hours"].sum())

    print(f"\n  Total calendar days:    {total_days}")
    print(f"  Full production days:   {len(full)}")
    print(f"  Partial-coverage days:  {len(partial)}")
    print(f"  Non-production days:    {len(non_prod)}")
    print(f"\n  Calendar hours:         {calendar_hours:.1f}h")
    print(f"  Effective hours:        {effective_hours:.1f}h")
    print(f"  Excluded hours:         {calendar_hours - effective_hours:.1f}h "
          f"({(1 - effective_hours/calendar_hours)*100:.1f}% of calendar)")

    if len(non_prod) > 0:
        print(f"\n  Non-production days:")
        for _, r in non_prod.iterrows():
            print(f"    {r['day']}: run={r['run_hours']:.2f}h, parts={int(r['parts_produced'])}, "
                  f"coverage={r['coverage_hours']:.1f}h")

    if len(partial) > 0:
        print(f"\n  Partial-coverage days (denominator scaled):")
        for _, r in partial.iterrows():
            print(f"    {r['day']}: run={r['run_hours']:.2f}h, parts={int(r['parts_produced'])}, "
                  f"coverage={r['coverage_hours']:.1f}h -> denom {r['effective_hours']:.1f}h")

    output = {
        "validator_id": "production-schedule",
        "line_id": CFG["line_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "time_range": {"from": time_from, "to": time_to},
        "thresholds": {
            "min_run_hours": MIN_RUN_HOURS,
            "min_parts": MIN_PARTS,
            "full_day_hours": FULL_DAY_HOURS,
        },
        "summary": {
            "total_days": int(total_days),
            "full_days": int(len(full)),
            "partial_days": int(len(partial)),
            "non_production_days": int(len(non_prod)),
            "calendar_hours": round(calendar_hours, 2),
            "effective_hours": round(effective_hours, 2),
            "excluded_hours": round(calendar_hours - effective_hours, 2),
            "exclusion_pct": round((1 - effective_hours / calendar_hours) * 100, 1),
        },
        "non_production_days": non_prod["day"].tolist(),
        "partial_days": partial["day"].tolist(),
        "per_day": [
            {
                "day": r["day"],
                "classification": r["classification"],
                "run_hours": round(r["run_hours"], 2),
                "parts_produced": int(r["parts_produced"]),
                "coverage_hours": round(r["coverage_hours"], 2),
                "effective_hours": round(r["effective_hours"], 2),
            }
            for _, r in schedule.iterrows()
        ],
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    rel_path = os.path.relpath(OUTPUT_PATH, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    print(f"\nOutput written to {rel_path}")


if __name__ == "__main__":
    main()
