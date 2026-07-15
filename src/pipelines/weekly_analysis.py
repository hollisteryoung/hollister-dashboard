#!/usr/bin/env python3
"""
Weekly Dashboard — 7-day aggregate view.

Queries the DB for the last N_DAYS calendar days (2 shifts per day) and
computes per-shift and aggregate weekly OEE, downtime, and segment metrics.

Produces output/analyses/<line>/weekly-dashboard.json.

Usage:
    MSSQL_SA_PASSWORD=<pw> python3 src/pipelines/weekly_analysis.py [--line ngp2|hu3]
"""

import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from core.db import get_connection
from core.lines import get_line_config, has_table, output_dir, get_spc_panels, get_status_block_cols
from metrics.oee import (
    _query_stop_blocks,
    build_anomaly_baseline,
    compute_global_p95,
    compute_shift_availability,
    compute_shift_cluster_profile,
    compute_shift_downtime_categories,
    compute_shift_performance,
    compute_shift_quality,
    compute_shift_segment_downtime,
    compute_shift_stop_summary,
    detect_latest_shift,
    detect_shift_anomalies,
)
from metrics.spc import (
    _compute_ewma,
    _laney_p_prime_params,
    build_shift_baseline,
    collect_spc_subgroups,
    derive_spc_lanes,
)
from core.storage import save_analysis_json
from domain.clusters import classify_alarm_segment, classify_cause_cluster, configure_segments
from domain.faults import parse_faults_field, rc_display_label
from domain.schedule import apply_schedule
from domain.shifts import format_shift_label, previous_shifts, shift_window

CFG = get_line_config()
SHIFT_BOUNDS = CFG["shift_boundaries"]

_SB_COLS  = get_status_block_cols(CFG)
C_START   = _SB_COLS.C_START
C_END     = _SB_COLS.C_END
C_RUN     = _SB_COLS.C_RUN
C_FAULTS  = _SB_COLS.C_FAULTS
C_STOPTYPE = _SB_COLS.C_STOPTYPE
C_STARTID = _SB_COLS.C_STARTID
C_ALARM   = _SB_COLS.C_ALARM

N_DAYS = 7               # calendar days to cover = N_DAYS * 2 shifts
SPC_BASELINE_SUBGROUPS = 100  # per-shift buckets before the weekly window used for p_bar/sigma_z
ANOMALY_BASELINE_SHIFTS = 13  # prior shifts used for per-shift z-score baseline (matches shift_dashboard)

OUTPUT_PATH = os.path.join(output_dir(CFG), "weekly-dashboard.json")


def _profile_from_seg_dt(seg_dt):
    """Derive a {seg:cluster → share} profile from an accumulated seg_dt dict.

    Mirrors compute_shift_cluster_profile's `cluster = ... or seg` fallback:
    named clusters → {seg}:{cluster}; unclustered remainder → {seg}:{seg}.
    """
    raw = {}
    for seg, data in seg_dt.items():
        clusters = data.get("clusters", {})
        for cluster, cd in clusters.items():
            h = cd.get("hours", 0.0)
            if h > 0:
                raw[f"{seg}:{cluster}"] = raw.get(f"{seg}:{cluster}", 0.0) + h
        remainder = data.get("hours", 0.0) - sum(cd.get("hours", 0.0) for cd in clusters.values())
        if remainder > 0.0:
            raw[f"{seg}:{seg}"] = raw.get(f"{seg}:{seg}", 0.0) + remainder
    total = sum(raw.values())
    return {k: round(v / total, 4) for k, v in raw.items()} if total > 0 else {}


def _compute_prior_weekly_profiles(conn, all_shifts, excluded, n_prior_weeks=4):
    """Return a list of cluster-share profiles, one per prior calendar week.

    Each profile is a {seg:cluster → share} dict aggregated across that
    week's production shifts (same format as compute_shift_cluster_profile).
    Shifts in excluded are skipped.  anchor starts from the shift just before
    the current week (all_shifts[-1] is the oldest shift of the current week).
    """
    n_shifts = N_DAYS * 2
    baseline = []
    anchor_date, anchor_type = all_shifts[-1]
    for _ in range(n_prior_weeks):
        week_shifts = previous_shifts(anchor_date, anchor_type, SHIFT_BOUNDS, n=n_shifts)
        if not week_shifts:
            break
        week_raw = {}
        week_total = 0.0
        for s_date, s_type in week_shifts:
            if s_date in excluded:
                continue
            s_start, s_end = shift_window(s_date, s_type, SHIFT_BOUNDS)
            p = compute_shift_cluster_profile(conn, s_start, s_end)
            for key, share in p.items():
                week_raw[key] = week_raw.get(key, 0.0) + share
                week_total += share
        if week_raw and week_total > 0:
            baseline.append({k: round(v / week_total, 4) for k, v in week_raw.items()})
        anchor_date, anchor_type = week_shifts[-1]
    return baseline


def _add_segment_dt(total, delta):
    """Merge a per-shift segment_downtime dict into a running total."""
    for seg, data in delta.items():
        if seg not in total:
            total[seg] = {"count": 0, "hours": 0.0, "halted_hours": 0.0,
                          "stopped_hours": 0.0, "clusters": {}}
        total[seg]["count"] += data.get("count", 0)
        total[seg]["hours"] = round(total[seg]["hours"] + data.get("hours", 0), 2)
        total[seg]["halted_hours"] = round(
            total[seg]["halted_hours"] + data.get("halted_hours", 0), 2)
        total[seg]["stopped_hours"] = round(
            total[seg]["stopped_hours"] + data.get("stopped_hours", 0), 2)
        for cluster, cd in data.get("clusters", {}).items():
            tc = total[seg]["clusters"].setdefault(
                cluster, {"count": 0, "hours": 0.0,
                          "halted_hours": 0.0, "stopped_hours": 0.0})
            tc["count"] += cd.get("count", 0)
            tc["hours"] = round(tc["hours"] + cd.get("hours", 0), 2)
            tc["halted_hours"] = round(
                tc["halted_hours"] + cd.get("halted_hours", 0), 2)
            tc["stopped_hours"] = round(
                tc["stopped_hours"] + cd.get("stopped_hours", 0), 2)


def _add_dt_categories(total, delta):
    for key in ("micro_stops", "halted", "auto_long", "manual"):
        src = delta.get(key, {"count": 0, "hours": 0.0})
        t = total.setdefault(key, {"count": 0, "hours": 0.0})
        t["count"] += src.get("count", 0)
        t["hours"] = round(t["hours"] + src.get("hours", 0), 2)


def _collect_weekly_top_stops(conn, all_shifts, excluded, n_top=5):
    """Return top N stop labels per (segment, cluster) across all production shifts.

    Uses _query_stop_blocks from metrics.oee for data access, then aggregates
    results across all production shifts in the weekly window.
    """
    # {seg: {cluster: {label: [count, hours]}}}
    acc = {}

    for s_date, s_type in all_shifts:
        if s_date in excluded:
            continue
        s_start, s_end = shift_window(s_date, s_type, SHIFT_BOUNDS)
        df = _query_stop_blocks(conn, s_start, s_end)
        if len(df) == 0:
            continue
        # _query_stop_blocks already clips timestamps and computes dur_min.
        df["dur_h"] = df["dur_min"] / 60.0

        for _, row in df.iterrows():
            parsed = parse_faults_field(row["Faults"] if pd.notna(row["Faults"]) else None)
            alarm = row.get("alarm_text") if pd.notna(row.get("alarm_text", None)) else None
            label = rc_display_label(parsed, alarm)
            seg = classify_alarm_segment(parsed, alarm, row.get("StopType"))
            cluster = classify_cause_cluster(label, seg)
            if cluster is None:
                continue
            entry = acc.setdefault(seg, {}).setdefault(cluster, {}).setdefault(label, [0, 0.0])
            entry[0] += 1
            entry[1] = round(entry[1] + row["dur_h"], 4)

    result = {}
    for seg, clusters in acc.items():
        result[seg] = {}
        for cluster, labels in clusters.items():
            sorted_labels = sorted(labels.items(), key=lambda x: -x[1][1])
            result[seg][cluster] = [
                {"label": lbl, "count": cnt, "hours": round(h, 2)}
                for lbl, (cnt, h) in sorted_labels[:n_top]
            ]
    return result


def _aggregate_subgroups_by_shift(subgroups, all_shifts, excluded):
    """Aggregate counter-reset subgroups into one data point per production shift (oldest first).

    Every non-excluded production shift is emitted so all SPC panels share the same
    x-axis.  Shifts with no qualifying subgroups get n_inspected=0; callers render
    those as gap markers (null rate/UCL/LCL) rather than shrinking the axis.

    Each reset belongs to exactly one shift — the shift whose window contains the
    reset timestamp (strict: shift_start ≤ t_stamp < shift_end).  No buffer is
    applied here because a counter reset at 18:56 captures Day-shift production and
    unambiguously belongs to Day.  The shift dashboard uses a separate, more lenient
    rule (12.5h threshold) to show the most recent batch as context; that is a
    display heuristic, not a data assignment decision.
    """
    if not subgroups:
        return []
    lane_keys = [k for k in subgroups[0].keys() if k not in ("t_stamp", "n_inspected")]
    result = []
    for s_date, s_type in reversed(all_shifts):
        if s_date in excluded:
            continue
        s_start, s_end = shift_window(s_date, s_type, SHIFT_BOUNDS)
        s_start_str = s_start.strftime("%Y-%m-%d %H:%M:%S")
        s_end_str = s_end.strftime("%Y-%m-%d %H:%M:%S")
        shift_subs = [s for s in subgroups if s_start_str <= s["t_stamp"] < s_end_str]
        label = format_shift_label(s_type, s_date)
        total_n = sum(s["n_inspected"] for s in shift_subs) if shift_subs else 0
        if not shift_subs or total_n < 100:
            result.append({"t_stamp": label, "n_inspected": 0})
            continue
        agg = {"t_stamp": label, "n_inspected": total_n}
        for k in lane_keys:
            agg[k] = sum(s.get(k, 0) for s in shift_subs)
        result.append(agg)
    return result



def _laney_p_prime_series(subgroups, lane_keys, lane_labels, baseline_subs=None):
    """Laney p' for a full series of shift-level subgroups; returns per-lane time-series data.

    baseline_subs: pre-window shift buckets used for p_bar/sigma_z; defaults to subgroups.
    Subgroups with n_inspected=0 (gap sentinels) emit null-valued points but are excluded
    from p_bar estimation so all lanes share a consistent x-axis across SPC panels.
    """
    p_bar_source = baseline_subs if baseline_subs is not None else subgroups
    valid_subs = [s for s in p_bar_source if s.get("n_inspected", 0) > 0]
    results = []
    for key, label in zip(lane_keys, lane_labels):
        d_vals_v = [s.get(key, 0) for s in valid_subs]
        n_vals_v = [s["n_inspected"] for s in valid_subs]

        # Delegate all Laney p' parameter math to the single source of truth in spc.py.
        p_bar, sigma_z = _laney_p_prime_params(d_vals_v, n_vals_v)

        points = []
        any_ooc = False
        for s in subgroups:
            n = s.get("n_inspected", 0)
            if n == 0:
                points.append({
                    "label": s["t_stamp"],
                    "rate_pct": None,
                    "ucl_pct": None,
                    "lcl_pct": None,
                    "out_of_control": False,
                })
                continue
            d = s.get(key, 0)
            rate = d / n
            se = (p_bar * (1 - p_bar) / n) ** 0.5 if p_bar > 0 else 0
            ucl = p_bar + 3 * sigma_z * se
            lcl = max(0.0, p_bar - 3 * sigma_z * se)
            ooc = rate > ucl
            if ooc:
                any_ooc = True
            points.append({
                "label": s["t_stamp"],
                "rate_pct": round(rate * 100, 3),
                "ucl_pct": round(ucl * 100, 3),
                "lcl_pct": round(lcl * 100, 3),
                "out_of_control": ooc,
            })
        results.append({
            "lane": label,
            "p_bar_pct": round(p_bar * 100, 3),
            "points": points,
            "any_out_of_control": any_ooc,
        })
    return results


_EWMA_BATCH_WINDOW = 30  # must match shift_analysis.py subgroups[-30:]


def _build_weekly_spc(shift_subs, all_subgroups, lane_keys, lane_labels, cutoff_str,
                      baseline_subs=None, window_start_str=None):
    """Laney p' on shift-aggregated subgroups; EWMA on full history batch subgroups.

    baseline_subs: pre-window per-shift buckets for Laney p' p_bar/sigma_z.
    window_start_str: upper bound for EWMA UCL/LCL baseline batches (same cutoff as Laney p').
    Both align the weekly's reference period with the shift dashboard's.
    """
    lanes = _laney_p_prime_series(shift_subs, lane_keys, lane_labels, baseline_subs=baseline_subs)
    windowed = [s for s in all_subgroups if s["t_stamp"] <= cutoff_str]
    ewma_bl = None
    if window_start_str:
        ewma_bl_list = [s for s in all_subgroups if s["t_stamp"] < window_start_str]
        ewma_bl = ewma_bl_list[-_EWMA_BATCH_WINDOW:] if ewma_bl_list else None
    ewma = _compute_ewma(windowed[-_EWMA_BATCH_WINDOW:], lane_keys, baseline_subgroups=ewma_bl)
    valid_shifts = sum(1 for s in shift_subs if s.get("n_inspected", 0) > 0)

    # Per-shift raw counts for the detail table (rejects + inspected per lane per shift).
    shift_table = []
    for j, s in enumerate(shift_subs):
        n = s.get("n_inspected", 0)
        if n == 0:
            continue
        row = {"label": s["t_stamp"], "n_inspected": round(n), "per_lane": []}
        for li, key in enumerate(lane_keys):
            d = s.get(key, 0)
            ooc = lanes[li]["points"][j]["out_of_control"]
            row["per_lane"].append({
                "lane": lane_labels[li],
                "rejects": round(d),
                "rate_pct": round(d / n * 100, 2) if n > 0 else 0,
                "ooc": ooc,
            })
        shift_table.append(row)

    return {"shift_subgroups": valid_shifts, "total_shifts": len(shift_subs),
            "lanes": lanes, "ewma": ewma, "shift_table": shift_table}


def _weekly_cutoff_str(all_shifts):
    """Upper-bound cutoff for EWMA: end of the most recent shift in the weekly window."""
    _, newest_end = shift_window(all_shifts[0][0], all_shifts[0][1], SHIFT_BOUNDS)
    return newest_end.strftime("%Y-%m-%d %H:%M:%S")


def compute_weekly_spc(conn, panel, all_shifts, excluded):
    """Shift-level Laney p' series + EWMA for one declarative SPC panel.

    p_bar/sigma_z are computed from per-shift buckets BEFORE the weekly window start —
    the same reference used by the shift dashboard — so UCLs are identical between views.
    """
    subgroups = collect_spc_subgroups(conn, CFG, panel, excluded)
    if not subgroups:
        return None
    lane_keys, lane_labels = derive_spc_lanes(panel, subgroups, active_subgroups=subgroups)
    if not lane_keys:
        return None
    shift_subs = _aggregate_subgroups_by_shift(subgroups, all_shifts, excluded)
    if sum(1 for s in shift_subs if s.get("n_inspected", 0) > 0) < 3:
        return None

    # Baseline: per-shift buckets strictly before the weekly window start.
    # This matches the shift dashboard's spc_baseline_cutoff so both views
    # share the same p_bar and sigma_z.
    window_start, _ = shift_window(all_shifts[-1][0], all_shifts[-1][1], SHIFT_BOUNDS)
    window_start_str = window_start.strftime("%Y-%m-%d %H:%M:%S")
    baseline_subs = build_shift_baseline(subgroups, lane_keys, SPC_BASELINE_SUBGROUPS,
                                         window_start_str, SHIFT_BOUNDS, excluded)
    if len(baseline_subs) < 2:
        baseline_subs = None  # fall back to within-window data if history is insufficient

    return _build_weekly_spc(shift_subs, subgroups, lane_keys, lane_labels,
                             _weekly_cutoff_str(all_shifts), baseline_subs=baseline_subs,
                             window_start_str=window_start_str)


def _compute_weekly_shift_entry(conn, s_date, s_type, s_start, s_end, p95_ppm) -> dict:
    """Compute all OEE metrics for one shift in the weekly window and return the entry dict.

    Every shift in the window gets segment_downtime so the weekly aggregate can accumulate it.
    """
    avail_pct, run_h, _ = compute_shift_availability(conn, s_start, s_end)
    perf_pct, _          = compute_shift_performance(conn, s_start, s_end, p95_ppm)
    qual_pct, good, total = compute_shift_quality(conn, s_start, s_end)
    oee = avail_pct / 100 * perf_pct / 100 * qual_pct / 100 * 100
    dt_cats = compute_shift_downtime_categories(conn, s_start, s_end)
    n_stops, dt_h, longest_min = compute_shift_stop_summary(conn, s_start, s_end)
    seg_dt = compute_shift_segment_downtime(conn, s_start, s_end)
    return {
        "shift_date": s_date, "shift_type": s_type,
        "label": format_shift_label(s_type, s_date),
        "oee": round(oee, 1), "availability": round(avail_pct, 1),
        "performance": round(perf_pct, 1), "quality": round(qual_pct, 1),
        "running_hours": round(run_h, 2), "total_stops": n_stops,
        "total_downtime_hours": round(dt_h, 2), "longest_stop_min": round(longest_min, 1),
        "good_count": good, "total_count": total,
        "downtime_categories": dt_cats, "segment_downtime": seg_dt,
        "skipped": False,
    }


def _aggregate_prior_week_oee(conn, prior_shifts, excluded, p95_ppm) -> dict:
    """Return OEE components rolled up over a week of prior shifts.

    Returns {"oee", "availability", "performance", "quality", "good", "total",
             "production_shifts", "from", "to"}.
    """
    running_h = perf_weighted = 0.0
    quality_good = quality_total = production_shifts = 0
    for s_date, s_type in prior_shifts:
        if s_date in excluded:
            continue
        s_start, s_end = shift_window(s_date, s_type, SHIFT_BOUNDS)
        avail_pct, run_h, _ = compute_shift_availability(conn, s_start, s_end)
        perf_pct, _          = compute_shift_performance(conn, s_start, s_end, p95_ppm)
        qual_pct, good, total = compute_shift_quality(conn, s_start, s_end)
        running_h     += run_h
        perf_weighted += perf_pct * run_h
        quality_good  += good
        quality_total += total
        production_shifts += 1
    scheduled_h = production_shifts * 12
    avail = running_h / scheduled_h * 100 if scheduled_h > 0 else 0.0
    perf  = perf_weighted / running_h      if running_h > 0   else 0.0
    qual  = quality_good / quality_total * 100 if quality_total > 0 else 0.0
    oee   = avail / 100 * perf / 100 * qual / 100 * 100
    return {
        "oee":  round(oee, 1),  "availability": round(avail, 1),
        "performance": round(perf, 1), "quality": round(qual, 1),
        "good": quality_good, "total": quality_total,
        "from": prior_shifts[-1][0] if prior_shifts else None,
        "to":   prior_shifts[0][0]  if prior_shifts else None,
    }


def main():
    configure_segments(CFG["segments"])
    conn = get_connection(database=CFG.get("database"))
    try:
        latest_date, latest_type, latest_start, latest_end = detect_latest_shift(conn)

        n_shifts = N_DAYS * 2
        all_shifts = [(latest_date, latest_type)]
        all_shifts.extend(previous_shifts(latest_date, latest_type, SHIFT_BOUNDS, n=n_shifts - 1))

        sched_warnings = []
        schedule = apply_schedule(CFG, None, None, sched_warnings)
        excluded = set(schedule["excluded_days"])
        for w in sched_warnings:
            print(f"  [schedule warning] {w}")

        p95_ppm = compute_global_p95(
            conn, [(sd, st) for sd, st in all_shifts
                   if sd not in excluded])
        print(f"P95 ideal PPM (global): {p95_ppm:.1f}")

        print("\nPre-computing cluster profiles for per-shift anomaly detection...")
        shift_profile_cache, _shared_baseline_profiles = build_anomaly_baseline(
            conn, all_shifts, all_shifts[-1], SHIFT_BOUNDS,
            n_baseline=ANOMALY_BASELINE_SHIFTS, excluded=excluded,
        )
        print(f"  {len(shift_profile_cache)} profiles cached, {len(_shared_baseline_profiles)} pre-window baseline shifts")

        shifts_data = []

        # Weekly accumulators
        total_running_h = 0.0
        total_perf_weighted = 0.0   # sum(perf * running_h)
        total_quality_good = 0
        total_quality_total = 0
        total_stops = 0
        total_dt_hours = 0.0
        longest_stop = 0.0
        week_dt_cats = {}
        week_seg_dt = {}
        production_shifts = 0
        excluded_shifts = 0

        for s_date, s_type in all_shifts:
            s_start, s_end = shift_window(s_date, s_type, SHIFT_BOUNDS)

            label = format_shift_label(s_type, s_date)
            print(f"\n--- {label} ---")

            if s_date in excluded:
                print("  [non-production day, skipping]")
                excluded_shifts += 1
                shifts_data.append({
                    "shift_date": s_date,
                    "shift_type": s_type,
                    "label": label,
                    "skipped": True,
                })
                continue

            entry = _compute_weekly_shift_entry(conn, s_date, s_type, s_start, s_end, p95_ppm)
            print(f"  OEE={entry['oee']}%  A={entry['availability']}%"
                  f"  P={entry['performance']}%  Q={entry['quality']}%")
            print(f"  Stops={entry['total_stops']}  Downtime={entry['total_downtime_hours']:.1f}h"
                  f"  Longest={entry['longest_stop_min']:.0f}min")

            # Accumulators
            production_shifts += 1
            total_running_h     += entry["running_hours"]
            total_perf_weighted += entry["performance"] * entry["running_hours"]
            total_quality_good  += entry["good_count"]
            total_quality_total += entry["total_count"]
            total_stops         += entry["total_stops"]
            total_dt_hours       = round(total_dt_hours + entry["total_downtime_hours"], 2)
            longest_stop         = max(longest_stop, entry["longest_stop_min"])
            _add_dt_categories(week_dt_cats, entry["downtime_categories"])
            _add_segment_dt(week_seg_dt, entry["segment_downtime"])

            _current_profile = shift_profile_cache.get((s_date, s_type), {})
            _shift_anomalies = detect_shift_anomalies(_current_profile, _shared_baseline_profiles)
            if _shift_anomalies:
                print(f"  Per-shift anomalies: {len(_shift_anomalies)} cluster outlier(s)")
            entry["anomalies"] = {
                "cluster_outliers": _shift_anomalies,
                "baseline_shifts":  len(_shared_baseline_profiles),
            }
            shifts_data.append(entry)

        # Weekly aggregate OEE
        scheduled_h = production_shifts * 12
        week_avail = total_running_h / scheduled_h * 100 if scheduled_h > 0 else 0.0
        week_perf = total_perf_weighted / total_running_h if total_running_h > 0 else 0.0
        week_qual = (total_quality_good / total_quality_total * 100
                     if total_quality_total > 0 else 0.0)
        week_oee = week_avail / 100 * week_perf / 100 * week_qual / 100 * 100

        week_from = all_shifts[-1][0]
        week_to = all_shifts[0][0]

        print(f"\n=== Weekly summary ({week_from} → {week_to}) ===")
        print(f"  OEE={week_oee:.1f}%  A={week_avail:.1f}%  P={week_perf:.1f}%  Q={week_qual:.1f}%")
        print(f"  Stops={total_stops}  Downtime={total_dt_hours:.1f}h  Longest={longest_stop:.0f}min")
        print(f"  Production shifts: {production_shifts}  Excluded: {excluded_shifts}")

        print("\nCollecting top stops per cluster...")
        top_stops = _collect_weekly_top_stops(conn, all_shifts, excluded, n_top=5)

        print("\nComputing weekly SPC (shift-level subgroups)...")
        weekly_spc = {}
        for panel in get_spc_panels(CFG):
            if not has_table(CFG, panel["gate_table"]):
                continue
            spc = compute_weekly_spc(conn, panel, all_shifts, excluded)
            weekly_spc["weekly_" + panel["key"]] = spc
            if spc:
                ooc = [l for l in spc["lanes"] if l.get("any_out_of_control")]
                print(f"  {panel['title']}: {spc['shift_subgroups']} shifts"
                      + (f", OOC lanes: {[l['lane'] for l in ooc]}" if ooc else ", all in control"))

        # ── Weekly anomaly detection ─────────────────────────────────────
        print("\nComputing weekly anomaly detection...")
        current_week_profile = _profile_from_seg_dt(week_seg_dt)
        prior_profiles = _compute_prior_weekly_profiles(conn, all_shifts, excluded, n_prior_weeks=4)
        week_anomalies = detect_shift_anomalies(current_week_profile, prior_profiles)
        if week_anomalies:
            print(f"  {len(week_anomalies)} weekly cluster outliers detected:")
            for a in week_anomalies[:3]:
                print(f"    {a['segment']}:{a['cluster']} — "
                      f"{a['current_share']}% vs baseline {a['baseline_mean']}% (z={a['z_score']})")
        else:
            print("  No weekly anomalies detected")

        # ── Prior week aggregate ─────────────────────────────────────────
        print("\nComputing prior week aggregate...")
        prior_week_shifts = previous_shifts(all_shifts[-1][0], all_shifts[-1][1],
                                            SHIFT_BOUNDS, n=n_shifts)
        prior = _aggregate_prior_week_oee(conn, prior_week_shifts, excluded, p95_ppm)
        prior_from, prior_to = prior["from"], prior["to"]
        print(f"  Prior ({prior_from} → {prior_to}): OEE={prior['oee']}%  "
              f"A={prior['availability']}%  P={prior['performance']}%  Q={prior['quality']}%")

        output = {
            "analysis_id": "weekly-dashboard",
            "line_id": CFG["line_id"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "week": {
                "from": week_from,
                "to": week_to,
                "oee": round(week_oee, 1),
                "availability": round(week_avail, 1),
                "performance": round(week_perf, 1),
                "quality": round(week_qual, 1),
                "good_count": total_quality_good,
                "total_count": total_quality_total,
                "total_stops": total_stops,
                "total_downtime_hours": total_dt_hours,
                "longest_stop_min": round(longest_stop, 1),
                "production_shifts": production_shifts,
                "excluded_shifts": excluded_shifts,
                "downtime_categories": week_dt_cats,
                "segment_downtime": week_seg_dt,
                "top_stops_by_cluster": top_stops,
                "anomalies": {
                    "cluster_outliers": week_anomalies,
                    "baseline_weeks": len(prior_profiles),
                },
                "prior_week": {
                    "from": prior_from, "to": prior_to,
                    "oee": prior["oee"], "availability": prior["availability"],
                    "performance": prior["performance"], "quality": prior["quality"],
                },
            },
            "shifts": shifts_data,
            **weekly_spc,
        }

        save_analysis_json(output, OUTPUT_PATH)
        print(f"\nWrote {OUTPUT_PATH}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
