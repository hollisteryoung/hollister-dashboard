"""
Shared HTML/CSS panel builders for shift and weekly dashboards.

All functions return HTML strings. They depend on constants from theme.py
(SEGMENT_DEFS, DOWNTIME_CATEGORIES, etc.) which are resolved at import time.
"""

import re as _re

from rendering.theme import (
    SEGMENT_DEFS,
    DOWNTIME_CATEGORIES,
    SHIFT_MIDPOINT_HOUR,
)
from domain.clusters import get_cause_clusters_display


# ──────────────────────────────────────────────
# Color utilities
# ──────────────────────────────────────────────

def _hex_blend(hex_color, ratio):
    """Blend hex_color toward white (ratio>0) or black (ratio<0)."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    if ratio >= 0:
        r = int(r + (255 - r) * ratio)
        g = int(g + (255 - g) * ratio)
        b = int(b + (255 - b) * ratio)
    else:
        factor = 1 + ratio
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
    return f"#{max(0,min(255,r)):02x}{max(0,min(255,g)):02x}{max(0,min(255,b)):02x}"


# ──────────────────────────────────────────────
# KPI helpers
# ──────────────────────────────────────────────

def _kpi_color(value):
    if value >= 80:
        return "#0077b6"
    if value >= 60:
        return "#264653"
    return "#e76f51"


def _delta_html(current, previous, prev_label, higher_is_better=True):
    delta = current - previous
    if abs(delta) < 0.05:
        return f'<span class="kpi-delta" style="color:#636e72;">&#8211; 0.0pp vs {prev_label}</span>'
    if (delta > 0) == higher_is_better:
        return f'<span class="kpi-delta up">&#9650; +{delta:.1f}pp vs {prev_label}</span>'
    return f'<span class="kpi-delta down">&#9660; {delta:.1f}pp vs {prev_label}</span>'


# ──────────────────────────────────────────────
# Timestamp → shift label
# ──────────────────────────────────────────────

def _ts_to_shift_label(ts_str):
    """Convert a counter reset timestamp to a shift label like 'Night 04-12'.

    A reset before the day-shift midpoint ends the previous day's night shift;
    one after it ends that day's day shift.
    """
    from datetime import datetime, timedelta
    dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
    if dt.hour < SHIFT_MIDPOINT_HOUR:
        shift_date = (dt - timedelta(days=1)).strftime("%m-%d")
        return f"Night {shift_date}"
    shift_date = dt.strftime("%m-%d")
    return f"Day {shift_date}"


# ──────────────────────────────────────────────
# Downtime categories horizontal bar chart
# ──────────────────────────────────────────────

def _build_downtime_categories(dt_cats):
    max_hours = max((dt_cats.get(k, {}).get("hours", 0) for _, k, _ in DOWNTIME_CATEGORIES), default=1) or 1

    rows_html = ""
    for label, key, color in DOWNTIME_CATEGORIES:
        cat = dt_cats.get(key, {"count": 0, "hours": 0})
        pct = cat["hours"] / max_hours * 100
        text_color = "white" if key in ("micro_stops", "halted") else "#2d3436"
        bg_style = f"background:{color};"
        right_text = f'{cat["hours"]:.1f}h ({cat["count"]} stops)'
        rows_html += (
            f'<div style="display:flex;align-items:center;margin-bottom:8px;">'
            f'<div style="width:190px;flex-shrink:0;font-size:1rem;color:#2d3436;text-align:right;padding-right:12px;">{label}</div>'
            f'<div style="flex:1;height:44px;background:#f5f5f5;border-radius:2px;overflow:hidden;">'
            f'<div style="display:inline-block;width:{pct:.1f}%;height:100%;{bg_style}'
            f'font-size:0.9rem;color:{text_color};line-height:44px;padding:0 8px;'
            f'white-space:nowrap;overflow:hidden;"></div>'
            f'</div>'
            f'<div style="width:130px;flex-shrink:0;font-size:0.95rem;color:#636e72;padding-left:8px;">{right_text}</div>'
            f'</div>\n'
        )

    return f"""<div class="panel">
  <h3>Downtime by Category</h3>
  <div class="panel-render" style="padding:0.8rem 1rem;">
    {rows_html}
  </div>
</div>"""


# ──────────────────────────────────────────────
# Downtime by segment (stacked horizontal bars)
# ──────────────────────────────────────────────

def _build_segment_downtime(seg_dt):
    max_hours = max((seg_dt.get(k, {}).get("hours", 0) for _, k, _ in SEGMENT_DEFS), default=1) or 1

    def _bar_divs(clusters_sorted, shades, max_h, hours_key):
        """Build inline-block divs for one sub-row from cluster data."""
        html = ""
        for (c_name, c_data), shade in zip(clusters_sorted, shades):
            h = c_data.get(hours_key, 0)
            if h <= 0:
                continue
            pct = h / max_h * 100
            if pct < 0.3:
                continue
            txt = f"{c_name} {h:.1f}h" if pct > 10 else (c_name if pct > 4 else "")
            html += (
                f'<div style="display:inline-block;width:{pct:.2f}%;height:100%;'
                f'background:{shade};vertical-align:top;overflow:hidden;white-space:nowrap;'
                f'font-size:1rem;color:white;line-height:52px;padding:0 8px;'
                f'box-sizing:border-box;border-right:1px solid rgba(255,255,255,0.3);"'
                f'>{txt}</div>'
            )
        return html

    rows_html = ""
    for label, key, base_color in SEGMENT_DEFS:
        cat = seg_dt.get(key, {"count": 0, "hours": 0})
        if cat["hours"] == 0 and cat["count"] == 0:
            continue

        halted_total = cat.get("halted_hours", 0)
        stopped_total = cat.get("stopped_hours", 0)
        clusters = cat.get("clusters", {})

        if clusters:
            sorted_c = sorted(clusters.items(), key=lambda x: -x[1]["hours"])
            n = len(sorted_c)
            shades = [_hex_blend(base_color, i * 0.50 / max(n - 1, 1) if n > 1 else 0) for i in range(n)]
        else:
            sorted_c = [(label, cat)]
            shades = [base_color]

        halted_bar = _bar_divs(sorted_c, shades, max_hours, "halted_hours")
        stopped_bar = _bar_divs(sorted_c, shades, max_hours, "stopped_hours")

        halted_text = f"{halted_total:.1f}h" if halted_total > 0 else "—"
        stopped_text = f"{stopped_total:.1f}h" if stopped_total > 0 else "—"

        rows_html += (
            f'<div style="display:flex;margin-bottom:18px;">'
            f'<div style="width:150px;flex-shrink:0;text-align:right;padding-right:14px;padding-top:6px;">'
            f'<div style="font-size:1.1rem;font-weight:600;color:#2d3436;">{label}</div>'
            f'<div style="font-size:1rem;color:#636e72;">{cat["hours"]:.1f}h total</div>'
            f'</div>'
            f'<div style="flex:1;display:flex;flex-direction:column;gap:6px;">'
        )

        if halted_total > 0:
            rows_html += (
                f'<div style="display:flex;align-items:center;">'
                f'<div style="width:75px;flex-shrink:0;font-size:1rem;color:#2d3436;font-weight:700;text-align:right;padding-right:8px;">halted</div>'
                f'<div style="flex:1;height:52px;background:#f5f5f5;border-radius:2px;font-size:0;line-height:0;overflow:hidden;">{halted_bar}</div>'
                f'<div style="width:70px;flex-shrink:0;font-size:1rem;color:#636e72;padding-left:8px;">{halted_text}</div>'
                f'</div>'
            )

        if stopped_total > 0:
            rows_html += (
                f'<div style="display:flex;align-items:center;">'
                f'<div style="width:75px;flex-shrink:0;font-size:1rem;color:#636e72;text-align:right;padding-right:8px;">stopped</div>'
                f'<div style="flex:1;height:52px;background:#f5f5f5;border-radius:2px;font-size:0;line-height:0;overflow:hidden;">{stopped_bar}</div>'
                f'<div style="width:70px;flex-shrink:0;font-size:1rem;color:#636e72;padding-left:8px;">{stopped_text}</div>'
                f'</div>'
            )

        rows_html += '</div></div>\n'

    legend_html = (
        '<div style="display:flex;gap:1.2rem;flex-wrap:wrap;align-items:center;font-size:1rem;color:#636e72;">'
        f'<span><span style="font-weight:700;color:#2d3436;">halted</span> = operator wait (machine parked)</span>'
        '<span><span style="font-weight:600;">stopped</span> = auto-stop (fault recovery)</span>'
        '</div>'
    )

    return f"""<div class="panel full-width">
  <h3>Downtime by Segment</h3>
  <div class="panel-render" style="padding:1rem 1rem 0.5rem;">
    {rows_html}
    <div style="margin-top:8px;padding-top:8px;border-top:1px solid #eee;">{legend_html}</div>
  </div>
</div>"""


# ──────────────────────────────────────────────
# Shared: rotated label inside a vertical bar segment
# ──────────────────────────────────────────────

def _bar_segment_label(name, value_str, seg_px):
    """Horizontal label centered inside a vertical bar segment.
    Font scales with segment height. Two-line (name + value) when tall enough."""
    if seg_px < 26:
        return ""
    if seg_px >= 180:
        font_size = "0.88rem"
    elif seg_px >= 110:
        font_size = "0.78rem"
    elif seg_px >= 65:
        font_size = "0.68rem"
    else:
        font_size = "0.58rem"
    base = f"font-size:{font_size};white-space:nowrap;overflow:hidden;display:block;text-align:center;line-height:1.35;"
    if seg_px >= 65:
        lines = (
            f'<span style="{base}font-weight:600;color:rgba(255,255,255,0.93);">{name}</span>'
            f'<span style="{base}font-weight:400;color:rgba(255,255,255,0.70);">{value_str}</span>'
        )
    else:
        lines = f'<span style="{base}font-weight:600;color:rgba(255,255,255,0.93);">{name}</span>'
    return (
        f'<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;'
        f'justify-content:center;overflow:hidden;pointer-events:none;padding:0 3px;">'
        f'{lines}</div>'
    )


# ──────────────────────────────────────────────
# Hourly breakdown stacked column chart
# ──────────────────────────────────────────────

def _build_hourly_chart(hourly_data, div_id="hourly-chart"):
    """Hourly breakdown stacked column chart — pure HTML/CSS, labels inside chart."""
    if not hourly_data:
        return ""

    CHART_PX = 200  # bar area height in px (= 60 minutes)
    LABEL_H = 20    # label row height, inside chart container

    cols_html = ""
    cluster_totals = {}  # seg_key -> {c_name: total_min} — for legend
    for h in hourly_data:
        segs = h.get("segments", {})
        total_dt_min = 0
        seg_divs = ""
        for seg_label, seg_key, seg_color in SEGMENT_DEFS:
            seg_data = segs.get(seg_key)
            if not seg_data:
                continue
            seg_min = seg_data.get("minutes", 0)
            if seg_min <= 0:
                continue
            total_dt_min += seg_min
            seg_pct = min(100, seg_min / 60 * 100)
            seg_px = seg_pct / 100 * CHART_PX
            clusters = seg_data.get("clusters") or {}
            if clusters:
                sorted_c = sorted(clusters.items(), key=lambda x: -x[1])
                n = len(sorted_c)
                shades = [_hex_blend(seg_color, i * 0.50 / max(n - 1, 1) if n > 1 else 0) for i in range(n)]
                cluster_divs = ""
                seg_totals = cluster_totals.setdefault(seg_key, {})
                for (c_name, c_min), shade in zip(sorted_c, shades):
                    seg_totals[c_name] = seg_totals.get(c_name, 0) + c_min
                    c_px = c_min / seg_min * seg_px
                    cluster_divs += (
                        f'<div style="height:{c_min / seg_min * 100:.1f}%;background:{shade};'
                        f'flex-shrink:0;min-height:1px;position:relative;overflow:hidden;">'
                        f'{_bar_segment_label(c_name, f"{c_min:.0f}min", c_px)}</div>'
                    )
                seg_divs += (
                    f'<div style="height:{seg_pct:.1f}%;flex-shrink:0;min-height:2px;'
                    f'position:relative;overflow:hidden;">'
                    f'<div style="display:flex;flex-direction:column;height:100%;">{cluster_divs}</div>'
                    f'</div>'
                )
            else:
                lbl = _bar_segment_label(seg_label, f"{seg_min:.0f}min", seg_px)
                seg_divs += (
                    f'<div style="height:{seg_pct:.1f}%;background:{seg_color};flex-shrink:0;'
                    f'overflow:hidden;min-height:2px;position:relative;">{lbl}</div>'
                )
        running_pct = max(0, (60 - total_dt_min) / 60 * 100)
        col_inner = (
            f'<div style="height:{running_pct:.1f}%;background:#f0f0f0;flex-shrink:0;'
            f'min-height:0;"></div>'
            + seg_divs
        )
        label = h.get("hour_label", "")
        cols_html += (
            f'<div style="display:flex;flex-direction:column;flex:1;min-width:16px;">'
            f'<div style="display:flex;flex-direction:column-reverse;height:{CHART_PX}px;'
            f'overflow:hidden;border-right:1px solid rgba(255,255,255,0.4);">'
            f'{col_inner}</div>'
            f'<div style="height:{LABEL_H}px;text-align:center;font-size:0.62rem;'
            f'color:#636e72;overflow:hidden;white-space:nowrap;padding-top:3px;'
            f'border-top:1px solid #e0e0e0;background:#fafafa;">{label}</div>'
            f'</div>'
        )

    y_lines = "".join(
        f'<div style="position:absolute;left:0;right:0;bottom:{int(m/60*CHART_PX) + LABEL_H}px;'
        f'border-top:1px solid #e8e8e8;z-index:0;"></div>'
        f'<span style="position:absolute;left:2px;width:33px;text-align:right;'
        f'bottom:{int(m/60*CHART_PX) + LABEL_H - 6}px;'
        f'font-size:0.65rem;color:#b2bec3;white-space:nowrap;">{m}m</span>'
        for m in [15, 30, 45, 60]
    )

    legend_items = ""
    for seg_label, seg_key, seg_color in SEGMENT_DEFS:
        ctotals = cluster_totals.get(seg_key, {})
        if ctotals:
            sorted_c = sorted(ctotals.items(), key=lambda x: -x[1])
            n = len(sorted_c)
            for i, (c_name, _) in enumerate(sorted_c):
                shade = _hex_blend(seg_color, i * 0.50 / max(n - 1, 1) if n > 1 else 0)
                legend_items += (
                    f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;margin-bottom:4px;">'
                    f'<span style="display:inline-block;width:10px;height:10px;background:{shade};border-radius:1px;flex-shrink:0;"></span>'
                    f'<span style="font-size:0.82rem;color:#636e72;">{c_name}</span></span>'
                )
        else:
            legend_items += (
                f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;margin-bottom:4px;">'
                f'<span style="display:inline-block;width:10px;height:10px;background:{seg_color};border-radius:1px;flex-shrink:0;"></span>'
                f'<span style="font-size:0.82rem;color:#636e72;">{seg_label}</span></span>'
            )
    legend_items += (
        '<span style="display:inline-flex;align-items:center;gap:4px;margin-bottom:4px;">'
        '<span style="display:inline-block;width:10px;height:10px;background:#f0f0f0;'
        'border:1px solid #ddd;border-radius:1px;flex-shrink:0;"></span>'
        '<span style="font-size:0.82rem;color:#636e72;">Running</span></span>'
    )

    return f"""<div class="panel full-width">
  <h3>Hourly Breakdown</h3>
  <div class="panel-render" style="padding:0.8rem 1rem;">
    <div style="position:relative;padding-left:36px;">
      {y_lines}
      <div style="display:flex;gap:2px;position:relative;z-index:1;">
        {cols_html}
      </div>
    </div>
    <div style="margin-top:10px;padding-top:8px;border-top:1px solid #eee;display:flex;flex-wrap:wrap;">{legend_items}</div>
  </div>
</div>"""


# ──────────────────────────────────────────────
# Anomaly callouts
# ──────────────────────────────────────────────

def _build_anomaly_panel(anomalies_data, panel_title="Unusual this shift",
                         period_label="shift", baseline_label="shifts"):
    if not anomalies_data:
        return ""

    outliers = anomalies_data.get("cluster_outliers", [])
    baseline_n = anomalies_data.get("baseline_shifts") or anomalies_data.get("baseline_weeks", 0)

    if not outliers:
        return (
            f'<div class="panel">'
            f'<h3>{panel_title}</h3>'
            f'<div class="panel-render" style="padding:0.8rem 1rem;">'
            f'<p style="font-size:0.88rem;color:#636e72;margin:0;">'
            f'No unusual cluster activity detected vs the prior {baseline_n} {baseline_label}.</p>'
            f'</div></div>'
        )

    seg_color_map = dict((sd[1], sd[2]) for sd in SEGMENT_DEFS)
    seg_label_map = dict((sd[1], sd[0]) for sd in SEGMENT_DEFS)

    items_html = ""
    for a in outliers[:5]:
        color = seg_color_map.get(a["segment"], "#636e72")
        seg_label = seg_label_map.get(a["segment"], a["segment"])
        ratio = a["current_share"] / a["baseline_mean"] if a["baseline_mean"] > 0 else 0
        multiplier = f"{ratio:.0f}&times;" if ratio >= 2 else ""

        if a["direction"] == "above":
            headline = f"{multiplier} more {a['cluster']} stops than usual"
        else:
            headline = f"Fewer {a['cluster']} stops than usual"

        detail = f'{a["current_share"]}% of this {period_label}\'s downtime, normally {a["baseline_mean"]}%'

        items_html += (
            f'<div style="display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-bottom:1px solid #f0f0f0;">'
            f'<div style="width:4px;min-height:40px;border-radius:2px;background:{color};flex-shrink:0;margin-top:2px;"></div>'
            f'<div>'
            f'<div style="font-size:0.88rem;font-weight:600;color:#2d3436;">{headline}'
            f' <span style="font-weight:400;color:#636e72;">({seg_label})</span></div>'
            f'<div style="font-size:0.78rem;color:#636e72;margin-top:2px;">{detail}</div>'
            f'</div></div>\n'
        )

    return f"""<div class="panel full-width">
  <h3>{panel_title} <span style="font-weight:400;font-size:0.75rem;color:#636e72;">(anomaly detection vs last {baseline_n} {baseline_label})</span></h3>
  <div class="panel-render" style="padding:0.8rem 1rem;">
    {items_html}
  </div>
</div>"""


# ──────────────────────────────────────────────
# Nozzle SPC (Laney p' chart)
# ──────────────────────────────────────────────

def _build_spc_panel(spc_data, title, lane_prefix, div_id):
    """Laney p' SPC — pure CSS vertical bar columns (one bar per lane)."""
    if not spc_data:
        return ""

    if spc_data.get("status") == "no_shift_data":
        last_ts = spc_data.get("last_reset_timestamp", "unknown")[:16]
        gap_h = spc_data.get("gap_hours", 0)
        days = int(gap_h // 24)
        hrs = int(gap_h % 24)
        age = f"{days}d {hrs}h" if days else f"{hrs}h"
        return (
            f'<div class="panel full-width">'
            f'<h3>{title} <span style="font-weight:400;font-size:0.75rem;color:#636e72;">'
            f'(Laney p\')</span></h3>'
            f'<div style="padding:1rem 1.2rem;">'
            f'<div style="padding:10px 14px;background:#fff8e1;border-left:3px solid #f39c12;'
            f'font-size:0.85rem;border-radius:0 4px 4px 0;">'
            f'<strong>&#9888; No SPC reading for this shift.</strong> '
            f'Last detected batch reset: <strong>{last_ts}</strong> ({age} before shift end). '
            f'Possible causes: no batch completed during this shift, a batch straddled the shift '
            f'boundary with too few parts in the in-shift segment (&lt;100/lane), '
            f'or the shift ran with low output volume.'
            f'</div></div></div>'
        )

    lanes = spc_data["lanes"]
    baseline_n = spc_data["baseline_subgroups"]
    avg_p_bar = sum(n["p_bar_pct"] for n in lanes) / len(lanes)

    max_scale = max(
        max((n["ucl_pct"] for n in lanes), default=0.1),
        max((n["rate_pct"] for n in lanes), default=0),
    ) * 1.15 or 0.1

    CHART_H = 260  # px — height of the bar area only
    OOC_H = 18    # reserved above bar for OOC badge
    LABEL_H = 20  # lane label row height below bar
    RATE_H = 18   # rate value row height below bar

    p_bar_pct = min(100, avg_p_bar / max_scale * 100)

    y_axis_html = ""
    for frac in [1.0, 0.75, 0.5, 0.25, 0.0]:
        val = frac * max_scale
        top_px = OOC_H + int((1.0 - frac) * CHART_H) - 7
        y_axis_html += (
            f'<div style="position:absolute;right:4px;top:{top_px}px;'
            f'font-size:0.62rem;color:#b2bec3;white-space:nowrap;">'
            f'{val:.2f}%</div>'
        )

    grid_html = "".join(
        f'<div style="position:absolute;left:0;right:0;'
        f'top:{OOC_H + int((1.0 - frac) * CHART_H)}px;'
        f'border-top:1px solid #eee;z-index:0;pointer-events:none;"></div>'
        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]
    )

    cols_html = ""
    for n in lanes:
        lane_label = f"{lane_prefix}{n['lane']}"
        rate = n["rate_pct"]
        ucl = n["ucl_pct"]
        ooc = n["out_of_control"]
        bar_color = "#023e8a" if ooc else "#1982c4"
        bar_pct = min(100, rate / max_scale * 100)
        ucl_pct_pos = min(100, ucl / max_scale * 100)
        label_w = "font-weight:700;" if ooc else ""

        ooc_row = (
            f'<div style="height:{OOC_H}px;text-align:center;position:relative;z-index:3;">'
            f'<span style="font-size:0.60rem;background:#023e8a;color:white;'
            f'padding:0 4px;border-radius:2px;line-height:{OOC_H}px;">OOC</span>'
            f'</div>'
        ) if ooc else f'<div style="height:{OOC_H}px;position:relative;z-index:3;"></div>'

        cols_html += (
            f'<div style="flex:1;min-width:22px;display:flex;flex-direction:column;">'
            f'{ooc_row}'
            f'<div style="height:{CHART_H}px;position:relative;background:#f5f5f5;'
            f'overflow:hidden;border-radius:2px 2px 0 0;">'
            f'<div style="position:absolute;bottom:0;left:0;right:0;height:{bar_pct:.1f}%;'
            f'background:{bar_color};border-radius:2px 2px 0 0;"></div>'
            f'<div style="position:absolute;bottom:{ucl_pct_pos:.1f}%;left:0;right:0;'
            f'border-top:2px dashed #0055ff;z-index:2;"></div>'
            f'<div style="position:absolute;bottom:{p_bar_pct:.1f}%;left:0;right:0;'
            f'border-top:2px dotted #636e72;z-index:1;"></div>'
            f'</div>'
            f'<div style="height:{LABEL_H}px;text-align:center;font-size:0.72rem;{label_w}'
            f'padding-top:4px;overflow:hidden;white-space:nowrap;">{lane_label}</div>'
            f'<div style="height:{RATE_H}px;text-align:center;font-size:0.68rem;'
            f'color:#636e72;">{rate:.2f}%</div>'
            f'</div>'
        )

    flagged = [n for n in lanes if n["out_of_control"]]
    if flagged:
        flag_text = ", ".join(f"{lane_prefix}{n['lane']} ({n['rate_pct']:.2f}%)" for n in flagged)
        alert_html = (
            f'<div style="margin-top:8px;padding:8px 12px;background:#e8f0fe;border-left:3px solid #023e8a;'
            f'font-size:0.85rem;color:#2d3436;border-radius:0 4px 4px 0;">'
            f'<strong>Above UCL:</strong> {flag_text}</div>'
        )
    else:
        alert_html = (
            '<div style="margin-top:8px;font-size:0.85rem;color:#636e72;">'
            'All lanes within control limits</div>'
        )

    legend_html = (
        '<div style="margin-top:8px;font-size:0.82rem;color:#636e72;line-height:1.8;">'
        '<span style="display:inline-block;width:18px;height:0;border-top:2px dashed #0055ff;'
        'vertical-align:middle;margin-right:5px;"></span>'
        f'<strong>UCL</strong> — upper control limit (last {baseline_n} batches)'
        '&nbsp;&nbsp;&nbsp;'
        '<span style="display:inline-block;width:18px;height:0;border-top:2px dotted #636e72;'
        'vertical-align:middle;margin-right:5px;"></span>'
        f'<strong>p̄</strong> = {avg_p_bar:.2f}% baseline average reject rate'
        '&nbsp;&nbsp;&nbsp;'
        '<span style="display:inline-block;background:#023e8a;color:white;font-size:0.60rem;'
        'padding:0 4px;border-radius:2px;vertical-align:middle;margin-right:5px;">OOC</span>'
        '<strong>OOC</strong> = Out of Control (lane rate exceeds its UCL)'
        '</div>'
    )

    return f"""<div class="panel full-width">
  <h3>{title} <span style="font-weight:400;font-size:0.75rem;color:#636e72;">(Laney p\', {baseline_n} baseline batches)</span></h3>
  <div class="panel-render" style="padding:0.8rem 1rem 0.6rem;">
    <div style="display:flex;gap:0;">
      <div style="width:42px;flex-shrink:0;position:relative;height:{OOC_H + CHART_H}px;">{y_axis_html}</div>
      <div style="flex:1;position:relative;display:flex;gap:4px;">
        {grid_html}
        {cols_html}
      </div>
    </div>
    {alert_html}
    {legend_html}
  </div>
</div>"""


# ──────────────────────────────────────────────
# Trend: Downtime mix stacked horizontal bars
# ──────────────────────────────────────────────

def _build_downtime_trend(shifts, div_id="dt-trend"):
    """4-shift downtime mix — pure HTML/CSS horizontal stacked bars."""
    trend_shifts = [s for s in reversed(shifts[:4]) if not s.get("skipped")]
    if not trend_shifts:
        return ""

    max_hours = max(
        (sum(s["downtime_categories"].get(k, {}).get("hours", 0) for _, k, _ in DOWNTIME_CATEGORIES)
         for s in trend_shifts),
        default=1,
    ) or 1

    rows_html = ""
    for s in trend_shifts:
        dt = s["downtime_categories"]
        total_dt = sum(dt.get(k, {}).get("hours", 0) for _, k, _ in DOWNTIME_CATEGORIES)
        bar_segs = ""
        for cat_label, key, color in DOWNTIME_CATEGORIES:
            h = dt.get(key, {}).get("hours", 0)
            if h <= 0:
                continue
            pct = h / max_hours * 100
            txt_color = "white" if key in ("micro_stops", "halted") else "#2d3436"
            txt = f"{h:.1f}h" if pct > 12 else ""
            bar_segs += (
                f'<div style="display:inline-block;width:{pct:.2f}%;height:100%;background:{color};'
                f'vertical-align:top;overflow:hidden;white-space:nowrap;font-size:0.85rem;'
                f'color:{txt_color};line-height:40px;padding:0 6px;box-sizing:border-box;'
                f'border-right:1px solid rgba(255,255,255,0.25);">{txt}</div>'
            )
        rows_html += (
            f'<div style="display:flex;align-items:center;margin-bottom:8px;">'
            f'<div style="width:120px;flex-shrink:0;font-size:1rem;font-weight:600;'
            f'text-align:right;padding-right:12px;color:#2d3436;">{s["label"]}</div>'
            f'<div style="flex:1;height:40px;background:#f5f5f5;border-radius:2px;overflow:hidden;">'
            f'{bar_segs}</div>'
            f'<div style="width:55px;flex-shrink:0;font-size:0.9rem;color:#636e72;padding-left:8px;">'
            f'{total_dt:.1f}h</div>'
            f'</div>\n'
        )

    legend_items = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px;">'
        f'<span style="display:inline-block;width:12px;height:12px;background:{color};border-radius:2px;"></span>'
        f'<span style="font-size:0.85rem;color:#636e72;">{cat_label}</span></span>'
        for cat_label, _, color in DOWNTIME_CATEGORIES
    )

    return f"""<div class="panel">
  <h3>Downtime Mix (Last 4 Shifts)</h3>
  <div class="panel-render" style="padding:0.8rem 1rem;">
    {rows_html}
    <div style="margin-top:8px;padding-top:8px;border-top:1px solid #eee;">{legend_items}</div>
  </div>
</div>"""


# ──────────────────────────────────────────────
# Cluster reference table
# ──────────────────────────────────────────────

def _pat_keywords(pat):
    """Convert compiled regex to a short human-readable keyword string."""
    s = pat.pattern
    s = _re.sub(r'\\b', '', s)
    s = _re.sub(r'\\s\\\*', ' ', s)
    s = _re.sub(r'\\\*', '', s)
    s = _re.sub(r'\.\*', '…', s)
    s = _re.sub(r'\\s', ' ', s)
    parts = [p.strip().strip('()') for p in s.split('|') if p.strip()]
    return ', '.join(parts)


def _build_cluster_reference_table(seg_dt, top_stops_by_cluster, period_label="this shift"):
    """Collapsible table: segment × cluster, with keyword triggers and top alarm labels."""
    seg_label_map = {sd[1]: sd[0] for sd in SEGMENT_DEFS}
    seg_color_map = {sd[1]: sd[2] for sd in SEGMENT_DEFS}

    rows_html = ""
    for seg_label, seg_key, _ in SEGMENT_DEFS:
        seg_data = seg_dt.get(seg_key, {})
        seg_total_h = seg_data.get("hours", 0.0)
        clusters_data = seg_data.get("clusters", {})
        top_stops = top_stops_by_cluster.get(seg_key, {})

        defined_clusters = get_cause_clusters_display().get(seg_key, [])
        defined_names = [name for name, _ in defined_clusters]

        def cluster_hours(name, _cd=clusters_data):
            return _cd.get(name, {}).get("hours", 0.0)

        ordered = sorted(defined_names, key=lambda n: -cluster_hours(n))
        if "Other" in clusters_data or top_stops.get("Other"):
            ordered.append("Other")

        if not ordered:
            continue

        color = seg_color_map.get(seg_key, "#b2bec3")
        first = True

        for cluster_name in ordered:
            cd = clusters_data.get(cluster_name, {"count": 0, "hours": 0.0})
            c_hours = cd.get("hours", 0.0)
            c_count = cd.get("count", 0)
            pct = (c_hours / seg_total_h * 100) if seg_total_h > 0 else 0.0

            if cluster_name == "Other":
                kw_html = '<span style="color:#636e72;font-style:italic;">Not matching any defined keyword pattern</span>'
            else:
                pat = next((p for n, p in defined_clusters if n == cluster_name), None)
                kw_html = f'<code style="font-size:0.78rem;">{_pat_keywords(pat)}</code>' if pat else "—"

            examples = top_stops.get(cluster_name, [])
            if examples:
                ex_items = "".join(
                    f'<li><span class="ex-label">{e["label"]}</span>'
                    f'<span class="ex-meta">{e["count"]}× · {e["hours"]:.1f}h</span></li>'
                    for e in examples
                )
                ex_html = f'<ul class="ex-list">{ex_items}</ul>'
            else:
                ex_html = '<span style="color:#b2bec3;">—</span>'

            seg_cell = ""
            if first:
                seg_cell = (
                    f'<td class="seg-cell" rowspan="{len(ordered)}" '
                    f'style="border-left:3px solid {color};color:{color};">'
                    f'<strong>{seg_label}</strong></td>'
                )
                first = False

            rows_html += f"""<tr>
  {seg_cell}
  <td><strong>{cluster_name}</strong></td>
  <td class="num-cell">{c_hours:.1f}h</td>
  <td class="num-cell">{c_count}</td>
  <td class="num-cell">{pct:.0f}%</td>
  <td>{kw_html}</td>
  <td>{ex_html}</td>
</tr>"""

    return f"""<details class="cluster-ref-details">
  <summary>Stop Reference &mdash; Sub-systems &amp; Example Alarms <span class="ref-hint">(click to expand)</span></summary>
  <div style="overflow-x:auto;margin-top:0.75rem;">
  <table class="cluster-ref-table">
    <thead>
      <tr>
        <th>Segment</th>
        <th>Sub-system / Cluster</th>
        <th class="num-cell">Hours</th>
        <th class="num-cell">Stops</th>
        <th class="num-cell">% of seg.</th>
        <th>Keyword triggers</th>
        <th>Top alarm labels ({period_label})</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
  </div>
</details>"""
