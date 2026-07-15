#!/usr/bin/env python3
"""
Render the Weekly Dashboard HTML from weekly-dashboard.json.

Reads output/analyses/<line>/weekly-dashboard.json and produces
output/reports/<line>/weekly-dashboard.html — a self-contained single
file with all JS/CSS inline.

Usage:
    python3 src/rendering/weekly_report.py [--line ngp2|hu3]
"""

import json
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from core.lines import get_spc_panels
from core.storage import load_analysis_json
from rendering.shared import MW_LOGO_BASE64, _assert_self_contained, build_html_head, build_kpi_tiles, build_stop_summary
from rendering.theme import (
    CFG,
    CLR_AVAIL,
    CLR_OEE,
    CLR_PERF,
    CLR_QUALITY,
    EWMA_LANE_COLORS,
    SEGMENT_DEFS,
    CHART_FONT_SIZE,
    CHART_LEGEND_FONT,
    CHART_TICKFONT,
    CHART_LINE_WIDTH,
    CHART_LINE_WIDTH_REF,
    CHART_LINE_WIDTH_UCL,
    CHART_MARKER_SM,
    CHART_MARKER_MD,
    CHART_MARKER_LG,
    CHART_MARKER_TREND,
    CHART_UCL_MARKER,
    CHART_H_WEEKLY_OEE,
    CHART_H_WEEKLY_SPC,
    CHART_H_WEEKLY_EWMA,
)
from rendering.components import (
    _bar_segment_label,
    _build_anomaly_panel,
    _build_cluster_reference_table,
    _build_downtime_categories,
    _build_segment_downtime,
    _delta_html,
    _kpi_color,
    _ts_to_shift_label,
)
ANALYSIS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "output", "analyses", CFG["line_id"]
)
OUTPUT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "output", "reports", CFG["line_id"]
)


_CHART_ERROR_DIV = (
    "'<div style=\"padding:16px;color:#d63031;background:#fff5f5;"
    "border:1px solid #fdb;border-radius:4px;font-family:monospace;\">Chart error: '+e+'</div>'"
)


def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> str:
    """Convert a 6-char hex color to an rgba() string."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _plotly_script(div_id, traces_str, layout, with_click=False):
    """Wrap a Plotly.newPlot call in a try/catch <script> tag.

    with_click=True adds a plotly_click handler that navigates to point.customdata.
    """
    if with_click:
        return (
            f"<script>\ntry {{\n"
            f"  Plotly.newPlot('{div_id}', [{traces_str}], {layout}, {{displayModeBar:false}});\n"
            f"  document.getElementById('{div_id}').on('plotly_click', function(data) {{\n"
            f"    var url = data.points[0].customdata;\n"
            f"    if (url) window.location.href = url;\n"
            f"  }});\n"
            f"}} catch(e) {{\n"
            f"  document.getElementById('{div_id}').innerHTML = {_CHART_ERROR_DIV};\n"
            f"}}\n</script>"
        )
    return (
        f"<script>try{{Plotly.newPlot('{div_id}',[{traces_str}],{layout},{{displayModeBar:false}})}}"
        f"catch(e){{document.getElementById('{div_id}').innerHTML={_CHART_ERROR_DIV};}}</script>"
    )


def _load_data():
    return load_analysis_json(CFG, "weekly-dashboard.json")


# ──────────────────────────────────────────────
# Weekly KPI tiles
# ──────────────────────────────────────────────

def _build_weekly_kpi_tiles(week):
    prior = week.get("prior_week")
    prev_label = ""
    if prior:
        pf, pt = prior["from"][5:], prior["to"][5:]
        prev_label = f"prior week ({pf}&ndash;{pt})"
    good = week.get("good_count", 0)
    total = week.get("total_count", 0)
    raw_metrics = [
        ("OEE", "oee", "A &times; P &times; Q"),
        ("Availability", "availability", f"{week.get('total_downtime_hours', 0):.1f}h DT / {week.get('production_shifts', 0) * 12}h sched"),
        ("Performance", "performance", "vs P95 ideal PPM"),
        ("Quality", "quality", f"{good:,} / {total:,} units" if total else "good / total produced"),
    ]
    tiles = [
        {
            "label": label,
            "val": week[key],
            "color": _kpi_color(week[key]),
            "delta": _delta_html(week[key], prior[key], prev_label) if prior else "",
            "sub": sub,
        }
        for label, key, sub in raw_metrics
    ]
    return build_kpi_tiles(tiles)


# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# OEE trend — 14-shift scatter (day=circle, night=diamond)
# ──────────────────────────────────────────────

def _build_oee_trend(shifts, div_id="weekly-oee-trend"):
    """OEE/A/P/Q line chart across production shifts — Plotly (inline JS), click point to open shift."""
    production = [s for s in reversed(shifts) if not s.get("skipped")]
    if not production:
        return ""

    x_labels = [s["label"] for s in production]
    urls = [f"shifts/shift-dashboard-{s['shift_date']}-{s['shift_type']}.html" for s in production]
    symbols = ["circle" if s["shift_type"] == "day" else "diamond" for s in production]
    sizes = [CHART_MARKER_LG if i == len(production) - 1 else CHART_MARKER_TREND
             for i in range(len(production))]

    traces = []
    for name, key, color in [
        ("OEE", "oee", CLR_OEE),
        ("Availability", "availability", CLR_AVAIL),
        ("Performance", "performance", CLR_PERF),
        ("Quality", "quality", CLR_QUALITY),
    ]:
        values = [s[key] for s in production]
        traces.append(json.dumps({
            "type": "scatter",
            "mode": "lines+markers",
            "name": name,
            "x": x_labels,
            "y": values,
            "customdata": urls,
            "marker": {"color": color, "size": sizes, "symbol": symbols},
            "line": {"color": color, "width": CHART_LINE_WIDTH},
            "hovertemplate": f"{name}: %{{y:.1f}}%<extra></extra>",
        }))

    # Night shading: faint rect behind each night shift point
    shapes = []
    for i, s in enumerate(production):
        if s["shift_type"] == "night":
            shapes.append({
                "type": "rect",
                "xref": "x",
                "yref": "paper",
                "x0": i - 0.4,
                "x1": i + 0.4,
                "y0": 0,
                "y1": 1,
                "fillcolor": "rgba(0,0,0,0.04)",
                "line": {"width": 0},
                "layer": "below",
            })

    layout = json.dumps({
        "margin": {"l": 80, "r": 130, "t": 10, "b": 100},
        "yaxis": {"title": "%", "range": [0, 105], "gridcolor": "#eee", "automargin": True},
        "xaxis": {"type": "category", "tickangle": -45,
                  "tickfont": {"size": CHART_TICKFONT}, "automargin": True},
        "plot_bgcolor": "white",
        "legend": {"orientation": "v", "x": 1.02, "y": 1, "xanchor": "left", "yanchor": "top",
                   "font": {"size": CHART_LEGEND_FONT}},
        "font": {"size": CHART_FONT_SIZE},
        "shapes": shapes,
        "height": CHART_H_WEEKLY_OEE,
    })

    traces_str = ", ".join(traces)
    return f"""<div class="panel full-width">
  <h3>OEE Trend &mdash; Last 7 Days <span style="font-weight:400;font-size:0.75rem;color:#636e72;">(&#9679; Day &middot; &#9670; Night &middot; click point to open shift dashboard)</span></h3>
  <div class="panel-render">
    <div id="{div_id}" style="width:100%;cursor:pointer;"></div>
  </div>
</div>
{_plotly_script(div_id, traces_str, layout, with_click=True)}"""


# Daily breakdown — stacked bar chart (like hourly breakdown)
# ──────────────────────────────────────────────

def _build_daily_breakdown(shifts, div_id="daily-breakdown"):
    """Per-shift stacked vertical column chart — pure HTML/CSS, labels inside chart."""
    production = [s for s in reversed(shifts) if not s.get("skipped")]
    if not production:
        return ""

    CHART_PX = 300  # bar area height in px (= 12 hours)
    LABEL_H = 32    # clickable label row height, inside chart container

    cols_html = ""
    for s in production:
        url = f"shifts/shift-dashboard-{s['shift_date']}-{s['shift_type']}.html"
        seg_dt = s.get("segment_downtime", {})
        total_dt = sum(seg_dt.get(seg_key, {}).get("hours", 0) for _, seg_key, _ in SEGMENT_DEFS)
        running_h = max(0.0, 12.0 - total_dt)
        is_night = s["shift_type"] == "night"

        seg_divs = ""
        for seg_label, seg_key, seg_color in SEGMENT_DEFS:
            h = seg_dt.get(seg_key, {}).get("hours", 0)
            if h <= 0:
                continue
            pct = min(100, h / 12.0 * 100)
            seg_px = pct / 100 * CHART_PX
            lbl_html = _bar_segment_label(seg_label, f"{h:.1f}h", seg_px)
            seg_divs += (
                f'<div style="height:{pct:.2f}%;background:{seg_color};flex-shrink:0;'
                f'overflow:hidden;min-height:2px;position:relative;">{lbl_html}</div>'
            )
        running_pct = max(0, running_h / 12.0 * 100)
        col_inner = (
            f'<div style="height:{running_pct:.2f}%;background:{"#ebebeb" if is_night else "#f0f0f0"};'
            f'flex-shrink:0;min-height:0;"></div>'
            + seg_divs
        )

        cols_html += (
            f'<div style="display:flex;flex-direction:column;flex:1;min-width:18px;">'
            # bar area
            f'<div style="display:flex;flex-direction:column-reverse;height:{CHART_PX}px;'
            f'overflow:hidden;border-right:1px solid rgba(255,255,255,0.4);">'
            f'{col_inner}</div>'
            # clickable label row inside chart container
            f'<a href="{url}" style="display:block;height:{LABEL_H}px;text-align:center;'
            f'font-size:0.72rem;overflow:hidden;white-space:nowrap;padding-top:7px;'
            f'text-decoration:none;border-top:1px solid #e0e0e0;background:#fafafa;'
            f'color:{"#636e72" if is_night else "#1E194B"};">'
            f'{"&#9670;" if is_night else "&#9679;"}&nbsp;{s["label"]}</a>'
            f'</div>'
        )

    # Y-axis reference lines — bottom offset includes label row height
    y_lines = "".join(
        f'<div style="position:absolute;left:0;right:0;bottom:{int(h/12.0*CHART_PX) + LABEL_H}px;'
        f'border-top:1px solid #e8e8e8;z-index:0;"></div>'
        f'<span style="position:absolute;left:2px;width:33px;text-align:right;'
        f'bottom:{int(h/12.0*CHART_PX) + LABEL_H - 6}px;'
        f'font-size:0.65rem;color:#b2bec3;white-space:nowrap;">{h}h</span>'
        for h in [3, 6, 9, 12]
    )

    legend_items = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;">'
        f'<span style="display:inline-block;width:10px;height:10px;background:{color};border-radius:1px;"></span>'
        f'<span style="font-size:0.82rem;color:#636e72;">{lbl}</span></span>'
        for lbl, _, color in SEGMENT_DEFS
    ) + (
        '<span style="display:inline-flex;align-items:center;gap:4px;">'
        '<span style="display:inline-block;width:10px;height:10px;background:#f0f0f0;'
        'border:1px solid #ddd;border-radius:1px;"></span>'
        '<span style="font-size:0.82rem;color:#636e72;">Running</span></span>'
    )

    return f"""<div class="panel full-width">
  <h3>Daily Breakdown <span style="font-weight:400;font-size:0.75rem;color:#636e72;">(running + downtime per shift &middot; &#9670; Night &middot; click label to open shift dashboard)</span></h3>
  <div class="panel-render" style="padding:0.8rem 1rem;">
    <div style="position:relative;padding-left:36px;">
      {y_lines}
      <div style="display:flex;gap:2px;position:relative;z-index:1;">{cols_html}</div>
    </div>
    <div style="margin-top:10px;padding-top:8px;border-top:1px solid #eee;">{legend_items}</div>
  </div>
</div>"""


# ──────────────────────────────────────────────
# Weekly SPC (shift-level Laney p' series + EWMA)
# ──────────────────────────────────────────────

def _build_weekly_spc_series(spc_data, title, lane_prefix, div_id):
    """Laney p' per-shift line chart — one trace per lane, Plotly (inline JS)."""
    if not spc_data:
        return ""

    lanes = spc_data["lanes"]
    n_shifts = spc_data["shift_subgroups"]
    total_shifts = spc_data.get("total_shifts", n_shifts)
    avg_p_bar = sum(lane["p_bar_pct"] for lane in lanes) / len(lanes)

    x_labels = [p["label"] for p in lanes[0]["points"]]

    traces = []
    ooc_lane_labels = []
    for i, lane in enumerate(lanes):
        lane_label = f"{lane_prefix}{lane['lane']}"
        color = EWMA_LANE_COLORS[i % len(EWMA_LANE_COLORS)]
        ucl_color = _hex_to_rgba(color, 0.3)

        y_vals = [p["rate_pct"] for p in lane["points"]]
        ooc_flags = [p["out_of_control"] for p in lane["points"]]

        traces.append(json.dumps({
            "type": "scatter",
            "name": lane_label,
            "x": x_labels,
            "y": y_vals,
            "mode": "lines+markers",
            "marker": {"size": CHART_MARKER_SM, "color": color},
            "line": {"color": color, "width": CHART_LINE_WIDTH},
            "hovertemplate": f"{lane_label}: %{{y:.2f}}%<extra></extra>",
        }))

        # Per-lane UCL as faint dashed horizontal
        ucl_vals = [p["ucl_pct"] for p in lane["points"] if p.get("ucl_pct") is not None]
        ucl = sum(ucl_vals) / len(ucl_vals) if ucl_vals else 0
        if ucl > 0:
            traces.append(json.dumps({
                "type": "scatter",
                "name": "UCL (per lane)" if i == 0 else f"UCL {lane_label}",
                "x": [x_labels[0], x_labels[-1]],
                "y": [ucl, ucl],
                "mode": "lines",
                "line": {"color": ucl_color, "width": CHART_LINE_WIDTH_UCL, "dash": "dash"},
                "showlegend": i == 0,
                "legendgroup": "ucl",
                "hovertemplate": f"UCL {lane_label}: {ucl:.2f}%<extra></extra>",
            }))

        # OOC markers (red X)
        ooc_x = [x_labels[j] for j, ooc in enumerate(ooc_flags) if ooc and y_vals[j] is not None]
        ooc_y = [y_vals[j] for j, ooc in enumerate(ooc_flags) if ooc and y_vals[j] is not None]
        if ooc_x:
            ooc_lane_labels.append(lane_label)
            traces.append(json.dumps({
                "type": "scatter",
                "x": ooc_x,
                "y": ooc_y,
                "mode": "markers",
                "marker": {"size": CHART_MARKER_MD, "color": color, "symbol": "x",
                           "line": {"width": CHART_LINE_WIDTH_REF, "color": "#d63031"}},
                "hovertemplate": f"{lane_label} OOC: %{{y:.2f}}%<extra></extra>",
                "showlegend": False,
            }))

    # p-bar dotted line
    traces.append(json.dumps({
        "type": "scatter",
        "name": f"p̄ = {avg_p_bar:.2f}%",
        "x": [x_labels[0], x_labels[-1]],
        "y": [avg_p_bar, avg_p_bar],
        "mode": "lines",
        "line": {"color": "#636e72", "width": CHART_LINE_WIDTH_REF, "dash": "dot"},
        "hovertemplate": f"p̄: {avg_p_bar:.2f}%<extra></extra>",
    }))

    layout = json.dumps({
        "margin": {"l": 80, "r": 20, "t": 10, "b": 200},
        "yaxis": {"title": "Reject rate %", "rangemode": "tozero", "gridcolor": "#eee", "automargin": True},
        "xaxis": {"type": "category", "tickangle": -45, "tickfont": {"size": CHART_TICKFONT}, "automargin": True},
        "plot_bgcolor": "white",
        "font": {"size": CHART_FONT_SIZE},
        "height": CHART_H_WEEKLY_SPC,
        "legend": {"orientation": "h", "y": -0.55, "font": {"size": CHART_LEGEND_FONT}},
        "hovermode": False,
    })

    traces_str = ", ".join(traces)
    missing_shifts = total_shifts - n_shifts
    gap_note = (
        f'<div style="margin-bottom:6px;padding:7px 12px;background:#fff8e1;'
        f'border-left:3px solid #f39c12;font-size:0.82rem;border-radius:0 4px 4px 0;">'
        f'<strong>&#9888; {missing_shifts} of {total_shifts} shifts have no data.</strong> '
        f'Control limits computed from {n_shifts} valid shifts only.</div>'
    ) if missing_shifts > 0 else ""

    table_html = _build_spc_shift_table(spc_data.get("shift_table", []), lane_prefix, ooc_lane_labels)
    return f"""<div class="panel full-width">
  <h3>{title} <span style="font-weight:400;font-size:0.75rem;color:#636e72;">(Laney p\', {n_shifts} of {total_shifts} shifts)</span></h3>
  <div class="panel-render">
    {gap_note}
    <div id="{div_id}" style="width:100%;"></div>
    {table_html}
  </div>
</div>
{_plotly_script(div_id, traces_str, layout)}"""


def _build_spc_shift_table(shift_table, lane_prefix, ooc_lane_labels=None):
    """Compact per-shift reject detail table below a Laney p' chart.

    Columns: Shift | Inspected | L1 | L2 | ... — newest shift first.
    OOC cells highlighted in orange (colorblind-safe).
    """
    if not shift_table:
        return ""
    lane_nums = [l["lane"] for l in shift_table[0]["per_lane"]]
    th_style = "padding:4px 8px;text-align:center;white-space:nowrap;"
    headers = "".join(
        f'<th style="{th_style}">{lane_prefix}{ln}</th>' for ln in lane_nums
    )
    rows_html = ""
    for row in shift_table:
        cells = ""
        for lane in row["per_lane"]:
            if lane["ooc"]:
                cell_style = "background:#e17055;color:#fff;text-align:center;padding:3px 6px;font-size:0.76rem;"
            else:
                cell_style = "text-align:center;padding:3px 6px;font-size:0.76rem;color:#636e72;"
            cells += (
                f'<td style="{cell_style}">'
                f'{lane["rejects"]:,}<br>'
                f'<span style="font-weight:600;">{lane["rate_pct"]:.2f}%</span>'
                f'</td>'
            )
        rows_html += (
            f'<tr style="border-bottom:1px solid #f0f0f0;">'
            f'<td style="padding:3px 8px;white-space:nowrap;font-size:0.78rem;">{row["label"]}</td>'
            f'<td style="padding:3px 8px;text-align:right;font-size:0.78rem;">{row["n_inspected"]:,}</td>'
            f'{cells}'
            f'</tr>'
        )
    return (
        f'<div style="overflow-x:auto;margin-top:0.8rem;border-top:1px solid #eee;padding-top:0.6rem;">'
        f'<table style="width:100%;font-size:0.78rem;border-collapse:collapse;min-width:300px;">'
        f'<thead><tr style="background:#f5f6fa;border-bottom:2px solid #dfe6e9;">'
        f'<th style="padding:4px 8px;text-align:left;">Shift</th>'
        f'<th style="padding:4px 8px;text-align:right;">Inspected</th>'
        f'{headers}'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table>'
        f'<div style="font-size:0.72rem;color:#636e72;margin-top:4px;">Cells: rejects / rate&nbsp;&mdash;&nbsp;'
        f'<span style="background:#e17055;color:#fff;padding:1px 5px;border-radius:3px;">orange</span>'
        f'&nbsp;= out of control</div>'
        f'</div>'
    )


def _build_weekly_ewma_chart(ewma, lanes, lane_prefix, parent_title, div_id, x_window=None):
    """EWMA per-lane multi-line chart — Plotly (inline JS), same structure as shift EWMA.

    x_window: if provided (a set of shift labels), clips EWMA to only the batches
    whose shift label falls within that set — keeps EWMA aligned with the Laney p' range.
    """
    if not ewma:
        return ""

    shifts = ewma["shifts"]
    if x_window:
        shifts = [s for s in shifts if _ts_to_shift_label(s["t_stamp"]) in x_window]
    if not shifts:
        return ""
    ucls = ewma["ucl"]
    lam = ewma.get("lambda", 0.2)
    n_lanes = len(lanes)

    x_labels = [_ts_to_shift_label(s["t_stamp"]) for s in shifts]

    traces = []
    for i in range(n_lanes):
        lane_label = f"{lane_prefix}{lanes[i]['lane']}"
        ewma_vals = [s["ewma"][i] for s in shifts]
        color = EWMA_LANE_COLORS[i % len(EWMA_LANE_COLORS)]
        ucl_color = _hex_to_rgba(color, 0.3)

        traces.append(json.dumps({
            "type": "scatter",
            "name": lane_label,
            "x": x_labels,
            "y": ewma_vals,
            "mode": "lines+markers",
            "marker": {"size": CHART_MARKER_SM, "color": color},
            "line": {"color": color, "width": CHART_LINE_WIDTH},
            "hovertemplate": f"{lane_label}: %{{y:.2f}}%<extra></extra>",
        }))

        if i < len(ucls) and ucls[i] > 0:
            is_first_ucl = i == 0
            traces.append(json.dumps({
                "type": "scatter",
                "name": "UCL (per lane)" if is_first_ucl else f"UCL {lane_label}",
                "x": [x_labels[0], x_labels[-1]],
                "y": [ucls[i], ucls[i]],
                "mode": "lines",
                "line": {"color": ucl_color, "width": CHART_LINE_WIDTH_UCL, "dash": "dash"},
                "showlegend": is_first_ucl,
                "legendgroup": "ucl-lane",
                "hovertemplate": f"UCL {lane_label}: {ucls[i]:.2f}%<extra></extra>",
            }))

            breach_x = [x_labels[j] for j in range(len(ewma_vals)) if ewma_vals[j] > ucls[i]]
            breach_y = [ewma_vals[j] for j in range(len(ewma_vals)) if ewma_vals[j] > ucls[i]]
            if breach_x:
                traces.append(json.dumps({
                    "type": "scatter",
                    "x": breach_x,
                    "y": breach_y,
                    "mode": "markers",
                    "marker": {"size": CHART_MARKER_MD, "color": color, "symbol": "x",
                               "line": {"width": CHART_LINE_WIDTH_REF, "color": "#d63031"}},
                    "hovertemplate": f"{lane_label} BREACH: %{{y:.2f}}% (UCL {ucls[i]:.2f}%)<extra></extra>",
                    "showlegend": False,
                    "legendgroup": "ucl-lane",
                }))

    layout = json.dumps({
        "margin": {"l": 100, "r": 20, "t": 10, "b": 200},
        "yaxis": {"title": "EWMA reject rate %", "rangemode": "tozero", "gridcolor": "#eee", "automargin": True},
        "xaxis": {"type": "category", "tickangle": -45, "tickfont": {"size": CHART_TICKFONT}, "automargin": True},
        "plot_bgcolor": "white",
        "font": {"size": CHART_FONT_SIZE},
        "height": CHART_H_WEEKLY_EWMA,
        "legend": {"orientation": "h", "y": -0.55, "font": {"size": CHART_LEGEND_FONT}},
        "hovermode": False,
    })

    traces_str = ", ".join(traces)
    return f"""<div class="panel full-width">
  <h3>{parent_title} &mdash; EWMA Trend <span style="font-weight:400;font-size:0.75rem;color:#636e72;">(&lambda;={lam}, drift detection across batches)</span></h3>
  <div class="panel-render">
    <div id="{div_id}" style="width:100%;"></div>
  </div>
</div>
{_plotly_script(div_id, traces_str, layout)}"""


def _build_weekly_spc_section(data):
    """Build the full SPC section (all three panels, each with Laney p' + EWMA grouped)."""
    specs = [
        ("weekly_" + p["key"], p.get("weekly_title", p["title"]),
         p["lane_prefix"], p["weekly_div_base"])
        for p in get_spc_panels(CFG)
    ]
    parts = []
    for key, title, prefix, div_base in specs:
        spc = data.get(key)
        if not spc:
            continue
        laney_html = _build_weekly_spc_series(spc, title, prefix, f"{div_base}-laney")
        ewma_html = ""
        if spc.get("ewma"):
            laney_labels = {p["label"] for p in spc["lanes"][0]["points"]} if spc.get("lanes") else None
            ewma_html = _build_weekly_ewma_chart(
                spc["ewma"], spc["lanes"], prefix, title, f"{div_base}-ewma",
                x_window=laney_labels)
        # Wrap both charts in a labelled group box
        parts.append(f"""<div class="spc-group">
  <div class="spc-group-label">{title}</div>
  {laney_html}
  {ewma_html}
</div>""")
    return "\n".join(parts)




# ──────────────────────────────────────────────
# Main assembly
# ──────────────────────────────────────────────

def _build_week_anomaly_html(week, shifts) -> str:
    """Return the weekly anomaly panel HTML.

    Three cases:
    - Week-level outliers found → full anomaly panel with cluster detail.
    - No week-level outliers but shift-level spikes present → explanatory note that
      the spikes averaged out over 14 shifts.
    - No anomalies at all → _build_anomaly_panel returns "" (safe to append).
    """
    anom_data = week.get("anomalies") or {}
    outliers  = anom_data.get("cluster_outliers", [])
    baseline_n = anom_data.get("baseline_weeks", 4)
    n_shift_anomalous = sum(
        1 for s in shifts
        if not s.get("skipped") and s.get("anomalies", {}).get("cluster_outliers")
    )
    if not outliers and n_shift_anomalous > 0:
        return (
            f'<div class="panel">'
            f'<h3>Unusual this week '
            f'<span style="font-weight:400;font-size:0.75rem;color:#636e72;">'
            f'(anomaly detection vs last {baseline_n} weeks)</span></h3>'
            f'<div class="panel-render" style="padding:0.8rem 1rem;">'
            f'<p style="font-size:0.88rem;color:#636e72;margin:0;">'
            f'No sustained cluster trend detected vs the prior {baseline_n} weeks. '
            f'The {n_shift_anomalous} shift-level anomalies below were short-lived — '
            f'they averaged out across the full week and did not form a persistent pattern. '
            f'See &#8220;Unusual shifts&#8221; below for shift-by-shift detail.'
            f'</p></div></div>'
        )
    return _build_anomaly_panel(anom_data, panel_title="Unusual this week",
                                period_label="week", baseline_label="weeks")


def _build_shift_anomalies_panel(shifts):
    """Compact panel listing each shift that had unusual cluster activity this week.

    Complements the weekly-aggregate anomaly panel: individual shift spikes can average
    out over 14 shifts and disappear from the weekly view, yet still be operationally
    significant.  Each row names the shift, the top anomaly, and links to the shift
    dashboard.  Tablet-safe — all information is visible inline.
    """
    seg_color_map = {sd[1]: sd[2] for sd in SEGMENT_DEFS}
    seg_label_map = {sd[1]: sd[0] for sd in SEGMENT_DEFS}

    anomalous = []
    baseline_n = 0
    for s in shifts:
        if s.get("skipped"):
            continue
        anom = s.get("anomalies") or {}
        outliers = anom.get("cluster_outliers", [])
        if outliers:
            anomalous.append(s)
        baseline_n = max(baseline_n, anom.get("baseline_shifts", 0))

    if not anomalous:
        return (
            '<div class="panel full-width" style="margin-top:1rem;">'
            '<h3>Unusual shifts this week '
            f'<span style="font-weight:400;font-size:0.75rem;color:#636e72;">'
            f'(per-shift view · vs prior {baseline_n} shifts)</span></h3>'
            '<div class="panel-render" style="padding:0.8rem 1rem;">'
            f'<p style="font-size:0.88rem;color:#636e72;margin:0;">'
            f'No unusual cluster activity detected in any individual shift vs the prior {baseline_n} shifts.</p>'
            '</div></div>'
        )

    rows_html = ""
    for s in anomalous:
        outliers = s["anomalies"]["cluster_outliers"]
        # Show the anomaly with the highest operational impact: prefer "above" (more stops
        # than usual) ordered by z descending, then fall back to "below" by abs(z) descending.
        above = [o for o in outliers if o["direction"] == "above"]
        top = max(above, key=lambda o: o["z_score"]) if above else max(outliers, key=lambda o: abs(o["z_score"]))
        color = seg_color_map.get(top["segment"], "#636e72")
        seg_label = seg_label_map.get(top["segment"], top["segment"])
        ratio = top["current_share"] / top["baseline_mean"] if top["baseline_mean"] > 0 else 0
        multiplier = f"{ratio:.0f}&times; " if ratio >= 2 else ""
        if top["direction"] == "above":
            headline = f"{multiplier}more {top['cluster']} stops than usual"
        else:
            headline = f"Fewer {top['cluster']} stops than usual"
        detail = (
            f'This shift: {top["current_share"]}% of downtime &middot; '
            f'pre-week avg: {top["baseline_mean"]}%'
        )
        extra = f' <span style="color:#636e72;">+{len(outliers) - 1} more</span>' if len(outliers) > 1 else ""
        link = f'shifts/shift-dashboard-{s["shift_date"]}-{s["shift_type"]}.html'
        rows_html += (
            f'<div style="display:flex;align-items:center;gap:10px;padding:9px 0;'
            f'border-bottom:1px solid #f0f0f0;flex-wrap:wrap;">'
            f'<div style="width:4px;min-height:36px;border-radius:2px;background:{color};flex-shrink:0;"></div>'
            f'<div style="min-width:100px;font-size:0.85rem;font-weight:600;color:#2d3436;">{s["label"]}</div>'
            f'<div style="flex:1;min-width:180px;">'
            f'  <div style="font-size:0.85rem;font-weight:600;color:#2d3436;">'
            f'    {headline} <span style="font-weight:400;color:#636e72;">({seg_label})</span>{extra}'
            f'  </div>'
            f'  <div style="font-size:0.78rem;color:#636e72;margin-top:1px;">{detail}</div>'
            f'</div>'
            f'<a href="{link}" style="font-size:0.8rem;color:#0984e3;white-space:nowrap;'
            f'text-decoration:none;padding:3px 8px;border:1px solid #0984e3;border-radius:3px;">'
            f'View &#8594;</a>'
            f'</div>\n'
        )

    legend = (
        f'<div style="font-size:0.76rem;color:#b2bec3;padding:4px 0 10px;border-bottom:1px solid #f0f0f0;margin-bottom:4px;">'
        f'Each shift is compared to the {baseline_n} shifts before this week. '
        f'A row appears only when a cluster&rsquo;s share of downtime is clearly outside '
        f'the normal range for that reference period &mdash; not just a small daily fluctuation.'
        f'</div>'
    )
    return (
        f'<div class="panel full-width" style="margin-top:1rem;">'
        f'<h3>Unusual shifts this week '
        f'<span style="font-weight:400;font-size:0.75rem;color:#636e72;">'
        f'({len(anomalous)} of {sum(1 for s in shifts if not s.get("skipped"))} shifts &middot; '
        f'fixed pre-week baseline)</span></h3>'
        f'<div class="panel-render" style="padding:0.8rem 1rem;">'
        f'{legend}{rows_html}'
        f'</div></div>'
    )


def _build_key_findings(week, shifts, data):
    """Data-driven key findings panel for the Weekly Summary section."""
    items = []

    # 1. Top downtime anomaly
    outliers = week.get("anomalies", {}).get("cluster_outliers", [])
    if outliers:
        a = outliers[0]
        ratio = a["current_share"] / a["baseline_mean"] if a["baseline_mean"] > 0 else 0
        if a["direction"] == "above" and ratio >= 2:
            items.append(
                f'<strong>{a["cluster"]}</strong> stops in {a["segment"].title()} accounted for '
                f'{a["current_share"]}% of downtime — {ratio:.0f}&times; the normal level '
                f'({a["baseline_mean"]}%)')
        elif a["direction"] == "above":
            items.append(
                f'<strong>{a["cluster"]}</strong> stops in {a["segment"].title()} ran above normal: '
                f'{a["current_share"]}% vs typical {a["baseline_mean"]}%')
        else:
            items.append(
                f'<strong>{a["cluster"]}</strong> stops in {a["segment"].title()} below normal: '
                f'{a["current_share"]}% vs typical {a["baseline_mean"]}%')

    # 2. OEE vs prior week
    prior = week.get("prior_week")
    if prior:
        delta = round(week["oee"] - prior["oee"], 1)
        if abs(delta) >= 1.0:
            direction = "improved" if delta > 0 else "declined"
            items.append(
                f'Weekly OEE <strong>{direction} by {abs(delta):.1f}pp</strong> vs '
                f'prior week ({prior["from"][5:]} &ndash; {prior["to"][5:]}): '
                f'{week["oee"]}% vs {prior["oee"]}%')

    # 3. SPC out-of-control lanes
    ooc_parts = []
    for p in get_spc_panels(CFG):
        spc = data.get(f"weekly_{p['key']}")
        if spc:
            ooc = [str(l["lane"]) for l in spc["lanes"] if l.get("any_out_of_control")]
            if ooc:
                ooc_parts.append(f'{p["title"]} lanes {", ".join(ooc)}')
    if ooc_parts:
        items.append(
            f'Quality alert: <strong>{" &middot; ".join(ooc_parts)}</strong> '
            f'exceeded Laney p\' control limits this week')

    # 4. Weakest shift
    production = [s for s in shifts if not s.get("skipped")]
    if production:
        worst = min(production, key=lambda s: s["oee"])
        items.append(
            f'Weakest shift: <strong>{worst["label"]}</strong> at {worst["oee"]}% OEE '
            f'(A={worst["availability"]}% &middot; P={worst["performance"]}% &middot; Q={worst["quality"]}%)')

    if not items:
        return ""

    items_html = "".join(
        f'<li style="margin-bottom:0.5rem;">{it}</li>' for it in items[:4])
    return f"""<div class="panel full-width" style="margin-top:1rem;">
  <h3>Key Findings</h3>
  <div class="panel-render" style="padding:0.8rem 1.2rem;">
    <ul style="padding-left:1.2rem;font-size:0.88rem;line-height:1.6;color:#2d3436;">
      {items_html}
    </ul>
  </div>
</div>"""


def main():
    data = _load_data()
    week = data["week"]
    shifts = data["shifts"]

    date_from = week["from"]
    date_to = week["to"]
    prod_shifts = week.get("production_shifts", len([s for s in shifts if not s.get("skipped")]))
    excl_shifts = week.get("excluded_shifts", 0)

    meta_parts = [f'{date_from} &ndash; {date_to}']
    if excl_shifts:
        meta_parts.append(f'{excl_shifts} non-production shift(s) excluded')
    meta_parts.append(f'Generated: {data["generated_at"][:19]}')
    meta_str = " &middot; ".join(meta_parts)

    latest_shift = next((s for s in shifts if not s.get("skipped")), shifts[0])
    shift_link = f"shifts/shift-dashboard-{latest_shift['shift_date']}-{latest_shift['shift_type']}.html"

    html_parts = []

    # Head
    html_parts.append(build_html_head(f"Weekly Dashboard — {CFG['label']} Line"))
    html_parts.append(f"""
<script>if(typeof Plotly==='undefined'){{var _b=document.createElement('div');_b.style.cssText='position:fixed;top:0;left:0;right:0;z-index:9999;background:#d63031;color:#fff;padding:14px;text-align:center;font-size:1rem;';_b.innerHTML='&#9888; Plotly.js failed to load &mdash; charts will not display. Open browser console (F12) for details.';document.addEventListener('DOMContentLoaded',function(){{document.body.prepend(_b);}});}}</script>
<div class="mw-header-bar"></div>
<header>
  <img src="{MW_LOGO_BASE64}" alt="MaibornWolff" class="mw-logo">
  <div class="header-divider"></div>
  <div class="header-text">
    <h1>Weekly Dashboard &mdash; {CFG["label"]} Line</h1>
    <div class="meta">{meta_str}</div>
  </div>
</header>
<nav class="dash-nav">
  <a href="{shift_link}">&#8592; Latest Shift Dashboard</a>
</nav>
<main>""")

    # ── Section 1: Weekly KPIs ───────────────────
    html_parts.append(f"""<section class="section">
  <div class="section-header" style="display:flex;align-items:baseline;gap:1.2rem;">
    <h2>Weekly Summary</h2>
    <span style="font-size:1.1rem;font-weight:600;color:#1E194B;letter-spacing:0.01em;">{date_from} &ndash; {date_to}</span>
  </div>
  <div class="section-body">""")
    html_parts.append(_build_weekly_kpi_tiles(week))
    anomaly_html = _build_week_anomaly_html(week, shifts)
    if anomaly_html:
        html_parts.append(anomaly_html)
    shift_anomaly_html = _build_shift_anomalies_panel(shifts)
    html_parts.append(shift_anomaly_html)
    findings_html = _build_key_findings(week, shifts, data)
    if findings_html:
        html_parts.append(findings_html)
    html_parts.append("""  </div>
</section>""")

    # ── Section 2: OEE Trend ─────────────────────
    html_parts.append("""<section class="section">
  <div class="section-header"><h2>OEE Trend</h2></div>
  <div class="section-body">
    <div class="panel-grid">""")
    html_parts.append(_build_oee_trend(shifts))
    html_parts.append("""    </div>
  </div>
</section>""")

    # ── Section 3: Downtime by Category + Stop Summary ──
    html_parts.append("""<section class="section">
  <div class="section-header"><h2>Downtime</h2></div>
  <div class="section-body">
    <div class="panel-grid">""")
    html_parts.append(_build_downtime_categories(week["downtime_categories"]))
    html_parts.append(f"""<div class="panel">
  <h3>Stop Summary</h3>
  <div class="panel-render" style="padding:1rem;">
    {build_stop_summary(week)}
  </div>
</div>""")
    html_parts.append("""    </div>""")
    html_parts.append(_build_segment_downtime(week.get("segment_downtime", {})))
    # Collapsible cluster reference table
    top_stops = week.get("top_stops_by_cluster", {})
    html_parts.append(_build_cluster_reference_table(week.get("segment_downtime", {}), top_stops,
                                                     period_label="this week"))
    html_parts.append("""  </div>
</section>""")

    # ── Section 4: Daily Breakdown ───────────────
    html_parts.append("""<section class="section">
  <div class="section-header"><h2>Daily Breakdown</h2></div>
  <div class="section-body">
    <div class="panel-grid">""")
    html_parts.append(_build_daily_breakdown(shifts))
    html_parts.append("""    </div>
  </div>
</section>""")

    # ── Section 5: Statistical Process Control ──
    spc_html = _build_weekly_spc_section(data)
    if spc_html:
        html_parts.append("""<section class="section">
  <div class="section-header"><h2>Statistical Process Control</h2></div>
  <div class="section-body">
    <div style="font-size:0.85rem;color:#636e72;line-height:1.8;margin-bottom:1.2rem;max-width:900px;">
      Each group shows two complementary charts for the same quality stream.<br>
      <strong>Laney p&rsquo; charts</strong> treat each shift as a subgroup: a point above the
      <span style="color:#023e8a;font-weight:600;">dashed UCL</span> means that shift&rsquo;s reject rate
      was statistically abnormal relative to the week&rsquo;s baseline.<br>
      <strong>EWMA trend charts</strong> use the same batch-level data as the shift dashboard,
      applying exponential smoothing (&lambda;=0.2) to reveal slow drift that individual shift snapshots may miss;
      an <span style="color:#d63031;font-weight:600;">&#10005; breach marker</span> appears whenever
      the smoothed rate crosses the per-lane UCL.
    </div>""")
        html_parts.append(spc_html)
        html_parts.append("""  </div>
</section>""")

    # Footer
    html_parts.append(f"""</main>
<footer>
  Weekly Dashboard &mdash; {CFG["label"]} Line &middot;
  {date_from} &ndash; {date_to} &middot;
  {prod_shifts} production shifts &middot;
  Generated {data["generated_at"][:10]} &middot;
  MaibornWolff
</footer>
</body>
</html>""")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    html_content = "\n".join(html_parts)
    fname = f"weekly-dashboard-{date_from}-to-{date_to}.html"
    out_path = os.path.join(OUTPUT_DIR, fname)
    _assert_self_contained(html_content, out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    # Also write canonical weekly-dashboard.html so shift dashboards can link back reliably
    canonical = os.path.join(OUTPUT_DIR, "weekly-dashboard.html")
    with open(canonical, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Wrote {out_path} ({os.path.getsize(out_path):,} bytes)")
    print(f"Wrote {canonical} ({os.path.getsize(canonical):,} bytes)")


if __name__ == "__main__":
    main()
