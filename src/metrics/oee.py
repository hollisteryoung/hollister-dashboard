#!/usr/bin/env python3
"""
OEE compute functions — shared by pipelines/shift_analysis and pipelines/weekly_analysis.

All functions accept conn + time window args.  Module-level CFG/SHIFT_BOUNDS
are resolved once at import time so callers don't have to thread them through.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from core.db import query
from core.lines import get_line_config, get_table, get_thresholds, get_col, get_status_block_cols
from domain.clusters import get_segment_order, classify_alarm_segment, classify_cause_cluster
from domain.faults import parse_faults_field, rc_display_label
from domain.shifts import assign_shift, previous_shifts, shift_window

CFG = get_line_config()
SHIFT_BOUNDS = CFG["shift_boundaries"]
SHIFT_LENGTH_HOURS = (SHIFT_BOUNDS[1] - SHIFT_BOUNDS[0]) % 24  # e.g. 07→19 = 12h
MICRO_STOP_THRESHOLD_MIN = get_thresholds(CFG)["micro_stop_min"]

# Physical historian column names (config-driven). SQL selects alias these back to
# their canonical names so downstream pandas references stay line-agnostic.
# StatusBlocks columns are resolved via the single source of truth in line_config.
_SB_COLS = get_status_block_cols(CFG)
C_START   = _SB_COLS.C_START
C_END     = _SB_COLS.C_END
C_RUN     = _SB_COLS.C_RUN
C_FAULTS  = _SB_COLS.C_FAULTS
C_STOPTYPE = _SB_COLS.C_STOPTYPE
C_STARTID = _SB_COLS.C_STARTID
C_ALARM   = _SB_COLS.C_ALARM
C_PPM = get_col(CFG, "output_ppm")
C_TOTAL = get_col(CFG, "counter_total")
C_GOOD = get_col(CFG, "counter_good")
C_TS = get_col(CFG, "t_stamp")


# ──────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────

def _query_stop_blocks(conn, s_start, s_end, extra_cols=""):
    """Query stopped StatusBlocks LEFT JOINed with FOFchanges for a shift window."""
    tbl_sb = get_table(CFG, "status_blocks")
    tbl_fof = get_table(CFG, "fof_changes")
    fof_ndx = CFG["fof_ndx_col"]
    extra = f", {extra_cols}" if extra_cols else ""
    sql = f"""
    SELECT sb.{C_START} AS Start_TS, sb.{C_END} AS End_TS, sb.{C_FAULTS} AS Faults, sb.{C_STOPTYPE} AS StopType,
           NULLIF(fof.{C_ALARM}, '') AS alarm_text{extra}
    FROM {tbl_sb} sb
    LEFT JOIN {tbl_fof} fof ON sb.{C_STARTID} = fof.{fof_ndx}
    WHERE sb.{C_START} < ? AND sb.{C_END} > ? AND sb.{C_END} IS NOT NULL
      AND (sb.{C_RUN} = 0 OR sb.{C_RUN} = 'False')
    ORDER BY sb.{C_START}
    """
    df = query(conn, sql, params=[str(s_end), str(s_start)])
    if len(df) == 0:
        return df
    df["Start_TS"] = pd.to_datetime(df["Start_TS"]).clip(lower=pd.Timestamp(s_start))
    df["End_TS"] = pd.to_datetime(df["End_TS"]).clip(upper=pd.Timestamp(s_end))
    df["dur_min"] = (df["End_TS"] - df["Start_TS"]).dt.total_seconds() / 60.0
    return df


def _detect_halted_indices(conn, df, s_start, s_end):
    """Return a boolean Series marking which rows in df are halted stops."""
    is_halted = pd.Series(False, index=df.index)
    halted_states = CFG.get("halted_states", ["Halted"])
    ms_text_col = CFG["machine_state_text_col"]
    tbl_ms = get_table(CFG, "machine_state")
    sql_ms = f"""
    SELECT {C_TS} AS t_stamp, {ms_text_col} AS state_text FROM {tbl_ms}
    WHERE {C_TS} >= ? AND {C_TS} < ?
    ORDER BY {C_TS}
    """
    df_ms = query(conn, sql_ms, params=[str(s_start), str(s_end)])
    if len(df_ms) == 0:
        return is_halted
    df_ms["t_stamp"] = pd.to_datetime(df_ms["t_stamp"])
    halted_ts = df_ms.loc[df_ms["state_text"].isin(halted_states), "t_stamp"].values
    if len(halted_ts) == 0:
        return is_halted
    starts = pd.to_datetime(df["Start_TS"]).values
    ends = pd.to_datetime(df["End_TS"]).values
    for i in range(len(df)):
        mask = (halted_ts >= starts[i]) & (halted_ts < ends[i])
        if mask.any():
            is_halted.iloc[i] = True
    return is_halted


# ──────────────────────────────────────────────
# Detect latest completed shift
# ──────────────────────────────────────────────

def detect_latest_shift(conn):
    tables = ["status_blocks", "ppm", "output_stats"]
    max_ts = []
    for tname in tables:
        tbl = get_table(CFG, tname)
        col = C_END if tname == "status_blocks" else C_TS
        sql = f"SELECT MAX({col}) AS latest FROM {tbl}"
        val = query(conn, sql).iloc[0, 0]
        if val is not None:
            max_ts.append(pd.to_datetime(val))
            print(f"  {tname} latest: {val}")
    latest = min(max_ts)
    shift_type, shift_date = assign_shift(latest, SHIFT_BOUNDS)

    start, end = shift_window(shift_date, shift_type, SHIFT_BOUNDS)
    if latest < end:
        prev = previous_shifts(shift_date, shift_type, SHIFT_BOUNDS, n=1)
        shift_date, shift_type = prev[0]
        start, end = shift_window(shift_date, shift_type, SHIFT_BOUNDS)

    print(f"Latest completed shift: {shift_type.title()} {shift_date} ({start} – {end})")
    return shift_date, shift_type, start, end


# ──────────────────────────────────────────────
# Availability
# ──────────────────────────────────────────────

def compute_shift_availability(conn, s_start, s_end):
    tbl = get_table(CFG, "status_blocks")
    sql = f"""
    SELECT {C_START} AS Start_TS, {C_END} AS End_TS, {C_RUN} AS RunningStatus
    FROM {tbl}
    WHERE {C_START} < ? AND {C_END} > ? AND {C_END} IS NOT NULL
    ORDER BY {C_START}
    """
    df = query(conn, sql, params=[str(s_end), str(s_start)])
    if len(df) == 0:
        return 0.0, 0.0, 0.0

    df["Start_TS"] = pd.to_datetime(df["Start_TS"])
    df["End_TS"] = pd.to_datetime(df["End_TS"])
    df["Start_TS"] = df["Start_TS"].clip(lower=pd.Timestamp(s_start))
    df["End_TS"] = df["End_TS"].clip(upper=pd.Timestamp(s_end))
    df["seconds"] = (df["End_TS"] - df["Start_TS"]).dt.total_seconds()

    running_mask = (df["RunningStatus"] == 1) | (df["RunningStatus"] == "true")
    running_s = df.loc[running_mask, "seconds"].sum()
    stopped_s = df.loc[~running_mask, "seconds"].sum()
    total_s = SHIFT_LENGTH_HOURS * 3600
    avail_pct = running_s / total_s * 100 if total_s > 0 else 0.0
    return avail_pct, running_s / 3600, stopped_s / 3600


# ──────────────────────────────────────────────
# Shared classifiers
# ──────────────────────────────────────────────

def classify_stop_row(row) -> tuple:
    """Return (segment, cluster, label) for a stopped status block row.

    Centralises the four-step decode that every stop-analysis function repeats:
    parse_faults_field → rc_display_label → classify_alarm_segment → classify_cause_cluster.
    """
    faults = row["Faults"] if pd.notna(row.get("Faults")) else None
    alarm = row["alarm_text"] if pd.notna(row.get("alarm_text")) else None
    stop_type = row.get("StopType")
    parsed = parse_faults_field(faults)
    label = rc_display_label(parsed, alarm)
    seg = classify_alarm_segment(parsed, alarm, stop_type)
    cluster = classify_cause_cluster(label, seg)
    return seg, cluster, label


def flag_running_intervals(ts_series: pd.Series, run_df: pd.DataFrame) -> np.ndarray:
    """Return boolean array: True where each timestamp falls within a running block.

    Uses binary search — O((N + M) log M) — instead of the naive O(N × M) loop.
    run_df must have Start_TS / End_TS columns as datetime64 and be sorted by Start_TS.
    Non-overlapping intervals are assumed (guaranteed by StatusBlocks structure).
    """
    if len(run_df) == 0:
        return np.zeros(len(ts_series), dtype=bool)
    starts = run_df["Start_TS"].values.astype("datetime64[ns]")
    ends = run_df["End_TS"].values.astype("datetime64[ns]")
    ts = ts_series.values.astype("datetime64[ns]")
    idx = np.searchsorted(starts, ts, side="right") - 1
    result = np.zeros(len(ts), dtype=bool)
    in_range = idx >= 0
    result[in_range] = ts[in_range] <= ends[idx[in_range]]
    return result


# ──────────────────────────────────────────────
# Performance
# ──────────────────────────────────────────────

def compute_global_p95(conn, shifts):
    """Compute P95 PPM across all shifts for a consistent baseline."""
    tbl = get_table(CFG, "ppm")
    earliest = min(shift_window(sd, st, SHIFT_BOUNDS)[0] for sd, st in shifts)
    latest = max(shift_window(sd, st, SHIFT_BOUNDS)[1] for sd, st in shifts)
    sql = f"""
    SELECT {C_PPM} AS Output_PPM FROM {tbl}
    WHERE {C_TS} >= ? AND {C_TS} < ? AND {C_PPM} > 0
    """
    df = query(conn, sql, params=[str(earliest), str(latest)])
    if len(df) == 0:
        return 100.0
    return float(np.percentile(df["Output_PPM"], 95))


def compute_shift_performance(conn, s_start, s_end, p95_ideal):
    tbl_ppm = get_table(CFG, "ppm")
    tbl_sb = get_table(CFG, "status_blocks")

    sql_ppm = f"""
    SELECT {C_TS} AS t_stamp, {C_PPM} AS Output_PPM FROM {tbl_ppm}
    WHERE {C_TS} >= ? AND {C_TS} < ? AND {C_PPM} > 0
    """
    df_ppm = query(conn, sql_ppm, params=[str(s_start), str(s_end)])
    if len(df_ppm) == 0:
        return 0.0, 0.0

    sql_run = f"""
    SELECT {C_START} AS Start_TS, {C_END} AS End_TS FROM {tbl_sb}
    WHERE ({C_RUN} = 1 OR {C_RUN} = 'true') AND {C_END} IS NOT NULL
      AND {C_START} < ? AND {C_END} > ?
    """
    df_run = query(conn, sql_run, params=[str(s_end), str(s_start)])

    df_ppm["t_stamp"] = pd.to_datetime(df_ppm["t_stamp"])
    df_run["Start_TS"] = pd.to_datetime(df_run["Start_TS"]).clip(lower=pd.Timestamp(s_start)).sort_values()
    df_run["End_TS"] = pd.to_datetime(df_run["End_TS"]).clip(upper=pd.Timestamp(s_end))
    df_run = df_run.sort_values("Start_TS").reset_index(drop=True)

    is_running = flag_running_intervals(df_ppm["t_stamp"], df_run)
    ppm_running = df_ppm.loc[is_running, "Output_PPM"]
    if len(ppm_running) == 0:
        return 0.0, 0.0

    avg_ppm = float(ppm_running.mean())
    perf_pct = avg_ppm / p95_ideal * 100 if p95_ideal > 0 else 0.0
    return perf_pct, avg_ppm


# ──────────────────────────────────────────────
# Quality
# ──────────────────────────────────────────────

def compute_shift_quality(conn, s_start, s_end):
    tbl = get_table(CFG, "output_stats")
    sql = f"""
    SELECT {C_TOTAL} AS Counter_Total, {C_GOOD} AS Counter_Good
    FROM {tbl}
    WHERE {C_TS} >= ? AND {C_TS} < ?
    ORDER BY {C_TS}
    """
    df = query(conn, sql, params=[str(s_start), str(s_end)])
    df = df.dropna(subset=["Counter_Total", "Counter_Good"])
    # Drop historian reset-edge glitches where Good > Total (accumulator desync).
    df = df[df["Counter_Good"] <= df["Counter_Total"]]
    if len(df) < 2:
        return 100.0, 0, 0

    totals = df["Counter_Total"].values
    goods = df["Counter_Good"].values

    delta_total = 0
    delta_good = 0
    seg_start_total = int(totals[0])
    seg_start_good = int(goods[0])

    for i in range(1, len(totals)):
        if int(totals[i]) < int(totals[i - 1]):
            delta_total += int(totals[i - 1]) - seg_start_total
            delta_good += int(goods[i - 1]) - seg_start_good
            seg_start_total = int(totals[i])
            seg_start_good = int(goods[i])

    delta_total += int(totals[-1]) - seg_start_total
    delta_good += int(goods[-1]) - seg_start_good

    if delta_total <= 0:
        return 100.0, 0, 0
    quality_pct = delta_good / delta_total * 100
    return quality_pct, int(delta_good), int(delta_total)


# ──────────────────────────────────────────────
# Downtime categories
# ──────────────────────────────────────────────

def compute_shift_downtime_categories(conn, s_start, s_end):
    empty = {k: {"count": 0, "hours": 0.0} for k in ("micro_stops", "halted", "auto_long", "manual")}
    df = _query_stop_blocks(conn, s_start, s_end)
    if len(df) == 0:
        return empty

    is_halted = _detect_halted_indices(conn, df, s_start, s_end)
    manual = df["StopType"].str.lower() == "manual"
    micro = (df["dur_min"] < MICRO_STOP_THRESHOLD_MIN) & ~manual & ~is_halted
    auto_long = (df["dur_min"] >= MICRO_STOP_THRESHOLD_MIN) & ~manual & ~is_halted

    def _agg(mask):
        return {"count": int(mask.sum()), "hours": round(float(df.loc[mask, "dur_min"].sum() / 60), 2)}

    return {
        "micro_stops": _agg(micro),
        "halted": _agg(is_halted),
        "auto_long": _agg(auto_long),
        "manual": _agg(manual),
    }


# ──────────────────────────────────────────────
# Downtime by segment
# ──────────────────────────────────────────────

def compute_shift_segment_downtime(conn, s_start, s_end):
    empty = {s: {"count": 0, "hours": 0.0} for s in get_segment_order()}
    df = _query_stop_blocks(conn, s_start, s_end)
    if len(df) == 0:
        return empty

    is_halted = _detect_halted_indices(conn, df, s_start, s_end)

    def _empty_cluster():
        return {"count": 0, "hours": 0.0, "halted_hours": 0.0, "stopped_hours": 0.0}

    result = {s: {"count": 0, "hours": 0.0, "halted_hours": 0.0, "stopped_hours": 0.0, "clusters": {}} for s in get_segment_order()}
    for idx, row in df.iterrows():
        seg, cluster, _ = classify_stop_row(row)
        hours = row["dur_min"] / 60.0
        halted = is_halted.loc[idx]

        result[seg]["count"] += 1
        result[seg]["hours"] += hours
        if halted:
            result[seg]["halted_hours"] += hours
        else:
            result[seg]["stopped_hours"] += hours

        if cluster is not None:
            if cluster not in result[seg]["clusters"]:
                result[seg]["clusters"][cluster] = _empty_cluster()
            result[seg]["clusters"][cluster]["count"] += 1
            result[seg]["clusters"][cluster]["hours"] += hours
            if halted:
                result[seg]["clusters"][cluster]["halted_hours"] += hours
            else:
                result[seg]["clusters"][cluster]["stopped_hours"] += hours

    for s in get_segment_order():
        for key in ("hours", "halted_hours", "stopped_hours"):
            result[s][key] = round(result[s][key], 2)
        for c in result[s]["clusters"]:
            for key in ("hours", "halted_hours", "stopped_hours"):
                result[s]["clusters"][c][key] = round(result[s]["clusters"][c][key], 2)
        if not result[s]["clusters"]:
            del result[s]["clusters"]
    return result


# ──────────────────────────────────────────────
# Hourly breakdown
# ──────────────────────────────────────────────

def compute_shift_hourly_breakdown(conn, s_start, s_end):
    tbl_sb = get_table(CFG, "status_blocks")
    tbl_fof = get_table(CFG, "fof_changes")
    fof_ndx = CFG["fof_ndx_col"]

    sql = f"""
    SELECT sb.{C_START} AS Start_TS, sb.{C_END} AS End_TS, sb.{C_RUN} AS RunningStatus, sb.{C_FAULTS} AS Faults, sb.{C_STOPTYPE} AS StopType,
           NULLIF(fof.{C_ALARM}, '') AS alarm_text
    FROM {tbl_sb} sb
    LEFT JOIN {tbl_fof} fof ON sb.{C_STARTID} = fof.{fof_ndx}
    WHERE sb.{C_START} < ? AND sb.{C_END} > ? AND sb.{C_END} IS NOT NULL
    ORDER BY sb.{C_START}
    """
    df = query(conn, sql, params=[str(s_end), str(s_start)])
    if len(df) == 0:
        return []

    df["Start_TS"] = pd.to_datetime(df["Start_TS"]).clip(lower=pd.Timestamp(s_start))
    df["End_TS"] = pd.to_datetime(df["End_TS"]).clip(upper=pd.Timestamp(s_end))

    # Precompute running flag and segment/cluster once per row — avoids repeating
    # the four-step decode inside the O(N×M) hour-bucket loop.
    row_meta = []
    for _, row in df.iterrows():
        is_run = row["RunningStatus"] == 1 or row["RunningStatus"] == "true"
        seg, cluster, _ = (None, None, None) if is_run else classify_stop_row(row)
        row_meta.append((row["Start_TS"], row["End_TS"], is_run, seg, cluster))

    shift_start = pd.Timestamp(s_start)
    hours = []
    for h in range(SHIFT_LENGTH_HOURS):
        h_start = shift_start + pd.Timedelta(hours=h)
        h_end = h_start + pd.Timedelta(hours=1)
        hour_label = h_start.strftime("%H:%M")

        running_min = 0.0
        seg_minutes = {s: 0.0 for s in get_segment_order()}
        seg_cluster_minutes = {s: {} for s in get_segment_order()}

        for r_start, r_end, is_run, seg, cluster in row_meta:
            overlap_start = max(r_start, h_start)
            overlap_end = min(r_end, h_end)
            if overlap_start >= overlap_end:
                continue
            minutes = (overlap_end - overlap_start).total_seconds() / 60.0
            if is_run:
                running_min += minutes
            else:
                seg_minutes[seg] += minutes
                if cluster:
                    seg_cluster_minutes[seg][cluster] = seg_cluster_minutes[seg].get(cluster, 0) + minutes

        seg_breakdown = {}
        for s in get_segment_order():
            if seg_minutes[s] > 0:
                clusters = {k: round(v, 1) for k, v in seg_cluster_minutes[s].items() if v > 0}
                seg_breakdown[s] = {
                    "minutes": round(seg_minutes[s], 1),
                    "clusters": clusters if clusters else None,
                }

        hours.append({
            "hour_label": hour_label,
            "running_min": round(running_min, 1),
            "segments": seg_breakdown,
        })

    return hours


# ──────────────────────────────────────────────
# Cluster profile + anomaly detection
# ──────────────────────────────────────────────

def compute_shift_cluster_profile(conn, s_start, s_end):
    """Return {segment:cluster → share} for a shift."""
    df = _query_stop_blocks(conn, s_start, s_end)
    profile = {}
    if len(df) == 0:
        return profile
    total_hours = 0.0
    for _, row in df.iterrows():
        seg, cluster, _ = classify_stop_row(row)
        cluster = cluster or seg
        key = f"{seg}:{cluster}"
        hours = row["dur_min"] / 60.0
        profile[key] = profile.get(key, 0) + hours
        total_hours += hours
    if total_hours > 0:
        profile = {k: round(v / total_hours, 4) for k, v in profile.items()}
    return profile


def detect_shift_anomalies(current_profile, baseline_profiles, threshold_z=1.8):
    """Flag clusters in current shift that deviate from baseline.

    Returns list of {cluster, segment, current_share, baseline_mean, z_score, direction}.
    """
    all_keys = set(current_profile.keys())
    for bp in baseline_profiles:
        all_keys.update(bp.keys())

    anomalies = []
    for key in all_keys:
        values = [bp.get(key, 0) for bp in baseline_profiles]
        current = current_profile.get(key, 0)
        mean = np.mean(values)
        std = np.std(values, ddof=1) if len(values) > 1 else 0.01
        if std < 0.01:
            std = 0.01
        z = (current - mean) / std
        if abs(z) >= threshold_z and (current > 0.05 or mean > 0.05):
            seg, cluster = key.split(":", 1)
            anomalies.append({
                "cluster": cluster,
                "segment": seg,
                "current_share": round(current * 100, 1),
                "baseline_mean": round(mean * 100, 1),
                "z_score": round(z, 1),
                "direction": "above" if z > 0 else "below",
            })

    anomalies.sort(key=lambda x: -abs(x["z_score"]))
    return anomalies


def build_anomaly_baseline(conn, window_shifts, oldest_shift, shift_bounds,
                           n_baseline=13, excluded=None):
    """Compute cluster profiles for window shifts + the fixed pre-window baseline.

    All shifts in the weekly window share a single fixed baseline (the n_baseline
    shifts immediately before the window's oldest shift), so anomaly baseline_mean
    values are identical across the weekly panel and individual shift dashboards.

    Args:
        conn:          DB connection
        window_shifts: list of (date, type) tuples for the current window
        oldest_shift:  (date, type) of the oldest shift in window_shifts
        shift_bounds:  [day_start_hour, night_start_hour]
        n_baseline:    how many pre-window shifts to use as baseline (default 13)
        excluded:      set of date strings to skip

    Returns:
        profile_cache:      {(date, type): profile_dict} for all computed shifts
        baseline_profiles:  ordered list of profiles for the n_baseline pre-window shifts
    """
    excluded = excluded or set()
    prior = previous_shifts(oldest_shift[0], oldest_shift[1], shift_bounds, n=n_baseline)
    all_to_compute = list(window_shifts) + prior
    cache = {}
    for sd, st in all_to_compute:
        if sd in excluded or (sd, st) in cache:
            continue
        ss, se = shift_window(sd, st, shift_bounds)
        cache[(sd, st)] = compute_shift_cluster_profile(conn, ss, se)
    baseline = [cache[k] for k in prior if k in cache]
    return cache, baseline


# ──────────────────────────────────────────────
# Stop summary + top stops
# ──────────────────────────────────────────────

def compute_shift_stop_summary(conn, s_start, s_end):
    # Use _query_stop_blocks so durations are clipped to [s_start, s_end].
    # Raw DATEDIFF(Start_TS, End_TS) overstates stops that span a shift boundary.
    df = _query_stop_blocks(conn, s_start, s_end)
    if len(df) == 0:
        return 0, 0.0, 0.0
    return int(len(df)), round(float(df["dur_min"].sum() / 60), 2), round(float(df["dur_min"].max()), 1)


def compute_shift_top_stops(conn, s_start, s_end, n_top=5):
    """Return top-N stop labels per (segment, cluster) for a single shift window."""
    df = _query_stop_blocks(conn, s_start, s_end)
    if len(df) == 0:
        return {}
    acc = {}
    for _, row in df.iterrows():
        seg, cluster, label = classify_stop_row(row)
        if cluster is None:
            continue
        entry = acc.setdefault(seg, {}).setdefault(cluster, {}).setdefault(label, [0, 0.0])
        entry[0] += 1
        entry[1] = round(entry[1] + row["dur_min"] / 60.0, 4)
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
