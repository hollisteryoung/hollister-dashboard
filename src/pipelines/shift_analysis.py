#!/usr/bin/env python3
"""
Shift Dashboard — Last-shift snapshot + 4-shift trend.

Queries StatusBlocks, OutputStats, PartsPerMinute, OmacState, and FOFchanges
to compute per-shift OEE (A/P/Q), downtime categories, segment breakdown,
hourly breakdown, and stop summaries for the last 5 completed shifts.

Produces output/analyses/<line>/shift-dashboard.json.

Usage:
    MSSQL_SA_PASSWORD=<pw> python3 src/pipelines/shift_analysis.py [--line ngp2|hu3]
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from core.db import get_connection
from core.lines import get_line_config, has_table, output_dir, get_spc_panels
from core.storage import save_analysis_json
from domain.clusters import configure_segments
from domain.schedule import apply_schedule
from domain.shifts import format_shift_label, previous_shifts, shift_window, spc_baseline_cutoff
from metrics.oee import (
    compute_global_p95,
    compute_shift_availability,
    compute_shift_cluster_profile,
    compute_shift_downtime_categories,
    compute_shift_hourly_breakdown,
    compute_shift_performance,
    compute_shift_quality,
    compute_shift_segment_downtime,
    compute_shift_stop_summary,
    compute_shift_top_stops,
    detect_latest_shift,
    detect_shift_anomalies,
)
from metrics.spc import (
    _compute_ewma,
    _laney_p_prime,
    build_shift_baseline,
    collect_spc_subgroups,
    derive_spc_lanes,
)

CFG = get_line_config()
SHIFT_BOUNDS = CFG["shift_boundaries"]
LABEL_THRESHOLD_MIN = 5
SPC_BASELINE_SUBGROUPS = 100   # Laney p' + EWMA baseline cap, all three SPC panels
ANOMALY_BASELINE_SHIFTS = 13   # prior shifts for cluster z-score baseline (~6.5 days)

OUTPUT_PATH = os.path.join(output_dir(CFG), "shift-dashboard.json")


# ──────────────────────────────────────────────
# Per-shift helpers (shared by main and run_shift_in_process)
# ──────────────────────────────────────────────

def _build_shift_entry(conn, s_date, s_type, s_start, s_end, p95_ppm) -> dict:
    """Compute core OEE metrics for one shift and return a base entry dict."""
    avail_pct, run_h, _  = compute_shift_availability(conn, s_start, s_end)
    perf_pct, _          = compute_shift_performance(conn, s_start, s_end, p95_ppm)
    qual_pct, good, total = compute_shift_quality(conn, s_start, s_end)
    oee = avail_pct / 100 * perf_pct / 100 * qual_pct / 100 * 100
    dt_cats = compute_shift_downtime_categories(conn, s_start, s_end)
    total_stops, dt_hours, longest = compute_shift_stop_summary(conn, s_start, s_end)
    return {
        "shift_date": s_date, "shift_type": s_type,
        "label": format_shift_label(s_type, s_date),
        "oee": round(oee, 1), "availability": round(avail_pct, 1),
        "performance": round(perf_pct, 1), "quality": round(qual_pct, 1),
        "running_hours": round(run_h, 2), "total_stops": total_stops,
        "total_downtime_hours": dt_hours, "longest_stop_min": longest,
        "good_count": good, "total_count": total,
        "downtime_categories": dt_cats,
    }


def _enrich_latest_shift(conn, entry, s_start, s_end):
    """Add segment_downtime, top_stops_by_cluster, and hourly_breakdown to the current-shift entry."""
    seg_dt = compute_shift_segment_downtime(conn, s_start, s_end)
    entry["segment_downtime"]     = seg_dt
    entry["top_stops_by_cluster"] = compute_shift_top_stops(conn, s_start, s_end)
    entry["hourly_breakdown"]     = compute_shift_hourly_breakdown(conn, s_start, s_end)
    return entry


def _attach_spc_panels(entry, conn, panel_list, cutoff, start, n_baseline, excluded, bl_cutoff,
                       preloaded=None):
    """Attach SPC results for each configured panel to a shift entry dict."""
    for panel in panel_list:
        if not has_table(CFG, panel["gate_table"]):
            continue
        spc_data = compute_shift_spc(
            conn, panel, cutoff, start, n_baseline, excluded,
            baseline_cutoff_ts=bl_cutoff,
            preloaded_subgroups=(preloaded or {}).get(panel["key"]),
        )
        if spc_data:
            entry[panel["key"]] = spc_data


def _format_full_shift_label(shift_type, shift_date, shift_bounds) -> str:
    """Format: 'Day Shift — YYYY-MM-DD (HH:MM – HH:MM)'."""
    day_start, night_start = shift_bounds
    time_range = (f"{day_start:02d}:00 – {night_start:02d}:00" if shift_type == "day"
                  else f"{night_start:02d}:00 – {day_start:02d}:00")
    return f"{'Day' if shift_type == 'day' else 'Night'} Shift — {shift_date} ({time_range})"


# ──────────────────────────────────────────────
# SPC (config-driven — see line_config.spc_panels)
# ──────────────────────────────────────────────

def compute_shift_spc(conn, panel, cutoff_ts, shift_start_ts, n_baseline, excluded_days,
                      baseline_cutoff_ts=None, preloaded_subgroups=None):
    """Shift-aggregate Laney p' + EWMA for one declarative SPC panel.

    baseline_cutoff_ts: upper bound for p_bar baseline (defaults to shift start).
    Pass the weekly window start so p_bar is identical to the weekly dashboard.
    Current: all in-shift batches summed into one observation.
    EWMA: raw batches (last 30) for fine-grained trend display.
    preloaded_subgroups: if provided, skip the DB scan and use these directly.
    """
    from datetime import datetime

    subgroups = preloaded_subgroups if preloaded_subgroups is not None else collect_spc_subgroups(conn, CFG, panel, excluded_days)
    if not subgroups:
        return None
    lane_keys, lane_labels = derive_spc_lanes(panel, subgroups, active_subgroups=subgroups[:-1])
    if not lane_keys:
        return None

    start_str  = shift_start_ts[:19] if isinstance(shift_start_ts, str) else str(shift_start_ts)[:19]
    cutoff_str = cutoff_ts[:19]      if isinstance(cutoff_ts, str)      else str(cutoff_ts)[:19]
    bl_cutoff  = (baseline_cutoff_ts[:19] if baseline_cutoff_ts else start_str)
    subgroups  = [s for s in subgroups if s["t_stamp"] <= cutoff_str]

    in_shift  = [s for s in subgroups if s["t_stamp"] >= start_str]
    pre_shift = [s for s in subgroups if s["t_stamp"] <  start_str]

    if not in_shift:
        if subgroups:
            gap_h = (datetime.fromisoformat(cutoff_str) - datetime.fromisoformat(subgroups[-1]["t_stamp"][:19])).total_seconds() / 3600
            return {"status": "no_shift_data", "last_reset_timestamp": subgroups[-1]["t_stamp"],
                    "gap_hours": round(gap_h, 1), "baseline_subgroups": 0,
                    "baseline_subgroups_requested": n_baseline}
        return None

    # Aggregate in-shift batches → one current observation
    current_n = sum(s["n_inspected"] for s in in_shift)
    current = {"t_stamp": in_shift[-1]["t_stamp"], "n_inspected": current_n}
    for k in lane_keys:
        current[k] = sum(s.get(k, 0) for s in in_shift)

    baseline = build_shift_baseline(pre_shift, lane_keys, n_baseline, bl_cutoff, SHIFT_BOUNDS, excluded_days)
    if len(baseline) < 2:
        return None

    lane_results = _laney_p_prime(baseline, current, lane_keys)
    for lr, label in zip(lane_results, lane_labels):
        lr["lane"] = label

    # EWMA UCL/LCL from the same pre-window batches as the Laney p' baseline.
    ewma_bl = [s for s in pre_shift if s["t_stamp"] < bl_cutoff]
    return {
        "lanes": lane_results,
        "baseline_subgroups": len(baseline),
        "baseline_subgroups_requested": n_baseline,
        "last_reset_timestamp": str(current["t_stamp"]),
        "ewma": _compute_ewma(subgroups[-30:], lane_keys,
                              baseline_subgroups=ewma_bl[-30:] if ewma_bl else None),
    }


# ──────────────────────────────────────────────
# In-process entry point (used by render_all_shifts.py)
# ──────────────────────────────────────────────

def run_shift_in_process(shift_date, shift_type, conn, shared):
    """Run the pipeline for one shift using pre-computed shared data.

    shared keys:
      p95_ppm              – float, global P95 across all needed shifts
      excluded             – set of excluded date strings
      spc_baseline_cutoff  – str, weekly window start used as p_bar cutoff
      spc_subgroups        – {panel_key: [subgroup dicts]} (no DB scan needed)
      cluster_profile_cache – {(date, type): profile dict} for the window shifts
      pre_window_baseline  – list of cluster profile dicts for the 13 pre-window shifts,
                             shared by all shifts in the window for consistent anomaly baselines

    Iterates only the current shift + 4 prior for OEE trend; anomaly baseline
    comes entirely from pre_window_baseline (no additional DB queries).

    Returns the output dict (does NOT write JSON files).
    """
    configure_segments(CFG["segments"])

    latest_date  = shift_date
    latest_type  = shift_type
    latest_start, latest_end = shift_window(shift_date, shift_type, SHIFT_BOUNDS)

    p95_ppm             = shared["p95_ppm"]
    excluded            = shared["excluded"]
    spc_baseline_cutoff = shared["spc_baseline_cutoff"]
    profile_cache       = shared["cluster_profile_cache"]
    spc_subgroups       = shared["spc_subgroups"]

    # Only need 5 shifts for the OEE trend; anomaly baseline comes from shared (pre-window fixed)
    all_shifts = [(latest_date, latest_type)]
    all_shifts.extend(previous_shifts(latest_date, latest_type, SHIFT_BOUNDS, n=4))

    tag = f"{latest_type[:1].upper()} {latest_date}"  # prefix for interleaved stdout

    shifts_data = []

    for i, (s_date, s_type) in enumerate(all_shifts):
        s_start, s_end = shift_window(s_date, s_type, SHIFT_BOUNDS)

        if i > 0 and s_date in excluded:
            shifts_data.append({
                "shift_date": s_date, "shift_type": s_type,
                "label": format_shift_label(s_type, s_date), "skipped": True,
            })
            continue

        shift_entry = _build_shift_entry(conn, s_date, s_type, s_start, s_end, p95_ppm)
        if i == 0:
            _enrich_latest_shift(conn, shift_entry, s_start, s_end)
        shifts_data.append(shift_entry)

    # Anomaly detection — use the shared pre-window baseline so every shift in the
    # weekly window is compared against the same reference period, matching the weekly panel.
    current_profile    = profile_cache.get((shift_date, shift_type), {})
    pre_window_baseline = shared.get("pre_window_baseline", [])
    anomalies = detect_shift_anomalies(current_profile, pre_window_baseline)
    shifts_data[0]["anomalies"] = {
        "cluster_outliers": anomalies,
        "baseline_shifts":  len(pre_window_baseline),
    }

    _attach_spc_panels(
        shifts_data[0], None, get_spc_panels(CFG), str(latest_end), str(latest_start),
        SPC_BASELINE_SUBGROUPS, excluded, spc_baseline_cutoff, preloaded=spc_subgroups,
    )

    label = _format_full_shift_label(latest_type, latest_date, SHIFT_BOUNDS)
    return {
        "analysis_id": "shift-dashboard",
        "line_id":     CFG["line_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_shift": {
            "label": label, "shift_date": latest_date, "shift_type": latest_type,
            "start": str(latest_start), "end": str(latest_end),
        },
        "p95_ideal_ppm": round(p95_ppm, 1),
        "shifts": shifts_data,
    }


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--line")
    parser.add_argument("--shift-date", help="Override shift date YYYY-MM-DD (e.g. 2026-05-27)")
    parser.add_argument("--shift-type", choices=["day", "night"], help="Override shift type")
    args, _ = parser.parse_known_args()

    configure_segments(CFG["segments"])
    conn = get_connection(database=CFG.get("database"))
    try:
        # Always detect the current latest shift — needed for SPC baseline cutoff
        # even when --shift-date overrides the analysis target.
        curr_date, curr_type, curr_start, curr_end = detect_latest_shift(conn)

        if args.shift_date and args.shift_type:
            from domain.shifts import shift_window as _sw
            latest_date = args.shift_date
            latest_type = args.shift_type
            latest_start, latest_end = _sw(latest_date, latest_type, SHIFT_BOUNDS)
        else:
            latest_date, latest_type, latest_start, latest_end = curr_date, curr_type, curr_start, curr_end

        cutoff = spc_baseline_cutoff(curr_date, curr_type, SHIFT_BOUNDS)
        print(f"SPC baseline cutoff (weekly window start): {cutoff}")

        # Build list of shifts: latest + 4 for trend + ANOMALY_BASELINE_SHIFTS more for anomaly baseline
        all_shifts = [(latest_date, latest_type)]
        all_shifts.extend(previous_shifts(latest_date, latest_type, SHIFT_BOUNDS, n=ANOMALY_BASELINE_SHIFTS))

        # Load excluded days early — used by both OEE loop and SPC
        sched_warnings = []
        schedule = apply_schedule(CFG, None, None, sched_warnings)
        excluded = set(schedule["excluded_days"])
        if excluded:
            print(f"Excluding {len(excluded)} non-production day(s) from trend and SPC: {sorted(excluded)}")
        for w in sched_warnings:
            print(f"  [schedule warning] {w}")

        p95_ppm = compute_global_p95(conn, [(sd, st) for sd, st in all_shifts])
        print(f"P95 ideal PPM (global): {p95_ppm:.1f}")

        shifts_data = []
        cluster_profiles = []
        for i, (s_date, s_type) in enumerate(all_shifts):
            s_start, s_end = shift_window(s_date, s_type, SHIFT_BOUNDS)

            # Skip excluded days in the trend window (never skip i==0 — current shift)
            if i > 0 and s_date in excluded:
                if i < 5:
                    shifts_data.append({
                        "shift_date": s_date,
                        "shift_type": s_type,
                        "label": format_shift_label(s_type, s_date),
                        "skipped": True,
                    })
                continue

            # Full metrics only for the 5 trend shifts
            if i < 5:
                print(f"\n--- {s_type.title()} {s_date} ---")
                shift_entry = _build_shift_entry(conn, s_date, s_type, s_start, s_end, p95_ppm)
                print(f"  OEE={shift_entry['oee']}%  A={shift_entry['availability']}%"
                      f"  P={shift_entry['performance']}%  Q={shift_entry['quality']}%")
                print(f"  Stops={shift_entry['total_stops']}  "
                      f"Downtime={shift_entry['total_downtime_hours']:.1f}h  "
                      f"Longest={shift_entry['longest_stop_min']:.0f}min")

                if i == 0:
                    _enrich_latest_shift(conn, shift_entry, s_start, s_end)
                    seg_str = ', '.join(f'{k}={v["hours"]}h'
                                        for k, v in shift_entry["segment_downtime"].items()
                                        if v['count'] > 0)
                    print(f"  Segments: {seg_str}")
                    print(f"  Hourly breakdown: {len(shift_entry['hourly_breakdown'])} hours")

                shifts_data.append(shift_entry)

            # Cluster profile for anomaly baseline (all shifts)
            profile = compute_shift_cluster_profile(conn, s_start, s_end)
            cluster_profiles.append(profile)

        # Anomaly detection: current shift vs baseline
        current_profile = cluster_profiles[0]
        baseline_profiles = cluster_profiles[1:]
        anomalies = detect_shift_anomalies(current_profile, baseline_profiles)
        shifts_data[0]["anomalies"] = {
            "cluster_outliers": anomalies,
            "baseline_shifts": len(baseline_profiles),
        }
        if anomalies:
            print(f"\nAnomalies detected: {len(anomalies)} cluster outliers")
            for a in anomalies[:5]:
                print(f"  {a['segment']}:{a['cluster']} — {a['current_share']}% vs baseline {a['baseline_mean']}% (z={a['z_score']})")

        cutoff = str(latest_end)
        _attach_spc_panels(shifts_data[0], conn, get_spc_panels(CFG),
                           cutoff, str(latest_start), SPC_BASELINE_SUBGROUPS, excluded, cutoff)
        for panel in get_spc_panels(CFG):
            spc_data = shifts_data[0].get(panel["key"])
            if not spc_data:
                continue
            spc_label = panel["title"]
            if spc_data.get("status") == "no_shift_data":
                print(f"\n{spc_label}: no shift data (last batch {spc_data.get('last_reset_timestamp','?')[:16]}, {spc_data.get('gap_hours','?')}h ago)")
            else:
                flagged = [n for n in spc_data["lanes"] if n["out_of_control"]]
                print(f"\n{spc_label}: {spc_data['baseline_subgroups']} baseline subgroups")
                if flagged:
                    for n in flagged:
                        print(f"  Lane {n['lane']} OUT OF CONTROL: {n['rate_pct']}% (UCL={n['ucl_pct']}%)")
                else:
                    print("  All lanes within control limits")
                if spc_data.get("ewma"):
                    print(f"  EWMA: {len(spc_data['ewma']['shifts'])} shifts trending")

        label = _format_full_shift_label(latest_type, latest_date, SHIFT_BOUNDS)

        output = {
            "analysis_id": "shift-dashboard",
            "line_id": CFG["line_id"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "latest_shift": {
                "label": label,
                "shift_date": latest_date,
                "shift_type": latest_type,
                "start": str(latest_start),
                "end": str(latest_end),
            },
            "p95_ideal_ppm": round(p95_ppm, 1),
            "shifts": shifts_data,
        }

        save_analysis_json(output, OUTPUT_PATH)
        print(f"\nWrote {OUTPUT_PATH}")

        if args.shift_date and args.shift_type:
            per_shift_dir = os.path.join(output_dir(CFG), "shifts")
            per_shift_path = os.path.join(
                per_shift_dir, f"shift-{latest_date}-{latest_type}.json"
            )
            save_analysis_json(output, per_shift_path)
            print(f"Wrote {per_shift_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
