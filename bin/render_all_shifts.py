#!/usr/bin/env python3
"""
Accelerated shift dashboard orchestrator.

Speedups over the original subprocess-per-shift pattern in render_all_shifts.sh:

  1. In-process threading — no Python startup / import cost per shift (~1-2 s × 14 saved).
  2. Pre-computed shared data — SPC subgroups (3 full-history scans → 1), global P95
     (14 overlapping queries → 1), and cluster profiles (14×14 queries → ~27).
  3. Incremental mode (--incremental) — skips shifts whose per-shift JSON is already
     newer than the latest completed shift in the DB.

Usage (from project root):
    source .env && python3 bin/render_all_shifts.py [ngp2|hu3] [--incremental]

Requires MSSQL_SA_PASSWORD set in environment (source .env first).
"""

import argparse
import glob
import json
import os
import sys
import threading
from datetime import datetime, timezone

# ── path setup ────────────────────────────────────────────────────────────────
_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, _SRC_DIR)

# ── load .env if password not already in environment ──────────────────────────
if "MSSQL_SA_PASSWORD" not in os.environ:
    env_file = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '.env'))
    try:
        with open(env_file) as _ef:
            for _ln in _ef:
                if _ln.startswith("MSSQL_SA_PASSWORD="):
                    os.environ["MSSQL_SA_PASSWORD"] = _ln.strip().split("=", 1)[1]
                    break
    except FileNotFoundError:
        pass


# ── pre-compute shared data ───────────────────────────────────────────────────

def _precompute_shared(line, conn):
    """Compute data that is identical across all 14 target shifts.

    Returns a dict consumed by run_shift_in_process (in pipelines/shift_analysis.py).
    """
    from metrics.oee import build_anomaly_baseline, compute_global_p95, detect_latest_shift
    from metrics.spc import collect_spc_subgroups
    from core.lines import get_line_config, get_spc_panels, has_table
    from domain.clusters import configure_segments
    from domain.schedule import load_production_schedule
    from domain.shifts import spc_baseline_cutoff

    CFG          = get_line_config()
    SHIFT_BOUNDS = CFG["shift_boundaries"]
    ANOMALY_N    = 13

    # 1. Detect true latest shift (once, shared)
    true_date, true_type, true_start, true_end = detect_latest_shift(conn)

    # 2. SPC baseline cutoff — start of the oldest shift in the 7-day window
    cutoff = spc_baseline_cutoff(true_date, true_type, SHIFT_BOUNDS)
    print(f"  SPC baseline cutoff: {cutoff}", flush=True)

    # 3. Excluded days
    non_prod, _, sched_warnings = load_production_schedule(CFG["line_id"])
    excluded = set(CFG.get("excluded_days", [])) | set(non_prod)
    for w in sched_warnings:
        print(f"  [schedule warning] {w}", flush=True)
    if excluded:
        print(f"  Excluded days: {sorted(excluded)}", flush=True)

    # 4. SPC subgroups — full historical scan once per panel
    configure_segments(CFG["segments"])
    spc_subgroups = {}
    for panel in get_spc_panels(CFG):
        if has_table(CFG, panel["gate_table"]):
            subs = collect_spc_subgroups(conn, CFG, panel, excluded)
            spc_subgroups[panel["key"]] = subs
            print(f"  SPC '{panel['title']}': {len(subs)} subgroups", flush=True)

    # 5. Target shifts from weekly JSON + shared pre-window anomaly baseline
    weekly_path = f"output/analyses/{line}/weekly-dashboard.json"
    with open(weekly_path) as f:
        weekly = json.load(f)
    targets = [(s["shift_date"], s["shift_type"])
               for s in reversed(weekly["shifts"])
               if not s.get("skipped")]

    oldest_target = targets[0]  # reversed → oldest first
    profile_cache, pre_window_baseline = build_anomaly_baseline(
        conn, targets, oldest_target, SHIFT_BOUNDS, n_baseline=ANOMALY_N, excluded=excluded
    )
    print(f"  Cluster profiles cached: {len(profile_cache)} shifts", flush=True)
    print(f"  Pre-window baseline: {len(pre_window_baseline)} shifts", flush=True)

    # 6. Global P95 across all profiled shifts
    p95_ppm = compute_global_p95(conn, list(profile_cache.keys()))
    print(f"  P95 ideal PPM ({len(profile_cache)} shifts): {p95_ppm:.1f}", flush=True)

    return {
        "true_date":             true_date,
        "true_type":             true_type,
        "true_start":            true_start,
        "true_end":              true_end,
        "spc_baseline_cutoff":   cutoff,
        "excluded":              excluded,
        "p95_ppm":               p95_ppm,
        "spc_subgroups":         spc_subgroups,
        "cluster_profile_cache": profile_cache,
        "pre_window_baseline":   pre_window_baseline,
    }


# ── per-shift thread ──────────────────────────────────────────────────────────

def _run_shift_chain(shift_date, shift_type, shared, line, results, lock, render=True):
    """Pipeline (+ optional render) for one shift, called inside a daemon thread.

    render=False writes only the analysis JSON — used by the Power BI bridge so a
    refresh needs neither plotly nor jinja2 (and skips the HTML render cost)."""
    from core.db import get_connection
    from core.storage import save_analysis_json
    from core.lines import get_line_config
    from pipelines.shift_analysis import run_shift_in_process

    CFG = get_line_config()
    conn = get_connection(database=CFG.get("database"))
    try:
        data = run_shift_in_process(shift_date, shift_type, conn, shared)

        shifts_dir     = f"output/analyses/{line}/shifts"
        os.makedirs(shifts_dir, exist_ok=True)
        per_shift_path = os.path.join(shifts_dir, f"shift-{shift_date}-{shift_type}.json")
        save_analysis_json(data, per_shift_path)
        print(f"  Wrote {per_shift_path}", flush=True)

        if render:
            from rendering.shift_report import render_shift_from_data
            render_shift_from_data(data)

        with lock:
            results[(shift_date, shift_type)] = {"ok": True}
    except Exception as exc:
        with lock:
            results[(shift_date, shift_type)] = {"ok": False, "error": str(exc)}
    finally:
        conn.close()


# ── weekly rollup (rendering step — HTML from JSON) ──────────────────

def _build_weekly_rollups(line):
    shifts_dir = f"output/analyses/{line}/shifts"
    weeks_dir  = f"output/analyses/{line}/shift-weeks"
    os.makedirs(weeks_dir, exist_ok=True)

    by_week: dict = {}
    for path in sorted(glob.glob(f"{shifts_dir}/shift-*.json")):
        with open(path) as f:
            s = json.load(f)
        shift_entry = s["shifts"][0]
        sd = shift_entry["shift_date"]
        dt = datetime.strptime(sd, "%Y-%m-%d")
        iso_year, iso_week, _ = dt.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        by_week.setdefault(key, []).append(shift_entry)

    for key, week_shifts in sorted(by_week.items()):
        week_shifts.sort(key=lambda s: (s["shift_date"], 0 if s["shift_type"] == "day" else 1))
        dates  = [datetime.strptime(s["shift_date"], "%Y-%m-%d") for s in week_shifts]
        weekly = {
            "analysis_id": "shift-week",
            "line_id":     line,
            "week":        key,
            "week_start":  min(dates).strftime("%Y-%m-%d"),
            "week_end":    max(dates).strftime("%Y-%m-%d"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "shifts":      week_shifts,
        }
        out_path = f"{weeks_dir}/{key}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(weekly, f, indent=2)
        print(f"  Wrote {out_path} ({len(week_shifts)} shifts)", flush=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Regenerate shift dashboards for a line.")
    parser.add_argument("line", nargs="?", default="ngp2", choices=["ngp2", "hu3"])
    parser.add_argument("--incremental", action="store_true",
                        help="Skip shifts whose per-shift JSON is newer than the DB latest")
    parser.add_argument("--no-render", action="store_true",
                        help="Compute + write per-shift analysis JSON only (skip HTML render)")
    args = parser.parse_args()
    line = args.line
    render = not args.no_render

    # Propagate line to env so get_line_config() in sub-modules resolves correctly
    # (they check LINE env var; the positional arg is invisible to them).
    os.environ["LINE"] = line

    print(f"Regenerating shift dashboards for line: {line}", flush=True)

    weekly_path = f"output/analyses/{line}/weekly-dashboard.json"
    with open(weekly_path) as f:
        weekly = json.load(f)
    all_targets = [(s["shift_date"], s["shift_type"])
                   for s in reversed(weekly["shifts"])
                   if not s.get("skipped")]

    # ── pre-compute shared data ───────────────────────────────────────────────
    print("\nPre-computing shared data...", flush=True)
    from core.db import get_connection
    from core.lines import get_line_config
    CFG         = get_line_config()
    shared_conn = get_connection(database=CFG.get("database"))
    try:
        shared = _precompute_shared(line, shared_conn)
    finally:
        shared_conn.close()

    # ── incremental filter ────────────────────────────────────────────────────
    if args.incremental:
        true_end_ts = shared["true_end"].timestamp() if hasattr(shared["true_end"], "timestamp") else float(str(shared["true_end"]))
        to_process = []
        skipped    = []
        for sd, st in all_targets:
            json_path = f"output/analyses/{line}/shifts/shift-{sd}-{st}.json"
            if os.path.exists(json_path) and os.path.getmtime(json_path) >= true_end_ts:
                skipped.append((sd, st))
            else:
                to_process.append((sd, st))
        print(f"\nIncremental: {len(skipped)} up-to-date, {len(to_process)} to reprocess", flush=True)
    else:
        to_process = all_targets

    # ── parallel pipeline + render ────────────────────────────────────────────
    print(f"\n{len(to_process)} shifts — running pipeline + render chains in parallel", flush=True)
    results: dict = {}
    lock    = threading.Lock()
    threads = [
        threading.Thread(
            target=_run_shift_chain,
            args=(sd, st, shared, line, results, lock, render),
            daemon=True,
        )
        for sd, st in to_process
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ── report ────────────────────────────────────────────────────────────────
    failed = [(sd, st) for (sd, st), r in results.items() if not r["ok"]]
    if failed:
        for sd, st in sorted(failed):
            print(f"  FAILED {sd} {st}: {results[(sd, st)]['error']}", flush=True)
    else:
        print(f"  All {len(to_process)} shifts completed successfully", flush=True)

    # ── weekly rollups ────────────────────────────────────────────────────────
    print("\nBuilding weekly rollups...", flush=True)
    _build_weekly_rollups(line)

    print(f"\nDone — {len(to_process)} shift dashboards regenerated for {line}.")


if __name__ == "__main__":
    main()
