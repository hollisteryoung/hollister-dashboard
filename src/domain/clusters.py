"""
domain.clusters — Alarm segment and cause-cluster classification.

Raw alarm rows in StatusBlocks represent individual faults on individual
machines.  For dashboards and anomaly detection the thousands of distinct
alarms must be collapsed into a small number of named segments (e.g.
"Line Overall", "Schubert", "Foam") and cause clusters (e.g. "Mechanical",
"Sensor", "Material").  The segment taxonomy and cluster keyword rules are
declared in line_config so they can differ across lines without code changes.
"""

import os
import re
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from domain.faults import rc_display_label, rc_description_from_fc_entries


_ALARM_NUM_PREFIX = re.compile(r'^(\d+)')

# Module-level active config — set via configure_segments() at pipeline startup.
_SEGMENTS_CFG: dict | None = None


def configure_segments(segments_cfg: dict) -> None:
    """Set the active segment config. Call once at pipeline startup with CFG['segments']."""
    global _SEGMENTS_CFG
    compiled_kw = {
        seg: re.compile(pat, re.I)
        for seg, pat in segments_cfg.get("keywords", {}).items()
    }
    compiled_clusters = {
        seg: [(name, re.compile(pat, re.I)) for name, pat in pairs]
        for seg, pairs in segments_cfg.get("cause_clusters", {}).items()
    }
    _SEGMENTS_CFG = {**segments_cfg, "_kw": compiled_kw, "_clusters": compiled_clusters}


def _ensure_cfg() -> dict:
    """Return the active segment config, or fail loudly if not configured."""
    if _SEGMENTS_CFG is None:
        raise RuntimeError(
            "Segment config not initialised — call configure_segments(CFG['segments']) "
            "at pipeline startup before using clusters classification."
        )
    return _SEGMENTS_CFG


def get_segment_order() -> list:
    return _ensure_cfg()["order"]


def get_cause_clusters_display() -> dict:
    """Return {seg: [(name, compiled_re), ...]} — for the presentation layer."""
    return _ensure_cfg()["_clusters"]


def _segment_from_label_cfg(label: str, cfg: dict) -> str:
    if not label or label == "Unattributed":
        return "unattributed"
    # 1. Numeric-prefix → code_ranges (for lines where alarm-code ranges are meaningful)
    code_ranges = cfg.get("code_ranges", {})
    if code_ranges:
        m = _ALARM_NUM_PREFIX.match(label)
        if m:
            num = int(m.group(1))
            if num < 10:
                num *= 1000
            for seg, (lo, hi) in code_ranges.items():
                if lo <= num < hi:
                    return seg
    # 2. Keyword fallback
    for seg, pat in cfg["_kw"].items():
        if pat.search(label):
            return seg
    return "unattributed"


def classify_cause_cluster(label: str, segment: str) -> str | None:
    """Classify a stop label into a cause cluster within its segment."""
    if segment in ("manual", "unattributed", "running"):
        return None
    if not label or label == "Unattributed":
        return "Other"
    patterns = _ensure_cfg()["_clusters"].get(segment, [])
    for name, pat in patterns:
        if pat.search(label):
            return name
    return "Other"


def classify_alarm_segment(parsed_faults, alarm_text=None, stop_type=None) -> str:
    """Classify a fault to a line segment based on alarm code prefix or keywords.

    Returns one of the segments defined in the active config (always ends with
    'manual' and 'unattributed').  Secondary RC codes are tried when the primary
    is unattributed, so upstream causes are not silently lost.
    """
    if stop_type and str(stop_type).lower() == "manual":
        return "manual"

    cfg = _ensure_cfg()
    label = rc_display_label(parsed_faults, alarm_text)
    seg = _segment_from_label_cfg(label, cfg)
    if seg != "unattributed":
        return seg

    # Primary RC is unattributed — try secondary RC codes
    fc_entries = parsed_faults.get("fc_entries", [])
    for rc in parsed_faults.get("rc_codes_all", [])[1:]:
        desc = rc_description_from_fc_entries(rc, fc_entries)
        if desc:
            seg = _segment_from_label_cfg(desc, cfg)
            if seg != "unattributed":
                return seg

    return "unattributed"
