#!/usr/bin/env python3
"""
SPC compute functions — shared by pipelines/shift_analysis and pipelines/weekly_analysis.

Pure SPC math (_laney_p_prime, _compute_ewma, _build_spc_result) plus the
counter-reset subgroup collectors that feed them.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from core.db import hour_trunc, query, top_clause, limit_clause
from core.lines import get_line_config, get_table, get_col
from domain.shifts import assign_shift

CFG = get_line_config()

C_TOTAL = get_col(CFG, "counter_total")
C_TS = get_col(CFG, "t_stamp")

D2_CONSTANT = 1.128  # for moving range subgroup size 2
EWMA_LAMBDA = 0.2

# Counter tables on the live server hold months of high-frequency rows (tens of
# millions). Reset detection only needs recent history — enough to cover the SPC
# baseline (100 shift buckets ≈ 50 days) — so bound the scan to a recent window
# using the t_stamp index. 0 disables the bound (full history). Env-tunable.
_SPC_LOOKBACK_DAYS = int(os.environ.get("SPC_LOOKBACK_DAYS", "75"))


# ──────────────────────────────────────────────
# Pure SPC math
# ──────────────────────────────────────────────

def _laney_p_prime_params(d_vals, n_vals):
    """Compute Laney p' baseline parameters (p_bar, sigma_z) for a single lane.

    d_vals: list of defect counts per subgroup
    n_vals: list of inspection counts per subgroup (same length)

    Returns (p_bar, sigma_z) — the two parameters needed to compute UCL/LCL for
    any subgroup drawn from this process.  This is the single source of truth for
    Laney p' math shared between _laney_p_prime (per-shift) and the series builder
    in pipelines/weekly_analysis.py.
    """
    total_d = sum(d_vals)
    total_n = sum(n_vals)
    p_bar = total_d / total_n if total_n > 0 else 0

    z_vals = []
    for d, n in zip(d_vals, n_vals):
        if n > 0 and p_bar > 0:
            se = (p_bar * (1 - p_bar) / n) ** 0.5
            z_vals.append((d / n - p_bar) / se if se > 0 else 0)
        else:
            z_vals.append(0)

    mr_vals = [abs(z_vals[i] - z_vals[i - 1]) for i in range(1, len(z_vals))]
    mr_bar = float(np.mean(mr_vals)) if mr_vals else 1.0
    sigma_z = max(mr_bar / D2_CONSTANT, 0.01)

    return p_bar, sigma_z


def _laney_p_prime(baseline, current, lane_keys):
    """Compute Laney p' statistics for a set of lanes.

    baseline: list of dicts with "n_inspected" and lane keys (defect counts)
    current: single dict, same structure
    lane_keys: list of keys like ["n1", "n2", ...] or ["lane1", "lane2", ...]

    Returns list of per-lane result dicts.
    """
    results = []
    for key in lane_keys:
        d_vals = [s[key] for s in baseline]
        n_vals = [s["n_inspected"] for s in baseline]

        p_bar, sigma_z = _laney_p_prime_params(d_vals, n_vals)

        n_current = current["n_inspected"]
        se_current = (p_bar * (1 - p_bar) / n_current) ** 0.5 if n_current > 0 and p_bar > 0 else 0
        ucl = p_bar + 3 * sigma_z * se_current
        lcl = max(0.0, p_bar - 3 * sigma_z * se_current)

        current_rate = current[key] / n_current if n_current > 0 else 0

        results.append({
            "rejects": current[key],
            "inspected": round(n_current),
            "rate_pct": round(current_rate * 100, 2),
            "p_bar_pct": round(p_bar * 100, 2),
            "ucl_pct": round(ucl * 100, 2),
            "lcl_pct": round(lcl * 100, 2),
            "out_of_control": current_rate > ucl,
        })

    return results


def _compute_ewma(subgroups, lane_keys, baseline_subgroups=None):
    """Compute EWMA trending for each lane across subgroups.

    baseline_subgroups: batches used for mean/std (UCL/LCL) estimation.
    Defaults to subgroups. Pass pre-window batches so UCL/LCL are computed
    from the same historical reference as the Laney p' baseline.

    Returns dict with "shifts" (list of {t_stamp, rates, ewma}) and
    "ucl"/"lcl" per lane (steady-state limits).
    """
    lam = EWMA_LAMBDA
    n_lanes = len(lane_keys)

    shift_data = []
    for s in subgroups:
        n = s["n_inspected"]
        rates = [(s[k] / n * 100) if n > 0 else 0 for k in lane_keys]
        shift_data.append({"t_stamp": s["t_stamp"], "rates": rates})

    if len(shift_data) < 3:
        return None

    # UCL/LCL from stable pre-period reference; fall back to displayed window.
    bl_src = baseline_subgroups if (baseline_subgroups is not None and len(baseline_subgroups) >= 3) else subgroups
    bl_data = [[(s[k] / s["n_inspected"] * 100) if s["n_inspected"] > 0 else 0
                for k in lane_keys] for s in bl_src]
    all_rates = [[bd[i] for bd in bl_data] for i in range(n_lanes)]
    means = [np.mean(r) for r in all_rates]
    stds = [max(np.std(r, ddof=1), 0.01) if len(r) > 1 else 0.01 for r in all_rates]

    L = 2.7
    factor = (lam / (2 - lam)) ** 0.5
    ucls = [round(means[i] + L * stds[i] * factor, 2) for i in range(n_lanes)]
    lcls = [round(max(0.0, means[i] - L * stds[i] * factor), 2) for i in range(n_lanes)]

    ewma_series = [[0.0] * len(shift_data) for _ in range(n_lanes)]
    for lane_i in range(n_lanes):
        ewma_series[lane_i][0] = shift_data[0]["rates"][lane_i]
        for t in range(1, len(shift_data)):
            ewma_series[lane_i][t] = round(
                lam * shift_data[t]["rates"][lane_i] + (1 - lam) * ewma_series[lane_i][t - 1], 3
            )

    shifts_out = []
    for t, sd in enumerate(shift_data):
        shifts_out.append({
            "t_stamp": sd["t_stamp"],
            "rates": [round(r, 2) for r in sd["rates"]],
            "ewma": [ewma_series[i][t] for i in range(n_lanes)],
        })

    return {
        "shifts": shifts_out,
        "ucl": ucls,
        "lcl": lcls,
        "lambda": lam,
    }


def _build_spc_result(subgroups, lane_labels, lane_keys, n_baseline=100,
                      cutoff_ts=None, shift_start_ts=None):
    """Run Laney p' + EWMA on collected subgroups.

    cutoff_ts: upper bound — only subgroups up to this timestamp are used.
    shift_start_ts: when provided, all batches within [shift_start, cutoff] are
    aggregated into one "current" observation (matching the weekly shift-aggregate
    SPC).  Batches before shift_start form the baseline.  Without shift_start_ts
    the last single batch is used as current (legacy behaviour).
    """
    if cutoff_ts:
        cutoff_str = cutoff_ts if isinstance(cutoff_ts, str) else str(cutoff_ts)
        subgroups = [s for s in subgroups if s["t_stamp"] <= cutoff_str]
    if len(subgroups) < 10:
        return None

    from datetime import datetime as _dt

    if shift_start_ts and cutoff_ts:
        start_str = shift_start_ts if isinstance(shift_start_ts, str) else str(shift_start_ts)
        shift_subs = [s for s in subgroups if s["t_stamp"] >= start_str[:19]]
        pre_shift   = [s for s in subgroups if s["t_stamp"] <  start_str[:19]]

        if not shift_subs:
            # No batches in this shift window at all.
            gap_h = (_dt.fromisoformat(cutoff_str[:19]) - _dt.fromisoformat(subgroups[-1]["t_stamp"][:19])).total_seconds() / 3600
            return {
                "status": "no_shift_data",
                "last_reset_timestamp": subgroups[-1]["t_stamp"],
                "gap_hours": round(gap_h, 1),
            }

        # Aggregate all in-shift batches into one observation.
        total_n = sum(s["n_inspected"] for s in shift_subs)
        current = {"t_stamp": shift_subs[-1]["t_stamp"], "n_inspected": total_n}
        for key in lane_keys:
            current[key] = sum(s.get(key, 0) for s in shift_subs)

        baseline = pre_shift[-n_baseline:] if len(pre_shift) >= 10 else pre_shift
        if len(baseline) < 10:
            # Fall back: use all pre-cutoff batches excluding shift batches
            baseline = subgroups[:-(len(shift_subs))]
            baseline = baseline[-n_baseline:]
    else:
        # Legacy: last single batch as current.
        gap_h = (_dt.fromisoformat(cutoff_str[:19]) - _dt.fromisoformat(subgroups[-1]["t_stamp"][:19])).total_seconds() / 3600 if cutoff_ts else 0
        if gap_h > 12:
            return {
                "status": "no_shift_data",
                "last_reset_timestamp": subgroups[-1]["t_stamp"],
                "gap_hours": round(gap_h, 1),
            }
        baseline = subgroups[-(n_baseline + 1):-1] if len(subgroups) > n_baseline else subgroups[:-1]
        current = subgroups[-1]

    if len(baseline) < 2:
        return None

    lane_results = _laney_p_prime(baseline, current, lane_keys)
    for lr, label in zip(lane_results, lane_labels):
        lr["lane"] = label

    ewma = _compute_ewma(subgroups[-30:], lane_keys)

    return {
        "lanes": lane_results,
        "baseline_subgroups": len(baseline),
        "baseline_subgroups_requested": n_baseline,
        "last_reset_timestamp": str(current["t_stamp"]),
        "ewma": ewma,
    }


# ──────────────────────────────────────────────
# Shared baseline builder
# ──────────────────────────────────────────────

def build_shift_baseline(subgroups, lane_keys, n_baseline, cutoff_str, shift_bounds, excluded_days=None):
    """Aggregate raw counter-reset subgroups into per-shift buckets before cutoff_str.

    Returns the last n_baseline qualifying shift buckets (n_inspected >= 100).
    Shared by shift and weekly pipelines so p_bar is computed from the same
    pre-period historical reference regardless of which dashboard is rendering.
    """
    buckets = {}
    for sg in subgroups:
        ts = sg["t_stamp"][:19]
        if ts >= cutoff_str:
            continue
        if excluded_days and ts[:10] in excluded_days:
            continue
        stype, sdate = assign_shift(ts, shift_bounds)
        skey = (sdate, stype)
        if skey not in buckets:
            buckets[skey] = {"t_stamp": ts, "n_inspected": 0, **{k: 0 for k in lane_keys}}
        buckets[skey]["n_inspected"] += sg["n_inspected"]
        for k in lane_keys:
            buckets[skey][k] = buckets[skey].get(k, 0) + sg.get(k, 0)

    shift_baseline = sorted(buckets.values(), key=lambda s: s["t_stamp"])
    shift_baseline = [s for s in shift_baseline if s["n_inspected"] >= 100]
    return shift_baseline[-n_baseline:]


# ──────────────────────────────────────────────
# Counter-reset subgroup collectors
# ──────────────────────────────────────────────

def _shift_boundaries_between(t1, t2, shift_boundaries):
    """Return shift boundary datetimes strictly between t1 and t2, in order."""
    from datetime import timedelta
    day_h, night_h = shift_boundaries
    boundaries = []
    d = t1.normalize()
    while d <= t2:
        for h in [day_h, night_h]:
            b = d.replace(hour=h, minute=0, second=0, microsecond=0)
            if t1 < b < t2:
                boundaries.append(b)
        d += timedelta(days=1)
    return sorted(boundaries)


def _lanes_at_block(conn, tbl, name_col, counter_col, counter_pattern, n_expected, block_id):
    """Get {lane_name: count} at a specific block_id. Returns None on mismatch or NULL."""
    df = query(conn, f"""
    SELECT {name_col} AS name, {counter_col} AS cnt
    FROM {tbl}
    WHERE block_id = ? AND {name_col} LIKE ?
    """, params=[block_id, counter_pattern])
    if len(df) != n_expected or df["cnt"].isna().any():
        return None
    return {str(r["name"]): int(r["cnt"]) for _, r in df.iterrows()}


def _bulk_lanes_at_blocks(conn, tbl, name_col, counter_col, counter_pattern, n_expected, block_ids):
    """One-shot version of _lanes_at_block for many blocks: {block_id: {lane: cnt} | None}.

    block_id is not indexed (the clustered key is the ndx column), so a per-block
    lookup is a full-table scan. Fetching every needed block in a single IN query
    turns hundreds of scans into one — the key SPC speed-up against production.
    """
    ids = sorted({int(b) for b in block_ids})
    if not ids:
        return {}
    in_list = ",".join(str(b) for b in ids)   # ints only — safe to inline
    df = query(conn, f"""
    SELECT block_id, {name_col} AS name, {counter_col} AS cnt
    FROM {tbl}
    WHERE block_id IN ({in_list}) AND {name_col} LIKE ?
    """, params=[counter_pattern])
    cache = {}
    for bid, g in df.groupby("block_id"):
        if len(g) != n_expected or g["cnt"].isna().any():
            cache[int(bid)] = None
        else:
            cache[int(bid)] = {str(r["name"]): int(r["cnt"]) for _, r in g.iterrows()}
    return cache


def _lanes_at_last_block_before(conn, tbl, name_col, counter_col, rep_name,
                                counter_pattern, n_expected, before_ts_str):
    """Get {lane_name: count} at the last block strictly before before_ts_str.

    Slow-path fallback only (see _bulk_block_ts_index) -- block_id isn't indexed,
    so `ORDER BY block_id DESC` here is a full-table sort against tens of millions
    of rows. Every real caller should resolve the boundary block via the bulk
    timestamp index first and only fall back to this if that lookup missed.
    """
    df_bk = query(conn, f"""
    SELECT {top_clause(1)} block_id FROM {tbl}
    WHERE {name_col} = ? AND t_stamp < ?
    ORDER BY block_id DESC
    {limit_clause(1)}
    """, params=[rep_name, before_ts_str])
    if df_bk.empty:
        return None
    return _lanes_at_block(conn, tbl, name_col, counter_col, counter_pattern,
                           n_expected, int(df_bk.iloc[0, 0]))


def _bulk_block_ts_index(conn, tbl, name_col, rep_name, floor_ts_str=None):
    """One-shot (block_id, t_stamp) index for a representative lane name, sorted
    by block_id -- turns "last block before timestamp X" into an in-memory
    binary search instead of a per-call `ORDER BY block_id DESC` full scan.

    t_stamp has a nonclustered index and block_id doesn't, so grouping by
    block_id while filtering/bounding on t_stamp stays a single indexed-range
    scan even against 16M+ row counter tables.
    """
    where = f"WHERE {name_col} = ?"
    params = [rep_name]
    if floor_ts_str:
        where += " AND t_stamp >= ?"
        params.append(floor_ts_str)
    df = query(conn, f"""
    SELECT block_id, MIN(t_stamp) AS t_stamp
    FROM {tbl}
    {where}
    GROUP BY block_id
    """, params=params)
    if df.empty:
        return df
    df["t_stamp"] = pd.to_datetime(df["t_stamp"])
    return df.sort_values("block_id").reset_index(drop=True)


def _last_block_before_ts(index_df, before_ts_str):
    """Binary-search a _bulk_block_ts_index frame for the last block_id whose
    t_stamp is strictly before before_ts_str. Returns None if there isn't one."""
    if index_df is None or index_df.empty:
        return None
    before_ts = pd.Timestamp(before_ts_str)
    pos = index_df["t_stamp"].searchsorted(before_ts, side="left") - 1
    if pos < 0:
        return None
    return int(index_df["block_id"].iloc[pos])


def _precompute_boundary_timestamps(reset_rows, shift_boundaries):
    """Every shift-boundary timestamp _collect_subgroups_core will need for
    straddle segments, computed purely from the reset rows already in hand (no
    DB call) so the boundary block lookups can be bulk-resolved up front."""
    needed_ts = set()
    if not shift_boundaries:
        return needed_ts
    for i in range(1, len(reset_rows)):
        bs = _shift_boundaries_between(
            pd.Timestamp(reset_rows[i - 1].t_stamp), pd.Timestamp(reset_rows[i].t_stamp), shift_boundaries
        )
        for b in bs:
            needed_ts.add(b.strftime("%Y-%m-%d %H:%M:%S"))
    return needed_ts


def _resolve_boundary_blocks(conn, tbl, name_col, rep_name, reset_rows, shift_boundaries):
    """Bulk-resolve every straddle-boundary timestamp to its block_id in one
    indexed-range query + in-memory binary search.

    Returns (ts_to_block, boundary_ts_set) -- ts_to_block maps the exact
    timestamp strings _collect_subgroups_core will ask for to a block_id (only
    for timestamps that resolved); boundary_ts_set is every timestamp that was
    requested, so callers can add all resolved block_ids to their bulk lane
    prefetch in a single combined query.
    """
    boundary_ts = _precompute_boundary_timestamps(reset_rows, shift_boundaries)
    ts_to_block = {}
    if not boundary_ts or not reset_rows:
        return ts_to_block, boundary_ts
    floor_ts_str = (pd.Timestamp(reset_rows[0].t_stamp) - pd.Timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    idx = _bulk_block_ts_index(conn, tbl, name_col, rep_name, floor_ts_str=floor_ts_str)
    for ts in boundary_ts:
        b = _last_block_before_ts(idx, ts)
        if b is not None:
            ts_to_block[ts] = b
    return ts_to_block, boundary_ts


def _output_max_in_epoch(conn, tbl_out, from_ts_str, to_ts_str, strict_lt_end=False):
    """MAX Counter_Total in [from_ts, to_ts] — the pre-reset-max for a segment.

    Counter_Total resets to 0 at shift boundaries AND at batch/order changes.
    Using MAX over the epoch window captures the peak before the reset rather
    than the post-reset value that a most-recent-row lookup would return.
    """
    end_op = "<" if strict_lt_end else "<="
    df = query(conn, f"""
    SELECT MAX({C_TOTAL}) AS peak FROM {tbl_out}
    WHERE {C_TS} >= ? AND {C_TS} {end_op} ? AND {C_TOTAL} > 0
    """, params=[from_ts_str, to_ts_str])
    if df.empty or df.iloc[0, 0] is None:
        return 0
    return int(df.iloc[0, 0])


def _detect_counter_resets(conn, tbl, name_col, counter_col, reset_name, min_resets=10):
    """Return rows where counter_col decremented vs the previous block_id row.

    Shared by all SPC collectors — the reset-detection SQL is identical regardless
    of how lanes are read from the block data.  Returns None when fewer than
    min_resets are found (not enough signal to build subgroups).

    Bounded to the last _SPC_LOOKBACK_DAYS (via the t_stamp index) so it stays fast
    against the full production history while still covering the SPC baseline.
    """
    where = f"WHERE {name_col} = ?"
    params = [reset_name]
    if _SPC_LOOKBACK_DAYS > 0:
        mx = query(conn, f"SELECT MAX({C_TS}) AS m FROM {tbl}")   # uses t_stamp index
        if not mx.empty and mx.iloc[0, 0] is not None:
            floor = (pd.Timestamp(mx.iloc[0, 0]) - pd.Timedelta(days=_SPC_LOOKBACK_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
            where += f" AND {C_TS} >= ?"
            params.append(floor)
    # Filter the reset rows SERVER-SIDE: the LAG comparison happens in SQL and only
    # the (few dozen) decrement rows cross the network — not the millions of raw
    # counter samples the outer query would otherwise transfer to pandas.
    df = query(conn, f"""
    SELECT block_id, t_stamp, cnt, prev_cnt FROM (
        SELECT block_id, t_stamp, {counter_col} AS cnt,
               LAG({counter_col}) OVER (ORDER BY block_id) AS prev_cnt
        FROM {tbl}
        {where}
    ) q
    WHERE q.cnt < q.prev_cnt
    ORDER BY q.block_id
    """, params=params)
    return df if len(df) >= min_resets else None


def _process_straddle_segments(conn, checkpoints, tbl_out, n_lanes_divisor):
    """Yield subgroup dicts for each (start, end) checkpoint pair in a straddle batch.

    checkpoints: list of (ts_str, lanes_dict | None) from batch_start through boundaries
    to batch_end.  Lane values are deltas (absolute counters accumulate across boundaries).
    Non-last segment timestamps are nudged 1 s before the boundary so weekly aggregation
    assigns them to the correct pre-boundary shift.
    """
    result = []
    for j in range(len(checkpoints) - 1):
        seg_ts_start, seg_lanes_start = checkpoints[j]
        seg_ts_end, seg_lanes_end = checkpoints[j + 1]
        if seg_lanes_start is None or seg_lanes_end is None:
            continue
        is_last = j == len(checkpoints) - 2
        n_peak = _output_max_in_epoch(conn, tbl_out, seg_ts_start, seg_ts_end,
                                      strict_lt_end=not is_last)
        n_per_lane = n_peak / n_lanes_divisor
        if n_per_lane < 100:
            continue
        stamp = seg_ts_end if is_last else (
            pd.Timestamp(seg_ts_end) - pd.Timedelta(seconds=1)
        ).strftime("%Y-%m-%d %H:%M:%S")
        row = {"t_stamp": stamp, "n_inspected": n_per_lane}
        for key, end_cnt in seg_lanes_end.items():
            row[key] = max(0, end_cnt - seg_lanes_start.get(key, 0))
        result.append(row)
    return result


def _resolve_epoch_start(prev_ts, reset_ts, shift_boundaries):
    """Return the epoch start string for a standard (non-straddle) subgroup.

    Epoch start is the later of the previous batch reset and the most recent
    shift boundary before reset_ts.  MAX(Counter_Total) over this epoch captures
    the peak before any within-epoch reset without being diluted by prior shifts.
    """
    epoch_start = str(prev_ts)[:19] if prev_ts else None
    if shift_boundaries and epoch_start:
        prev_bs = _shift_boundaries_between(pd.Timestamp("1900-01-01"), reset_ts, shift_boundaries)
        if prev_bs:
            last_boundary = prev_bs[-1].strftime("%Y-%m-%d %H:%M:%S")
            epoch_start = max(epoch_start, last_boundary)
    return epoch_start


def _collect_subgroups_core(
    conn, reset_rows, fetch_lanes_at_block, fetch_lanes_at_boundary,
    tbl_out, n_lanes_divisor, excluded_days=None, shift_boundaries=None,
    min_subgroups=10,
):
    """Core subgroup assembly shared by all SPC collectors.

    Callers supply two callables encapsulating their table's lane-read logic:
      fetch_lanes_at_block(block_id)         -> dict {lane_key: int} | None
      fetch_lanes_at_boundary(before_ts_str) -> dict {lane_key: int} | None

    Standard path: one subgroup per batch reset; lane value = absolute counter
    at prev_block (valid because counters start from 0 at each batch reset).
    Straddle path: batch crosses a shift boundary; produces one subgroup per
    checkpoint segment via _process_straddle_segments.
    """
    reset_rows = list(reset_rows)
    subgroups = []

    for i, r in enumerate(reset_rows):
        prev_block = int(r.block_id) - 1
        reset_ts = pd.Timestamp(r.t_stamp)
        reset_ts_str = reset_ts.strftime("%Y-%m-%d %H:%M:%S")

        if excluded_days and reset_ts_str[:10] in excluded_days:
            continue

        boundaries = (
            _shift_boundaries_between(pd.Timestamp(reset_rows[i - 1].t_stamp), reset_ts, shift_boundaries)
            if shift_boundaries and i > 0 else []
        )

        if boundaries:
            batch_start_str = pd.Timestamp(reset_rows[i - 1].t_stamp).strftime("%Y-%m-%d %H:%M:%S")
            checkpoints = [(batch_start_str, fetch_lanes_at_block(int(reset_rows[i - 1].block_id)))]
            for b in boundaries:
                checkpoints.append((b.strftime("%Y-%m-%d %H:%M:%S"), fetch_lanes_at_boundary(b.strftime("%Y-%m-%d %H:%M:%S"))))
            checkpoints.append((reset_ts_str, fetch_lanes_at_block(prev_block)))
            subgroups.extend(_process_straddle_segments(conn, checkpoints, tbl_out, n_lanes_divisor))
            continue

        end_lanes = fetch_lanes_at_block(prev_block)
        if end_lanes is None:
            continue

        epoch_start_str = _resolve_epoch_start(
            reset_rows[i - 1].t_stamp if i > 0 else None, reset_ts, shift_boundaries
        ) or reset_ts_str
        total_per_lane = _output_max_in_epoch(conn, tbl_out, epoch_start_str, reset_ts_str) / n_lanes_divisor
        if total_per_lane < 100:
            continue

        subgroups.append({"t_stamp": reset_ts_str, "n_inspected": total_per_lane, **end_lanes})

    return subgroups if len(subgroups) >= min_subgroups else None


def _latest_reading(conn, tbl, name_col, counter_col, name):
    """(block_id, t_stamp) of the most recent row for one representative counter
    name. Used only to detect whether a batch has run past the last reset for
    longer than usual — see _add_open_batch_subgroup.
    """
    df = query(conn, f"""
    SELECT {top_clause(1)} block_id, t_stamp FROM {tbl}
    WHERE {name_col} = ?
    ORDER BY t_stamp DESC
    {limit_clause(1)}
    """, params=[name])
    if df.empty:
        return None
    return int(df.iloc[0]["block_id"]), df.iloc[0]["t_stamp"]


def _add_open_batch_subgroup(conn, subgroups, reset_rows, tbl, name_col, counter_col,
                             rep_name, fetch_lanes_at_block, tbl_out, n_lanes_divisor,
                             shift_boundaries=None, excluded_days=None):
    """Append one provisional subgroup for a batch that is still running (hasn't
    reset yet), so a long-running batch doesn't withhold SPC data indefinitely.

    Without this, a subgroup only exists once a counter reset closes a batch —
    correct for a complete count, but it means a station whose current batch
    runs longer than a shift (confirmed on Vision: batches sometimes span 20+
    hours with no reset) shows nothing at all for that stretch, even though
    inspection is actively happening. This reads the counter's current
    (not-yet-reset) value as a stand-in for the closing reset, the same way a
    real subgroup reads the value at the block right before the *next* reset —
    the counter counts up from 0 either way, so "value now" and "value at the
    next reset" are the same kind of number, just observed at different times.

    Deliberately shaped identically to a normal subgroup (same keys, no
    "provisional" marker) so every downstream consumer — baseline building,
    Laney p', EWMA, Gold schema — needs no changes. The real tradeoff is
    business, not technical: this reading will typically be *lower* than the
    eventual closed-batch count, since inspection is still in progress.
    """
    if subgroups is None or not reset_rows:
        return subgroups

    latest = _latest_reading(conn, tbl, name_col, counter_col, rep_name)
    if latest is None:
        return subgroups
    latest_block, latest_ts = latest
    last_reset = reset_rows[-1]
    if latest_block <= int(last_reset.block_id):
        return subgroups   # no rows past the last reset — nothing open yet

    ts_str = str(latest_ts)[:19]
    if excluded_days and ts_str[:10] in excluded_days:
        return subgroups

    end_lanes = fetch_lanes_at_block(latest_block)
    if end_lanes is None:
        return subgroups

    epoch_start_str = _resolve_epoch_start(
        last_reset.t_stamp, pd.Timestamp(latest_ts), shift_boundaries
    ) or str(last_reset.t_stamp)[:19]
    total_per_lane = _output_max_in_epoch(conn, tbl_out, epoch_start_str, ts_str) / n_lanes_divisor
    if total_per_lane < 100:
        return subgroups   # too little inspected so far to be a meaningful reading

    subgroups.append({"t_stamp": ts_str, "n_inspected": total_per_lane, **end_lanes})
    return subgroups


def _collect_counter_subgroups(conn, table_key, counter_col, name_col,
                               counter_pattern, n_expected, n_lanes_divisor,
                               excluded_days=None, shift_boundaries=None):
    """Thin wrapper: single-pattern counter table → _collect_subgroups_core."""
    tbl = get_table(CFG, table_key)
    if not tbl:
        return None

    df_sample = query(conn, f"""
    SELECT {top_clause(1)} {name_col} AS name FROM {tbl}
    WHERE {name_col} LIKE ? ORDER BY row_id
    {limit_clause(1)}
    """, params=[counter_pattern])
    if len(df_sample) == 0:
        return None
    rep_name = df_sample.iloc[0, 0]

    resets = _detect_counter_resets(conn, tbl, name_col, counter_col, rep_name)
    if resets is None:
        return None

    reset_rows = list(resets.itertuples(index=False))
    # Pre-fetch every block the standard path will read (reset block + its predecessor)
    # in one query, so per-subgroup lane reads become dict lookups instead of scans.
    needed = set()
    for r in reset_rows:
        needed.add(int(r.block_id))
        needed.add(int(r.block_id) - 1)

    # Straddle-segment boundary blocks: resolve every needed timestamp to a
    # block_id in one indexed-range query (was previously one unindexed
    # `ORDER BY block_id DESC` scan per boundary -- the confirmed multi-hour
    # bottleneck), then fold those block_ids into the same bulk lane prefetch.
    ts_to_block, _ = _resolve_boundary_blocks(conn, tbl, name_col, rep_name, reset_rows, shift_boundaries)
    needed.update(ts_to_block.values())

    cache = _bulk_lanes_at_blocks(conn, tbl, name_col, counter_col, counter_pattern, n_expected, needed)

    def fetch_lanes_at_block(block_id):
        b = int(block_id)
        if b in cache:
            return cache[b]
        return _lanes_at_block(conn, tbl, name_col, counter_col, counter_pattern, n_expected, b)

    def fetch_lanes_at_boundary(before_ts_str):
        b = ts_to_block.get(before_ts_str)
        if b is not None and b in cache:
            return cache[b]
        return _lanes_at_last_block_before(conn, tbl, name_col, counter_col,
                                           rep_name, counter_pattern, n_expected, before_ts_str)

    subgroups = _collect_subgroups_core(
        conn, reset_rows,
        fetch_lanes_at_block, fetch_lanes_at_boundary,
        get_table(CFG, "output_stats"), n_lanes_divisor, excluded_days, shift_boundaries,
    )
    return _add_open_batch_subgroup(
        conn, subgroups, reset_rows, tbl, name_col, counter_col, rep_name,
        fetch_lanes_at_block, get_table(CFG, "output_stats"), n_lanes_divisor,
        shift_boundaries, excluded_days,
    )


def _sum_camera_lanes(df_all, n_expected=10):
    """Sum non-NULL counter values per physical lane across both camera stations.

    Each physical lane is measured by two stations (Stn012 rear, Stn031 front).
    In normal operation one station reports the real count and the other reports 0.
    During historian logging interruptions one station may log NULL instead of 0 —
    summing non-NULLs recovers the real count without double-counting.

    Returns a dict {lane_num: total_count} or None if any lane is completely
    missing (all stations NULL for that lane).
    """
    lane_sums = {}
    for _, nr in df_all.iterrows():
        lane_num = int(nr["name"].split("_")[-1])
        if pd.isna(nr["cnt"]):
            continue
        lane_sums[lane_num] = lane_sums.get(lane_num, 0) + int(nr["cnt"])
    if len(lane_sums) < n_expected:
        return None
    return lane_sums


def _bulk_camera_lanes_at_blocks(conn, tbl, name_col, counter_col, rear_pattern,
                                  front_pattern, n_expected, block_ids):
    """One-shot version of the camera dual-station block read for many blocks:
    {block_id: {laneN: cnt} | None}. Same one-IN-query idea as
    _bulk_lanes_at_blocks, adapted for the rear+front pattern and lane summing.
    """
    ids = sorted({int(b) for b in block_ids})
    if not ids:
        return {}
    in_list = ",".join(str(b) for b in ids)
    df = query(conn, f"""
    SELECT block_id, {name_col} AS name, {counter_col} AS cnt
    FROM {tbl}
    WHERE block_id IN ({in_list}) AND ({name_col} LIKE ? OR {name_col} LIKE ?)
    """, params=[rear_pattern, front_pattern])
    cache = {}
    for bid, g in df.groupby("block_id"):
        if len(g) < n_expected:
            cache[int(bid)] = None
            continue
        lane_sums = _sum_camera_lanes(g, n_expected=n_expected)
        cache[int(bid)] = None if lane_sums is None else {f"lane{k}": v for k, v in lane_sums.items()}
    return cache


def _collect_camera_dual(conn, params, excluded_days=None, shift_boundaries=None):
    """Dual-station camera collector.

    Two stations measure the same physical lanes (rear + front cameras).
    Each subgroup sums non-NULL counts per lane across both stations via
    _sum_camera_lanes.  Straddle detection, epoch-max Counter_Total, and the
    1-second boundary nudge are handled by _collect_subgroups_core — identical
    behaviour to the single-pattern collector.
    """
    tbl = get_table(CFG, params["table_key"])
    if not tbl:
        return None
    tbl_out = get_table(CFG, "output_stats")
    counter_col  = params["counter_col"]
    name_col     = params["name_col"]
    rear_pattern = params["rear_pattern"]
    front_pattern = params["front_pattern"]
    reset_name   = params["reset_name"]
    divisor      = params.get("divisor", 10)
    n_expected   = params.get("n_expected", 10)
    min_subgroups = params.get("min_subgroups", 5)

    resets = _detect_counter_resets(conn, tbl, name_col, counter_col, reset_name, min_resets=min_subgroups)
    if resets is None:
        return None

    reset_rows = list(resets.itertuples(index=False))
    # Same bulk-prefetch treatment as _collect_counter_subgroups: reset/prev
    # blocks plus every straddle-boundary block, all in one combined IN query,
    # instead of a per-block scan and a per-boundary unindexed sort (the two
    # confirmed multi-hour bottlenecks -- this collector previously had neither
    # optimization applied).
    needed = set()
    for r in reset_rows:
        needed.add(int(r.block_id))
        needed.add(int(r.block_id) - 1)
    ts_to_block, _ = _resolve_boundary_blocks(conn, tbl, name_col, reset_name, reset_rows, shift_boundaries)
    needed.update(ts_to_block.values())

    cache = _bulk_camera_lanes_at_blocks(conn, tbl, name_col, counter_col, rear_pattern,
                                          front_pattern, n_expected, needed)

    def _fetch_at_block(block_id):
        b = int(block_id)
        if b in cache:
            return cache[b]
        df_all = query(conn, f"""
        SELECT {name_col} AS name, {counter_col} AS cnt
        FROM {tbl}
        WHERE block_id = ?
          AND ({name_col} LIKE ? OR {name_col} LIKE ?)
        """, params=[b, rear_pattern, front_pattern])
        if len(df_all) < n_expected:
            return None
        lane_sums = _sum_camera_lanes(df_all, n_expected=n_expected)
        if lane_sums is None:
            return None
        return {f"lane{k}": v for k, v in lane_sums.items()}

    def _fetch_at_boundary(before_ts_str):
        b = ts_to_block.get(before_ts_str)
        if b is not None and b in cache:
            return cache[b]
        df_bk = query(conn, f"""
        SELECT {top_clause(1)} block_id FROM {tbl}
        WHERE {name_col} = ? AND t_stamp < ?
        ORDER BY block_id DESC
        {limit_clause(1)}
        """, params=[reset_name, before_ts_str])
        if df_bk.empty:
            return None
        return _fetch_at_block(int(df_bk.iloc[0, 0]))

    subgroups = _collect_subgroups_core(
        conn, reset_rows,
        _fetch_at_block, _fetch_at_boundary,
        tbl_out, divisor, excluded_days, shift_boundaries,
        min_subgroups=min_subgroups,
    )
    return _add_open_batch_subgroup(
        conn, subgroups, reset_rows, tbl, name_col, counter_col, reset_name,
        _fetch_at_block, tbl_out, divisor, shift_boundaries, excluded_days,
    )


# ──────────────────────────────────────────────
# Config-driven SPC dispatch
# ──────────────────────────────────────────────

# Registry of non-standard collectors. The "single" case (one counter table,
# one LIKE pattern, fixed lane count) is handled directly by the dispatcher via
# _collect_counter_subgroups. Anything more exotic (dual-station, derived lanes)
# registers a named collector here and references it from spc_panels[].collector.
CUSTOM_SPC_COLLECTORS = {
    "camera_dual": _collect_camera_dual,
}


def collect_spc_subgroups(conn, cfg, panel, excluded):
    """Collect raw counter-reset subgroups for an SPC panel (config-driven).

    Returns the raw subgroup list (or None). Lane-key derivation and result
    building stay with the calling pipeline, because the shift dashboard uses a
    current-batch Laney p' (_build_spc_result) while the weekly dashboard uses a
    shift-level series (_build_weekly_spc).
    """
    collector = panel.get("collector", "single")
    if collector == "single":
        return _collect_counter_subgroups(
            conn,
            table_key=panel["table_key"],
            counter_col=panel["counter_col"],
            name_col=panel["name_col"],
            counter_pattern=panel["pattern"],
            n_expected=panel["n_expected"],
            n_lanes_divisor=panel["divisor"],
            excluded_days=excluded,
            shift_boundaries=cfg.get("shift_boundaries"),
        )
    fn = CUSTOM_SPC_COLLECTORS.get(collector)
    if fn is None:
        raise KeyError(
            f"Unknown SPC collector {collector!r}; register it in CUSTOM_SPC_COLLECTORS"
        )
    return fn(conn, panel.get("params", {}), excluded_days=excluded,
              shift_boundaries=cfg.get("shift_boundaries"))


def derive_spc_lanes(panel, subgroups, active_subgroups=None):
    """Return (lane_keys, lane_labels) for a panel given its collected subgroups.

    Modes:
      - "name_template": keys built from a counter-name template (1..n_expected).
      - "discovered_sorted": keys are the sorted data keys of the first subgroup.
      - "synthetic": keys are <lane_key_prefix><i> for 1..n_expected, optionally
        filtered to lanes that ever fire (active_filter) within active_subgroups.
    """
    mode = panel["lane_key_mode"]
    if mode == "name_template":
        n = panel["n_expected"]
        keys = [panel["name_template"].format(i=i) for i in range(1, n + 1)]
        return keys, list(range(1, n + 1))
    if mode == "discovered_sorted":
        keys = sorted(k for k in subgroups[0].keys() if k not in ("t_stamp", "n_inspected"))
        return keys, list(range(1, len(keys) + 1))
    if mode == "synthetic":
        prefix = panel["lane_key_prefix"]
        n = panel["n_expected"]
        all_keys = [f"{prefix}{i}" for i in range(1, n + 1)]
        if panel.get("active_filter"):
            scope = active_subgroups if active_subgroups is not None else subgroups
            keys = [k for k in all_keys if any(s.get(k, 0) > 0 for s in scope)]
        else:
            keys = all_keys
        return keys, [int(k.replace(prefix, "")) for k in keys]
    raise ValueError(f"Unknown lane_key_mode {mode!r}")


# ──────────────────────────────────────────────
# Hourly SPC collection (raw counters, not counter-reset subgroups)
# ──────────────────────────────────────────────
#
# An SPC subgroup above is created by a batch counter reset, which happens
# ~2 times/day/panel — about once per shift (measured live, 2026-08-27). That
# is fine for a shift-level trend but leaves ~90% of clock hours with no
# subgroup at all, so an hourly trend needs its own bucketing off the raw
# counters rather than a finer slice of the same reset events.
#
# The technique: sum positive increments between consecutive readings of the
# same counter, grouped by clock hour. A decrease mid-hour is a batch/order
# reset; its delta is the new (post-reset) value, since the counter counts up
# from 0 again — the same convention _output_max_in_epoch() uses for resets
# within a shift epoch, applied here per hour instead of per epoch. Verified
# against a bulk 3-day pull against live SQL: exact hourly rejects and parts
# in one query per table, no per-subgroup round trips, median ~316 parts/
# lane/hour, comfortably above the existing n>=100 subgroup threshold.
#
# Bounded on the same _SPC_LOOKBACK_DAYS used for reset detection above —
# generous relative to what the hourly window/baseline actually need
# (SPC_HOURLY_WINDOW_HOURS + SPC_HOURLY_BASELINE_BUCKETS in weekly_analysis.py
# is well under 75 days of hours), but reusing one bound means one fewer knob
# and automatic consistency if that constant is ever retuned.


def _hourly_deltas(conn, tbl, order_col, name_col, counter_col, patterns, since_str):
    """One bulk query: per (hour, counter_name) positive-increment sum.

    `patterns` is one LIKE pattern or a list of them — camera_dual passes both
    its rear and front patterns in one call so lane-pairing sees both series;
    PARTITION BY name keeps each counter's LAG sequence independent even though
    every matching name is read in one pass. `order_col` is what LAG orders by:
    block_id for the counter tables (their logged sequence, matching
    _detect_counter_resets' own ORDER BY), t_stamp for output_stats (no
    block_id column there).
    """
    pats = list(patterns) if isinstance(patterns, (list, tuple)) else [patterns]
    where_like = "(" + " OR ".join(f"{name_col} LIKE ?" for _ in pats) + ")"
    hr = hour_trunc("t_stamp")
    return query(conn, f"""
    WITH d AS (
        SELECT {name_col} AS name, t_stamp, {counter_col} AS cnt,
               LAG({counter_col}) OVER (PARTITION BY {name_col} ORDER BY {order_col}) AS prev
        FROM {tbl}
        WHERE {where_like} AND t_stamp >= ?
    )
    SELECT {hr} AS hr, name,
           SUM(CASE WHEN prev IS NULL THEN 0
                    WHEN cnt >= prev THEN cnt - prev ELSE cnt END) AS rejects
    FROM d
    GROUP BY {hr}, name
    """, params=[*pats, since_str])


def _hourly_parts(conn, since_str):
    """Hourly parts produced, from output_stats' Counter_Total — same
    positive-increment technique as _hourly_deltas(), undivided (callers
    divide by their own panel's n_lanes_divisor)."""
    tbl = get_table(CFG, "output_stats")
    hr = hour_trunc("t_stamp")
    return query(conn, f"""
    WITH d AS (
        SELECT t_stamp, {C_TOTAL} AS cnt,
               LAG({C_TOTAL}) OVER (ORDER BY t_stamp) AS prev
        FROM {tbl}
        WHERE t_stamp >= ?
    )
    SELECT {hr} AS hr,
           SUM(CASE WHEN prev IS NULL THEN 0
                    WHEN cnt >= prev THEN cnt - prev ELSE cnt END) AS parts
    FROM d
    GROUP BY {hr}
    """, params=[since_str])


def _hourly_subgroups_from_deltas(lane_df, parts_df, divisor, excluded_days=None):
    """Assemble the {t_stamp, n_inspected, <lane>: rejects} subgroup list from
    the two bulk hourly queries: one dict per hour across the full lookback
    window, oldest first.

    Every hour in [min(hr), max(hr)] across both frames is emitted, matching
    the shift-grain convention of "every point on the axis gets an entry, gaps
    included" — an hour with no lane activity is 0 rejects, not a missing
    point; an hour with no production at all is n_inspected=0, which callers
    already render as a gap (null rate/UCL) rather than shrinking the axis.

    Every hour's dict carries the FULL set of lane names discovered anywhere
    in the window as keys (0 where inactive that hour), not just the names
    active that particular hour. This matters for lane_key_mode
    "discovered_sorted" (the Vision panel), whose lane set is read from just
    the first subgroup in derive_spc_lanes() — with a per-hour-only key set,
    an oldest hour that happened to be quiet could silently drop a lane from
    the whole series.
    """
    if parts_df.empty:
        return []
    lane_names = sorted(lane_df["name"].unique()) if not lane_df.empty else []
    lo, hi = parts_df["hr"].min(), parts_df["hr"].max()
    if not lane_df.empty:
        lo, hi = min(lo, lane_df["hr"].min()), max(hi, lane_df["hr"].max())
    grid = pd.date_range(lo, hi, freq="1h")

    parts_by_hr = parts_df.set_index("hr")["parts"].to_dict()
    lane_by_hr = {}
    if not lane_df.empty:
        for hr, g in lane_df.groupby("hr"):
            lane_by_hr[hr] = dict(zip(g["name"], g["rejects"]))

    out = []
    for hr in grid:
        ts_str = hr.strftime("%Y-%m-%d %H:%M:%S")
        if excluded_days and ts_str[:10] in excluded_days:
            continue
        parts = parts_by_hr.get(pd.Timestamp(hr), 0) or 0
        row = {"t_stamp": ts_str, "n_inspected": parts / divisor if divisor else 0}
        active = lane_by_hr.get(pd.Timestamp(hr), {})
        for name in lane_names:
            row[name] = active.get(name, 0) or 0
        out.append(row)
    return out


def _collect_single_hourly(conn, panel, since_str, excluded_days=None):
    """Hourly counterpart to _collect_counter_subgroups() for a "single" panel."""
    tbl = get_table(CFG, panel["table_key"])
    lane_df = _hourly_deltas(conn, tbl, "block_id", panel["name_col"],
                             panel["counter_col"], panel["pattern"], since_str)
    parts_df = _hourly_parts(conn, since_str)
    return _hourly_subgroups_from_deltas(lane_df, parts_df, panel["divisor"],
                                         excluded_days)


def _collect_camera_dual_hourly(conn, params, since_str, excluded_days=None):
    """Hourly counterpart to _collect_camera_dual(): sums both stations'
    hourly positive increments per physical lane (parsed from the counter
    name's numeric suffix, same as _sum_camera_lanes()), producing the
    "lane{N}" keys lane_key_mode "synthetic" expects. The literal "lane"
    prefix mirrors the existing block-level camera collector's own
    dict-comprehension construction rather than reading
    panel["lane_key_prefix"], for the same reason that one does: it is a
    collector-internal convention, not something callers configure per run.
    """
    tbl = get_table(CFG, params["table_key"])
    lane_df = _hourly_deltas(conn, tbl, "block_id", params["name_col"],
                             params["counter_col"],
                             [params["rear_pattern"], params["front_pattern"]],
                             since_str)
    if not lane_df.empty:
        lane_num = lane_df["name"].str.rsplit("_", n=1).str[-1].astype(int)
        lane_df = (lane_df.assign(lane_num=lane_num)
                          .groupby(["hr", "lane_num"], as_index=False)["rejects"].sum())
        lane_df["name"] = "lane" + lane_df["lane_num"].astype(str)
    parts_df = _hourly_parts(conn, since_str)
    return _hourly_subgroups_from_deltas(lane_df, parts_df, params["divisor"],
                                         excluded_days)


CUSTOM_HOURLY_COLLECTORS = {
    "camera_dual": _collect_camera_dual_hourly,
}


def collect_hourly_subgroups(conn, cfg, panel, excluded, lookback_days=None):
    """Hourly counterpart to collect_spc_subgroups(): buckets by clock hour off
    the raw counters rather than by counter-reset event. See the module note
    above _hourly_deltas() for why hourly needs its own collection path.

    Bounded on _SPC_LOOKBACK_DAYS back from the freshest observed output_stats
    reading — never wall clock, matching every other lookback bound in this
    module, since the historian's clock is not assumed to match the caller's.
    """
    days = lookback_days if lookback_days is not None else _SPC_LOOKBACK_DAYS
    mx = query(conn, f"SELECT MAX(t_stamp) AS m FROM {get_table(CFG, 'output_stats')}")
    if mx.empty or mx.iloc[0, 0] is None:
        return None
    anchor = pd.Timestamp(mx.iloc[0, 0])
    since_str = (anchor - pd.Timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    collector = panel.get("collector", "single")
    if collector == "single":
        return _collect_single_hourly(conn, panel, since_str, excluded_days=excluded)
    fn = CUSTOM_HOURLY_COLLECTORS.get(collector)
    if fn is None:
        raise KeyError(
            f"Unknown hourly SPC collector {collector!r}; register it in "
            f"CUSTOM_HOURLY_COLLECTORS"
        )
    return fn(conn, panel.get("params", {}), since_str, excluded_days=excluded)
