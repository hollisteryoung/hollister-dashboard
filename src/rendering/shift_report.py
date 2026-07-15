#!/usr/bin/env python3
"""
Render the Shift Dashboard HTML from shift-dashboard.json.

Reads output/analyses/<line>/shift-dashboard.json and produces
output/reports/<line>/shifts/shift-dashboard-<date>-<type>.html —
a self-contained single file with all JS/CSS inline.

Usage:
    python3 src/rendering/shift_report.py [--line ngp2|hu3]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..')))
from core.lines import get_spc_panels
from core.storage import load_analysis_json
from rendering.theme import CFG
from rendering.components import (
    _build_anomaly_panel,
    _build_cluster_reference_table,
    _build_downtime_categories,
    _build_downtime_trend,
    _build_hourly_chart,
    _build_segment_downtime,
    _build_spc_panel,
    _delta_html,
    _kpi_color,
)
from rendering.shared import MW_LOGO_BASE64, _assert_self_contained, build_html_head, build_kpi_tiles, build_stop_summary

ANALYSIS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "output", "analyses", CFG["line_id"])
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "output", "reports", CFG["line_id"], "shifts")


def _load_data(shift_date=None, shift_type=None):
    if shift_date and shift_type:
        path = os.path.join(ANALYSIS_DIR, "shifts",
                            f"shift-{shift_date}-{shift_type}.json")
        with open(path) as f:
            return json.load(f)
    return load_analysis_json(CFG, "shift-dashboard.json")


def _build_kpi_tiles(latest, prev):
    prev_label = prev["label"] if prev else ""
    raw_metrics = [
        ("OEE", "oee", "A &times; P &times; Q"),
        ("Availability", "availability", f"{latest['running_hours']:.1f}h run / 12h"),
        ("Performance", "performance", "vs P95 ideal"),
        ("Quality", "quality", f"{latest.get('good_count', 0):,} / {latest.get('total_count', 0):,}"),
    ]
    tiles = [
        {
            "label": label,
            "val": latest[key],
            "color": _kpi_color(latest[key]),
            "delta": _delta_html(latest[key], prev[key], prev_label) if prev and key in prev else "",
            "sub": sub,
        }
        for label, key, sub in raw_metrics
    ]
    return build_kpi_tiles(tiles)


def _render_header(latest, generated_at):
    day_start_h, night_start_h = CFG["shift_boundaries"]
    if latest["shift_type"] == "day":
        shift_time_range = f"{day_start_h:02d}:00 &ndash; {night_start_h:02d}:00"
    else:
        shift_time_range = f"{night_start_h:02d}:00 &ndash; {day_start_h:02d}:00"
    shift_label_bold = f"{'Day' if latest['shift_type'] == 'day' else 'Night'} Shift &mdash; {latest['shift_date']}"
    return (
        build_html_head(f"Shift Dashboard — {CFG['label']} Line")
        + f"""
<div class="mw-header-bar"></div>
<header>
  <img src="{MW_LOGO_BASE64}" alt="MaibornWolff" class="mw-logo">
  <div class="header-divider"></div>
  <div class="header-text">
    <h1>Shift Dashboard &mdash; {CFG["label"]} Line</h1>
    <div class="meta">{latest["label"]} &middot; Auto-detected &middot; Generated: {generated_at[:19]}</div>
  </div>
</header>
<nav class="dash-nav">
  <a href="../weekly-dashboard.html">&#128197; Weekly Dashboard</a>
</nav>
<main>
<section class="section">
  <div class="section-header"><h2>{shift_label_bold} <span style="font-size:1rem;font-weight:400;color:#636e72;">({shift_time_range})</span></h2></div>
  <div class="section-body">"""
    )


def _render_snapshot_section(latest, prev):
    return (
        _build_kpi_tiles(latest, prev)
        + _build_anomaly_panel(latest.get("anomalies"))
        + """  </div>
</section>"""
    )


def _render_trend_section(context_shifts):
    return (
        """<section class="section">
  <div class="section-header"><h2>4-Shift Trend</h2></div>
  <div class="section-body">"""
        + _build_downtime_trend(context_shifts)
        + """  </div>
</section>"""
    )


def _render_downtime_section(latest):
    return (
        """<section class="section">
  <div class="section-header"><h2>Downtime</h2></div>
  <div class="section-body">
    <div class="panel-grid">"""
        + _build_downtime_categories(latest["downtime_categories"])
        + f"""<div class="panel">
  <h3>Stop Summary</h3>
  <div class="panel-render" style="padding:1rem;">
    {build_stop_summary(latest)}
  </div>
</div>"""
        + "    </div>"
        + _build_segment_downtime(latest.get("segment_downtime", {}))
        + _build_cluster_reference_table(
            latest.get("segment_downtime", {}),
            latest.get("top_stops_by_cluster", {}),
        )
        + """  </div>
</section>"""
    )


def _render_hourly_section(latest):
    """Return the hourly breakdown section HTML, or empty string if unavailable."""
    hourly = _build_hourly_chart(latest.get("hourly_breakdown", []))
    if not hourly:
        return ""
    return (
        """<section class="section">
  <div class="section-header"><h2>Hourly Breakdown</h2></div>
  <div class="section-body">"""
        + hourly
        + """  </div>
</section>"""
    )


def _render_spc_section(latest):
    """Return the SPC section HTML, or empty string if no SPC data available."""
    spc_panels = [
        (latest.get(p["key"]), p["title"], p["lane_prefix"], p["div_id"])
        for p in get_spc_panels(CFG)
    ]
    if not any(s[0] for s in spc_panels):
        return ""
    panel_html = "".join(
        laney for _, title, prefix, div_id in spc_panels
        if (laney := _build_spc_panel(_, title, prefix, div_id))
    )
    return (
        """<section class="section">
  <div class="section-header"><h2>Statistical Process Control</h2></div>
  <div class="section-body">
    <div style="font-size:0.85rem;color:#636e72;line-height:1.7;margin-bottom:1.2rem;max-width:900px;">
      <strong>Laney p&rsquo; charts</strong> show whether each lane&rsquo;s reject rate this shift
      is within normal statistical variation. A bar exceeding the
      <span style="color:#0055ff;font-weight:600;">dashed UCL line</span> signals an abnormal
      reject rate that warrants investigation.
      EWMA drift trends are in the <a href="../weekly-dashboard.html" style="color:#1E194B;font-weight:600;">Weekly Dashboard</a>.
    </div>"""
        + panel_html
        + """  </div>
</section>"""
    )


def _render_footer(latest, generated_at):
    return f"""</main>
<footer>
  Shift Dashboard &mdash; {CFG["label"]} Line &middot; {latest["shift_date"]} {latest["shift_type"].title()} &middot; Generated {generated_at[:10]} &middot; MaibornWolff
</footer>
</body>
</html>"""


def _render_one_shift(latest, context_shifts, generated_at):
    """Render and write one shift dashboard HTML file.

    context_shifts: list starting with latest, followed by up to 4 prior shifts.
    Sections that require detailed data (hourly, SPC) are skipped if unavailable.
    """
    prev = next((s for s in context_shifts[1:] if not s.get("skipped")), None)
    html = "\n".join([
        _render_header(latest, generated_at),
        _render_snapshot_section(latest, prev),
        _render_trend_section(context_shifts),
        _render_downtime_section(latest),
        _render_hourly_section(latest),
        _render_spc_section(latest),
        _render_footer(latest, generated_at),
    ])
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    shift_slug = f"{latest['shift_date']}-{latest['shift_type']}"
    out_path = os.path.join(OUTPUT_DIR, f"shift-dashboard-{shift_slug}.html")
    _assert_self_contained(html, out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out_path} ({len(html):,} bytes)")


def render_shift_from_data(data):
    """Render one shift dashboard HTML from an already-loaded data dict.

    Called by render_all_shifts.py to avoid subprocess overhead; equivalent
    to what main() does after loading the JSON file.
    """
    shifts = data["shifts"]
    _render_one_shift(shifts[0], shifts, data["generated_at"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shift-date")
    parser.add_argument("--shift-type")
    args, _ = parser.parse_known_args()
    data = _load_data(args.shift_date, args.shift_type)
    shifts = data["shifts"]
    _render_one_shift(shifts[0], shifts, data["generated_at"])


if __name__ == "__main__":
    main()
