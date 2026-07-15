"""
domain.shifts — Shift arithmetic: assignment, window calculation, and traversal.

All OEE and SPC metrics are computed per-shift (Day / Night).  This module
owns the logic for mapping any timestamp to its owning shift, computing exact
shift start/end datetimes, and iterating the sequence of shifts in a date
range.  Shift boundaries are read from line_config so the same code works for
both the 07:00–19:00 pattern on NGP2/HU3 and any future schedule variant.
"""

from datetime import datetime, timedelta

import pandas as pd


def assign_shift(ts, shift_boundaries):
    """Assign a timestamp to a shift.

    Returns (shift_type, shift_date_str) where shift_type is 'day' or 'night',
    and shift_date_str is the YYYY-MM-DD of the calendar date the shift belongs
    to.  Night shifts starting at e.g. 19:00 belong to that same calendar date
    (not the next morning).
    """
    if isinstance(ts, str):
        ts = pd.Timestamp(ts)
    day_start, night_start = shift_boundaries
    h = ts.hour
    if day_start <= h < night_start:
        return "day", ts.strftime("%Y-%m-%d")
    if h >= night_start:
        return "night", ts.strftime("%Y-%m-%d")
    # Before day_start (e.g. 00:00–06:59) → belongs to previous day's night shift
    return "night", (ts - timedelta(days=1)).strftime("%Y-%m-%d")


def shift_window(shift_date_str, shift_type, shift_boundaries):
    """Return (start_dt, end_dt) as datetime objects for a specific shift."""
    day_start, night_start = shift_boundaries
    d = datetime.strptime(shift_date_str, "%Y-%m-%d")
    if shift_type == "day":
        return d.replace(hour=day_start), d.replace(hour=night_start)
    return d.replace(hour=night_start), (d + timedelta(days=1)).replace(hour=day_start)


def previous_shifts(shift_date_str, shift_type, shift_boundaries, n=4):
    """Return a list of (shift_date_str, shift_type) for the *n* shifts
    preceding the given shift, in reverse chronological order."""
    result = []
    cur_date, cur_type = shift_date_str, shift_type
    for _ in range(n):
        if cur_type == "day":
            prev_date = (datetime.strptime(cur_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            cur_date, cur_type = prev_date, "night"
        else:
            cur_type = "day"
        result.append((cur_date, cur_type))
    return result


def spc_baseline_cutoff(ref_date, ref_type, shift_bounds, n_days=7):
    """Return the SPC baseline cutoff as a "%Y-%m-%d %H:%M:%S" string.

    This is the start of the oldest shift in an n_days-day window anchored at
    (ref_date, ref_type).  Both shift and weekly dashboards use this value as
    the upper bound for SPC parameter estimation (p_bar, sigma_z) so the two
    views share identical control limits.
    """
    week = [(ref_date, ref_type)] + previous_shifts(ref_date, ref_type, shift_bounds, n=n_days * 2 - 1)
    bl_start, _ = shift_window(week[-1][0], week[-1][1], shift_bounds)
    return bl_start.strftime("%Y-%m-%d %H:%M:%S")


def is_near_shift_transition(minute_of_day: int, transitions: list, tolerance_min: int = 30) -> bool:
    """Return True if minute_of_day falls within tolerance_min of any shift boundary.

    transitions: list of hour integers (e.g. [7, 19] for 07:00 and 19:00).
    minute_of_day: minutes since midnight (0–1439).

    Used to flag measurements near shift changes where data continuity is uncertain.
    """
    return any(abs(minute_of_day - t * 60) <= tolerance_min for t in transitions)


def format_shift_label(s_type, s_date):
    """Return the canonical shift label string, e.g. "Day 05-25" or "Night 05-25".

    s_type: "day" or "night"
    s_date: ISO date string, e.g. "2026-05-25" (only MM-DD part is used)

    This is the single source of truth for the shift label format used across
    all pipelines and dashboards (convention: "Day MM-DD" / "Night MM-DD").
    """
    return f"{'Day' if s_type == 'day' else 'Night'} {s_date[5:]}"
