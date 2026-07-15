"""
domain.schedule — Analysis window resolution and production schedule loading.

The analysis window (time_from / time_to) is the authoritative date range used
by every pipeline query.  In the full LLM pipeline it is written to
state/session.json by the COORDINATOR; in the client-only deployment it falls
back to default_time_range in lines.py.  load_time_range() hides that
complexity from callers.  load_production_schedule() reads the validator output
so that OEE denominators can exclude non-production days.
"""

import json
import os
import sys


SESSION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "state", "session.json",
)


def build_excl_clause(excluded_days: list, ts_col: str = "Start_TS") -> tuple:
    """Build a SQL WHERE fragment that excludes non-production days.

    Returns (clause, params) where clause is an empty string when excluded_days
    is empty (safe to embed with AND or WHERE), and params is the matching list
    of parameter values for the SQL driver.

    Usage:
        excl_clause, excl_params = build_excl_clause(excluded_days)
        sql = f"SELECT ... WHERE Start_TS >= ? AND Start_TS < ? {excl_clause}"
        df = pd.read_sql(sql, conn, params=[time_from, time_to] + excl_params)
    """
    if not excluded_days:
        return "", []
    placeholders = ", ".join("?" * len(excluded_days))
    clause = f"AND CAST({ts_col} AS DATE) NOT IN ({placeholders})"
    return clause, list(excluded_days)


def load_time_range(line_id=None):
    """Load time_range and excluded_days from state/session.json.

    If line_id is provided and the session belongs to a *different* line,
    this function refuses to inherit that window and falls back to the
    default_time_range declared in lines.py for the requested line.
    This prevents silent cross-line contamination when session.json was last
    written by a different pipeline run.

    Returns (time_from, time_to, excluded_days) with string dates.
    Falls back to neutral (None, None, []) if no valid source can be found.
    """
    defaults = (None, None, [])
    try:
        with open(SESSION_PATH) as f:
            session = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return defaults

    session_line = session.get("line_id")
    if line_id and session_line and session_line != line_id:
        print(
            f"[schedule] session.json belongs to line '{session_line}', not '{line_id}' — "
            f"refusing to inherit its time_range. Falling back to line_config default_time_range.",
            file=sys.stderr,
        )
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            from core.lines import get_line_config
            cfg = get_line_config(line_id)
            dr = cfg.get("default_time_range", (None, None))
            return (dr[0], dr[1], [])
        except Exception as exc:
            print(f"[schedule] Could not load line_config default_time_range: {exc}", file=sys.stderr)
            return defaults

    tr = session.get("time_range", {})
    time_from = tr.get("from", defaults[0])
    time_to = tr.get("to", defaults[1])
    excluded_days = session.get("excluded_days", defaults[2])
    return time_from, time_to, excluded_days


def load_production_schedule(line_id, time_from=None, time_to=None):
    """Load the auto-detected production schedule for a line.

    Returns (non_production_days, effective_hours, warnings) where:
      - non_production_days: list of "YYYY-MM-DD" strings to exclude from OEE
      - effective_hours: dict mapping "YYYY-MM-DD" -> float scheduled hours, or
        None if the schedule isn't available (caller falls back to flat 24h).
      - warnings: list of strings to surface in the analysis warnings list

    When time_from/time_to are provided the helper verifies the schedule's
    time_range matches; on mismatch the schedule is rejected (returns None)
    and a warning is emitted so the analyzer doesn't silently apply stale
    per-day hours from a previous run.
    """
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "output", "validation",
        f"production-schedule-{line_id}.json",
    )
    if not os.path.exists(path):
        return [], None, [
            f"No production-schedule file at {os.path.relpath(path)} — "
            f"availability uses flat 24h × calendar days. "
            f"Run: python3 src/pipelines/schedule_validation.py --line {line_id}"
        ]

    with open(path) as f:
        data = json.load(f)

    warnings = []
    if time_from is not None and time_to is not None:
        sched_tr = data.get("time_range", {})
        if sched_tr.get("from") != time_from or sched_tr.get("to") != time_to:
            warnings.append(
                f"Production-schedule time_range {sched_tr.get('from')}..{sched_tr.get('to')} "
                f"does not match analysis window {time_from}..{time_to} — "
                f"ignoring schedule, falling back to flat 24h. Re-run the validator."
            )
            return [], None, warnings

    non_prod = data.get("non_production_days", [])
    effective = {row["day"]: row["effective_hours"] for row in data.get("per_day", [])}
    return non_prod, effective, warnings


def apply_schedule(cfg, time_from, time_to, warnings, session_excluded=None):
    """Load the validator's production schedule and return schedule state.

    Returns a dict with keys: excluded_days, auto_excluded_days, effective_hours.
    Appends any warnings about staleness/absence to the provided list.
    """
    non_prod, effective, sched_warnings = load_production_schedule(
        cfg["line_id"], time_from, time_to,
    )
    warnings.extend(sched_warnings)
    auto_excluded = list(non_prod)
    base_excluded = set(cfg.get("excluded_days", session_excluded or []))
    excluded = sorted(base_excluded | set(auto_excluded))
    return {
        "excluded_days": excluded,
        "auto_excluded_days": auto_excluded,
        "effective_hours": effective,
    }
