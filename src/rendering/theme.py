"""
Shared visual constants — colors, CSS, chart sizing, and line-aware segment definitions.

Imported by panels.py and both dashboard rendering scripts. Calls get_line_config()
and configure_segments() once at module load so SEGMENT_DEFS reflects the active line.
"""

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from core.lines import get_line_config
from domain.clusters import configure_segments

CFG = get_line_config()
configure_segments(CFG["segments"])

# Hour separating day-shift from night-shift labels: midpoint of the day shift
# (boundaries are [day_start, night_start], e.g. 07/19 → 13). Used to attribute
# counter-reset timestamps to a shift.
_SB = CFG["shift_boundaries"]
SHIFT_MIDPOINT_HOUR = (_SB[0] + (_SB[0] + ((_SB[1] - _SB[0]) % 24))) // 2

# ──────────────────────────────────────────────
# Colorblind-safe OEE palette (no red-green pairs)
# ──────────────────────────────────────────────
CLR_OEE     = "#264653"
CLR_AVAIL   = "#e76f51"
CLR_PERF    = "#e9c46a"
CLR_QUALITY = "#0077b6"

# ──────────────────────────────────────────────
# Downtime category palette (monochrome dark→light)
# ──────────────────────────────────────────────
DOWNTIME_CATEGORIES = [
    ("Micro-stops (<2 min)", "micro_stops", "#2d3436"),
    ("Halted (operator wait)", "halted", "#636e72"),
    ("Auto-stops (>2 min)", "auto_long", "#b2bec3"),
    ("Manual stops", "manual", "#dfe6e9"),
]

# ──────────────────────────────────────────────
# Segment definitions — derived from line config
# ──────────────────────────────────────────────
_seg_cfg = CFG["segments"]
SEGMENT_DEFS = [
    (_seg_cfg["labels"].get(s, s.title()), s, _seg_cfg["colors"].get(s, "#b2bec3"))
    for s in _seg_cfg["order"]
]

# ──────────────────────────────────────────────
# Shared CSS
# ──────────────────────────────────────────────
CSS = """\
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Poppins', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f6fa; color: #2d3436; }
.mw-header-bar { background: linear-gradient(135deg, #E6007E 0%, #5F32FF 50%, #0031FF 100%); height: 4px; }
header { background: #1E194B; color: white; padding: 1.5rem 2rem; display: flex; align-items: center; gap: 1.5rem; }
.mw-logo { height: 32px; flex-shrink: 0; }
.header-divider { width: 1px; height: 36px; background: rgba(255,255,255,0.25); flex-shrink: 0; }
.header-text { flex: 1; }
header h1 { font-size: 1.6rem; font-weight: 600; margin-bottom: 0.3rem; letter-spacing: -0.01em; }
.meta { opacity: 0.75; font-size: 0.85rem; font-weight: 300; }
main { max-width: 1400px; margin: 0 auto; padding: 1.5rem; }
.section { background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 1.5rem; overflow: hidden; }
.section-header { padding: 1.2rem 1.5rem; border-bottom: 1px solid #eee; border-left: 4px solid #1E194B; }
.section-header h2 { font-size: 1.6rem; color: #1E194B; }
.section-body { padding: 1.5rem; }
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; margin-bottom: 1.5rem; }
.kpi { border: 1px solid #eee; text-align: center; padding: 1rem 0.5rem; }
.kpi:first-child { border-radius: 8px 0 0 8px; }
.kpi:last-child { border-radius: 0 8px 8px 0; }
.kpi-label { font-size: 1rem; color: #636e72; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.kpi-value { font-size: 4rem; font-weight: 700; margin: 0.3rem 0; }
.kpi-delta { font-size: 1rem; font-weight: 600; }
.kpi-delta.up { color: #0077b6; }
.kpi-delta.down { color: #e76f51; }
.kpi-sub { font-size: 0.9rem; color: #b2bec3; }
.stop-summary-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; margin-top: 1.5rem; }
.stop-kpi { border: 1px solid #eee; text-align: center; padding: 0.8rem 0.5rem; }
.stop-kpi:first-child { border-radius: 8px 0 0 8px; }
.stop-kpi:last-child { border-radius: 0 8px 8px 0; }
.stop-kpi-label { font-size: 0.95rem; color: #636e72; font-weight: 600; text-transform: uppercase; }
.stop-kpi-value { font-size: 3rem; font-weight: 700; color: #2d3436; margin: 0.2rem 0; }
.panel-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; margin-top: 1.5rem; }
@media (max-width: 1100px) { .panel-grid { grid-template-columns: 1fr; } }
.panel { border: 1px solid #eee; border-radius: 6px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
.panel h3 { font-size: 1.4rem; padding: 1rem 1.2rem; background: #f8f9fa; border-bottom: 1px solid #eee; color: #1E194B; }
.panel-render { padding: 0.5rem; }
.full-width { grid-column: 1 / -1; }
footer { text-align: center; padding: 1.5rem 2rem; color: #b2bec3; font-size: 0.8rem; border-top: 3px solid; border-image: linear-gradient(135deg, #E6007E 0%, #5F32FF 50%, #0031FF 100%) 1; }
.spc-group { border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 1.5rem; overflow: hidden; }
.spc-group-label { background: #f0f0f0; padding: 0.5rem 1.2rem; font-size: 0.82rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: #2d3436; border-bottom: 1px solid #e0e0e0; }
.spc-group .panel { border-radius: 0; box-shadow: none; border-bottom: 1px solid #f0f0f0; }
.spc-group .panel:last-child { border-bottom: none; }
.cluster-ref-details { margin: 1rem 0 0.5rem; border: 1px solid #e0e0e0; border-radius: 6px; padding: 0 1rem; background: #fafafa; }
.cluster-ref-details summary { cursor: pointer; font-weight: 600; font-size: 0.9rem; padding: 0.65rem 0; color: #2d3436; user-select: none; }
.ref-hint { font-weight: 400; font-size: 0.78rem; color: #636e72; margin-left: 0.5rem; }
.cluster-ref-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-bottom: 0.75rem; }
.cluster-ref-table th { background: #f0f0f0; text-align: left; padding: 6px 10px; border-bottom: 2px solid #ddd; white-space: nowrap; }
.cluster-ref-table td { padding: 5px 10px; border-bottom: 1px solid #eee; vertical-align: top; }
.cluster-ref-table tr:last-child td { border-bottom: none; }
.cluster-ref-table .seg-cell { font-size: 0.85rem; font-weight: 600; white-space: nowrap; vertical-align: middle; padding-left: 12px; background: #fff; }
.cluster-ref-table .num-cell { text-align: right; white-space: nowrap; color: #636e72; }
.ex-list { margin: 0; padding: 0 0 0 1rem; list-style: disc; }
.ex-list li { margin-bottom: 2px; white-space: nowrap; }
.ex-label { margin-right: 0.4rem; }
.ex-meta { font-size: 0.75rem; color: #636e72; }
.dash-nav { background: #f8f9fa; border-bottom: 1px solid #e0e0e0; padding: 0.4rem 2rem; display: flex; align-items: center; justify-content: flex-end; gap: 1rem; font-size: 0.82rem; }
.dash-nav a { color: #1E194B; text-decoration: none; font-weight: 600; padding: 0.25rem 0.8rem; border: 1px solid #ddd; border-radius: 4px; background: white; }
.dash-nav a:hover { background: #1E194B; color: white; }
@media print { .dash-nav { display: none; } .section { box-shadow: none !important; border: 1px solid #ddd; break-inside: avoid; } }
/* ── OEE dashboard components (oee_section.py) ────────────── */
.callout { border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: 1rem; font-size: 0.9rem; line-height: 1.5; display: flex; gap: 0.8rem; align-items: flex-start; }
.callout-warn { background: #fff3cd; border-left: 4px solid #ffc107; }
.callout-info { background: #d1ecf1; border-left: 4px solid #17a2b8; }
.callout-icon { font-size: 1.2rem; flex-shrink: 0; margin-top: 0.1rem; }
.callout strong { color: #664d03; }
.callout-info strong { color: #0c5460; }
.analysis { background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 1.5rem; overflow: hidden; }
.analysis-header { padding: 1.2rem 1.5rem; border-bottom: 1px solid #eee; border-left: 4px solid #1E194B; display: flex; align-items: center; gap: 1rem; }
.analysis-header h2 { font-size: 1.3rem; flex: 1; color: #1E194B; }
.status { font-size: 0.75rem; padding: 0.25rem 0.6rem; border-radius: 12px; font-weight: 600; text-transform: uppercase; }
.status.partial { background: #ffeaa7; color: #856404; }
.status.success { background: #d4edda; color: #155724; }
.analysis-meta { padding: 0.5rem 1.5rem; font-size: 0.85rem; color: #636e72; border-bottom: 1px solid #eee; }
.warnings { padding: 0.8rem 1.5rem; background: #fff9e6; border-bottom: 1px solid #eee; }
.warnings summary { cursor: pointer; font-weight: 600; color: #856404; font-size: 0.85rem; }
.warnings ul { margin: 0.5rem 0 0 1.5rem; font-size: 0.8rem; color: #664d03; }
.warnings li { margin-bottom: 0.3rem; }
.panel .subtitle { font-size: 0.8rem; color: #636e72; padding: 0 1rem 0.5rem; background: #f8f9fa; }
.derivation { background: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 6px; padding: 1rem; margin: 0.5rem 1rem; font-size: 0.85rem; }
.derivation code { background: #e8eaf6; padding: 1px 6px; border-radius: 3px; font-size: 0.82rem; }
.derivation .arrow { color: #4CAF50; font-weight: 700; margin: 0 0.3rem; }
.source-tag { display: inline-block; font-size: 0.7rem; background: #e8f5e9; color: #2e7d32; padding: 2px 8px; border-radius: 10px; margin-left: 8px; font-weight: 600; }
"""

# ──────────────────────────────────────────────
# Tablet / display sizing constants
# Tune these to adjust for different screen sizes without touching chart code.
# ──────────────────────────────────────────────
CHART_FONT_SIZE = 18        # global Plotly font (axis labels, legend, tick marks)
CHART_LEGEND_FONT = 18      # legend item font (can differ from base if needed)
CHART_TICKFONT = 16         # explicit tick-label override where Plotly needs it
CHART_BAR_LABEL = 16        # inside-bar text labels
CHART_ANNOTATION_FONT = 15  # small chart annotations (p-bar label etc.)
CHART_LINE_WIDTH = 4        # main data lines
CHART_LINE_WIDTH_REF = 3    # reference / p-bar dotted lines
CHART_LINE_WIDTH_UCL = 2.5  # UCL faint dashed lines
CHART_MARKER_SM = 12        # small markers (EWMA dots, trend dots)
CHART_MARKER_MD = 14        # medium markers (trend current-shift, breach X)
CHART_MARKER_LG = 18        # large markers (OEE trend latest point)
CHART_MARKER_TREND = 14     # non-latest points on OEE trend
CHART_UCL_MARKER = 16       # UCL step-tick markers
CHART_UCL_MARKER_LINE = 4   # UCL step-tick line width

# Heights (px) — increase for more vertical space per chart
CHART_H_HOURLY = 500
CHART_H_EWMA = 420
CHART_H_OEE_TREND = 450
CHART_H_DT_MIX = 380
CHART_H_WEEKLY_OEE = 500
CHART_H_WEEKLY_DT = 560
CHART_H_WEEKLY_SPC = 450
CHART_H_WEEKLY_EWMA = 420

EWMA_LANE_COLORS = [
    "#264653", "#e9c46a", "#e76f51", "#2a9d8f", "#6a4c93",
    "#f4a261", "#6d6875", "#48bfe3", "#8338ec", "#d4a373",
]
