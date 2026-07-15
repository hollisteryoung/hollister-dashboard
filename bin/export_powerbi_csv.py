#!/usr/bin/env python3
"""
export_powerbi_csv.py — Flatten the committed OEE analysis JSON into tidy CSVs
for the "Hollister Dashboard PBI" Power BI model (Phase 1: import).

Reads   output/analyses/<line>/shifts/*.json  (per-shift rich detail)
        output/analyses/<line>/weekly-dashboard.json  (exact weekly rollup KPIs)
Writes  powerbi_data/*.csv

No database or network access required — this consumes the pipeline's committed
output, so every metric is exactly what the Python pipeline already computed.
Power BI only aggregates for display; it never re-derives OEE.

Run:  python bin/export_powerbi_csv.py
"""

import csv
import datetime as dt
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYSES = os.path.join(ROOT, "output", "analyses")
OUT = os.path.join(ROOT, "powerbi_data")

LINES = {"ngp2": "NGP2", "hu3": "HU3"}
SHIFT_LENGTH_HOURS = 12.0  # 07:00-19:00 day / 19:00-07:00 night (src/core/lines.py)

# Segment colours (src/core/lines.py) — enables exact "format by field value" fills.
SEG_COLORS = {
    "hydration": "#e76f51", "foil": "#e9c46a", "schubert": "#2a9d8f",
    "manual": "#636e72", "unattributed": "#b2bec3",
    "web": "#3B82F6", "sealing": "#F59E0B", "handling": "#8B5CF6",
    "pneumatic": "#10B981", "running": "#c7c0e8",
}
SPC_PANELS = {"weekly_nozzle_spc": "Nozzle", "weekly_camera_spc": "Camera",
              "weekly_vision_spc": "Vision"}
SEG_LABELS = {"hydration": "Hydration", "foil": "Foil", "schubert": "Schubert",
              "manual": "Manual", "unattributed": "Other", "web": "Web / Reel",
              "sealing": "Sealing", "handling": "Handling", "pneumatic": "Pneumatic",
              "running": "Running"}
# Downtime-category display labels (src/rendering/theme.py DOWNTIME_CATEGORIES).
CAT_LABELS = {"micro_stops": "Micro-stops (<2 min)", "halted": "Halted (operator wait)",
              "auto_long": "Auto-stops (>2 min)", "manual": "Manual stops"}
CAT_COLORS = {"micro_stops": "#2d3436", "halted": "#636e72",
              "auto_long": "#b2bec3", "manual": "#dfe6e9"}


def anomaly_headline(a, period="week"):
    """Reproduce the HTML anomaly callout headline + detail strings."""
    seg = a.get("segment", "")
    cluster = a.get("cluster", "")
    cur = a.get("current_share", 0)
    base = a.get("baseline_mean", 0)
    ratio = cur / base if base else 0
    seg_label = SEG_LABELS.get(seg, seg.title())
    if a.get("direction") == "above":
        mult = f"{round(ratio)}× " if ratio >= 2 else ""
        head = f"{mult}more {cluster} stops than usual ({seg_label})"
    else:
        head = f"Fewer {cluster} stops than usual ({seg_label})"
    detail = f"{cur}% of this {period}'s downtime, normally {base}%"
    return head, detail


def key_findings(line, w, shifts, doc):
    """Plain-text Key Findings bullets — mirrors weekly_report._build_key_findings."""
    items = []
    outliers = (w.get("anomalies") or {}).get("cluster_outliers") or []
    if outliers:
        a = outliers[0]
        base = a.get("baseline_mean", 0)
        cur = a.get("current_share", 0)
        ratio = cur / base if base else 0
        seg = a.get("segment", "").title()
        if a.get("direction") == "above" and ratio >= 2:
            items.append(f"{a.get('cluster')} stops in {seg} accounted for {cur}% of "
                         f"downtime — {ratio:.0f}× the normal level ({base}%)")
        elif a.get("direction") == "above":
            items.append(f"{a.get('cluster')} stops in {seg} ran above normal: "
                         f"{cur}% vs typical {base}%")
        else:
            items.append(f"{a.get('cluster')} stops in {seg} below normal: "
                         f"{cur}% vs typical {base}%")
    prior = w.get("prior_week")
    if prior:
        delta = round(w.get("oee", 0) - prior.get("oee", 0), 1)
        if abs(delta) >= 1.0:
            direction = "improved" if delta > 0 else "declined"
            items.append(f"Weekly OEE {direction} by {abs(delta):.1f}pp vs prior week "
                         f"({prior.get('from','')[5:]} – {prior.get('to','')[5:]}): "
                         f"{w.get('oee')}% vs {prior.get('oee')}%")
    ooc_parts = []
    for pkey, pname in SPC_PANELS.items():
        sp = doc.get(pkey)
        if sp:
            ooc = [str(l.get("lane")) for l in sp.get("lanes", []) if l.get("any_out_of_control")]
            if ooc:
                ooc_parts.append(f"{pname} lanes {', '.join(ooc)}")
    if ooc_parts:
        items.append(f"Quality alert: {' · '.join(ooc_parts)} exceeded Laney p′ "
                     f"control limits this week")
    production = [s for s in (doc.get("shifts") or []) if not s.get("skipped")]
    if production:
        worst = min(production, key=lambda s: s.get("oee", 0))
        items.append(f"Weakest shift: {worst.get('label')} at {worst.get('oee')}% OEE "
                     f"(A={worst.get('availability')}% · P={worst.get('performance')}% "
                     f"· Q={worst.get('quality')}%)")
    return items[:4]

_FNAME_RE = re.compile(r"shift-(\d{4}-\d{2}-\d{2})-(day|night)\.json$")


def shift_key(line, date, stype):
    return f"{line}|{date}|{stype}"


def collect_shifts():
    """Return one authoritative rich record per (line, date, type).

    Each per-shift file carries a rolling window; the *subject* shift (the one
    named in the filename) always holds the full detail, so we take that entry.
    """
    records = {}
    for line in LINES:
        sdir = os.path.join(ANALYSES, line, "shifts")
        if not os.path.isdir(sdir):
            continue
        for fn in sorted(os.listdir(sdir)):
            m = _FNAME_RE.search(fn)
            if not m:
                continue
            date, stype = m.group(1), m.group(2)
            data = json.load(open(os.path.join(sdir, fn), encoding="utf-8"))
            subject = next(
                (s for s in data.get("shifts", [])
                 if s.get("shift_date") == date and s.get("shift_type") == stype),
                None,
            )
            if subject:
                records[shift_key(line, date, stype)] = (line, subject)
    return records


def iso_week(date_str):
    d = dt.date.fromisoformat(date_str)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_label(date_str):
    """Friendly slicer label for the Monday of this date's ISO week, e.g. 'Week of Jun 08'."""
    d = dt.date.fromisoformat(date_str)
    monday = d - dt.timedelta(days=d.isocalendar()[2] - 1)
    return f"Week of {monday.strftime('%b %d')}"


def write_csv(name, header, rows):
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name:28s} {len(rows):6d} rows")


def build_tables():
    """Collect + flatten every analysis JSON into a list of (name, header, rows).

    Pure in-memory: no files written. Reused by bin/powerbi_compute.py so Power BI
    can obtain the same tables as DataFrames without going through CSV on disk.
    """
    records = collect_shifts()
    print(f"Collected {len(records)} distinct shifts across {len(LINES)} lines")

    shifts, categories, segments, clusters, topstops, hourly, anomalies = ([] for _ in range(7))
    hourly_clusters = []
    dates = set()

    for key, (line, s) in sorted(records.items()):
        date, stype = s["shift_date"], s["shift_type"]
        dates.add(date)
        running = s.get("running_hours", 0.0) or 0.0
        shifts.append([
            key, line, LINES[line], date, stype, s.get("label", ""),
            f"{line}|{s.get('label', '')}",
            iso_week(date), week_label(date),
            s.get("oee", 0), s.get("availability", 0), s.get("performance", 0),
            s.get("quality", 0), running, SHIFT_LENGTH_HOURS,
            s.get("total_stops", 0), s.get("total_downtime_hours", 0),
            s.get("longest_stop_min", 0),
            s.get("good_count", 0), s.get("total_count", 0),
        ])

        for cat, v in (s.get("downtime_categories") or {}).items():
            categories.append([key, line, cat, CAT_LABELS.get(cat, cat.replace("_", " ").title()),
                                v.get("count", 0), v.get("hours", 0)])

        for seg, v in (s.get("segment_downtime") or {}).items():
            segments.append([
                key, line, seg, SEG_LABELS.get(seg, seg.title()),
                v.get("count", 0), v.get("hours", 0),
                v.get("halted_hours", 0), v.get("stopped_hours", 0),
                SEG_COLORS.get(seg, "#b2bec3"),
            ])
            seg_clusters = v.get("clusters") or {}
            if seg_clusters:
                for cl, cv in seg_clusters.items():
                    clusters.append([
                        key, line, seg, cl, cv.get("count", 0), cv.get("hours", 0),
                        cv.get("halted_hours", 0), cv.get("stopped_hours", 0),
                    ])
            elif v.get("hours", 0) > 0:
                # No per-cause cluster breakdown for this segment (e.g. Manual, Other) —
                # fall back to a single pseudo-cluster using the segment's own label,
                # matching src/rendering/components.py::_build_segment_downtime's else-branch.
                clusters.append([
                    key, line, seg, SEG_LABELS.get(seg, seg.title()),
                    v.get("count", 0), v.get("hours", 0),
                    v.get("halted_hours", 0), v.get("stopped_hours", 0),
                ])

        for seg, cls in (s.get("top_stops_by_cluster") or {}).items():
            for cl, items in cls.items():
                for it in items:
                    topstops.append([
                        key, line, seg, cl, it.get("label", ""),
                        it.get("count", 0), it.get("hours", 0),
                    ])

        for h_idx, h in enumerate(s.get("hourly_breakdown") or []):
            hl, rm = h.get("hour_label", ""), h.get("running_min", 0)
            hourly.append([key, line, hl, "running", "Running", rm, SEG_COLORS["running"]])
            for seg, sv in (h.get("segments") or {}).items():
                hourly.append([key, line, hl, seg, SEG_LABELS.get(seg, seg.title()),
                               sv.get("minutes", 0), SEG_COLORS.get(seg, "#b2bec3")])
                seg_min = sv.get("minutes", 0)
                if seg_min <= 0:
                    continue
                hr_clusters = sv.get("clusters") or {}
                if hr_clusters:
                    for cl, c_min in hr_clusters.items():
                        hourly_clusters.append([key, line, hl, h_idx, seg, cl, c_min])
                else:
                    hourly_clusters.append([key, line, hl, h_idx, seg,
                                             SEG_LABELS.get(seg, seg.title()), seg_min])

        an = s.get("anomalies") or {}
        for rank, a in enumerate(an.get("cluster_outliers") or []):
            head, detail = anomaly_headline(a, period="shift")
            anomalies.append([
                key, line, s.get("label", ""), a.get("segment", ""), a.get("cluster", ""),
                round(a.get("current_share", 0), 1), round(a.get("baseline_mean", 0), 1),
                round(a.get("z_score", 0), 1), a.get("direction", ""),
                head, detail, f"{head}\n{detail}",
                SEG_COLORS.get(a.get("segment", ""), "#636e72"), rank,
            ])

    # Weekly rollup (+prior week), weekly anomalies, SPC, EWMA, key findings — from weekly doc
    weekly, weekly_anoms, spc, findings, ewma_rows = [], [], [], [], []
    for line in LINES:
        wf = os.path.join(ANALYSES, line, "weekly-dashboard.json")
        if not os.path.exists(wf):
            continue
        doc = json.load(open(wf, encoding="utf-8"))
        w = doc["week"]
        pw = w.get("prior_week") or {}
        for rank, text in enumerate(key_findings(line, w, doc.get("shifts") or [], doc)):
            findings.append([line, LINES[line], rank, text])
        weekly.append([
            line, LINES[line], w.get("from"), w.get("to"),
            w.get("oee", 0), w.get("availability", 0), w.get("performance", 0),
            w.get("quality", 0), w.get("good_count", 0), w.get("total_count", 0),
            w.get("total_stops", 0), w.get("total_downtime_hours", 0),
            w.get("longest_stop_min", 0), w.get("production_shifts", 0),
            pw.get("oee", 0), pw.get("availability", 0),
            pw.get("performance", 0), pw.get("quality", 0),
        ])
        for rank, a in enumerate(((w.get("anomalies") or {}).get("cluster_outliers") or [])):
            cur = a.get("current_share", 0)
            base = a.get("baseline_mean", 0) or 0.0001
            head, detail = anomaly_headline(a)
            weekly_anoms.append([
                line, LINES[line], a.get("segment", ""), a.get("cluster", ""),
                round(cur, 1), round(a.get("baseline_mean", 0), 1),
                round(a.get("z_score", 0), 1), a.get("direction", ""),
                round(cur / base, 1), head, detail, f"{head}\n{detail}",
                SEG_COLORS.get(a.get("segment", ""), "#636e72"), rank,
            ])
        for pkey, pname in SPC_PANELS.items():
            sp = doc.get(pkey)
            if not sp:
                continue
            ucl, pbar = {}, {}
            for lane in sp.get("lanes", []):
                ln = lane.get("lane")
                pbar[ln] = lane.get("p_bar_pct", 0)
                for pt in lane.get("points", []):
                    ucl[(ln, pt.get("label"))] = (pt.get("ucl_pct", 0), pt.get("out_of_control", False))
            for sidx, st in enumerate(sp.get("shift_table", [])):
                lbl, n = st.get("label"), st.get("n_inspected", 0)
                for pl in st.get("per_lane", []):
                    ln = pl.get("lane")
                    u, ooc = ucl.get((ln, lbl), (0, pl.get("ooc", False)))
                    is_ooc = 1 if (pl.get("ooc") or ooc) else 0
                    spc.append([
                        line, LINES[line], pname, lbl, f"{line}|{lbl}", ln,
                        pl.get("rejects", 0), round(pl.get("rate_pct", 0), 3),
                        round(u, 3), is_ooc, n,
                        "#e17055" if is_ooc else "#ffffff",
                        sidx, round(pbar.get(ln, 0), 3),
                    ])
            # EWMA drift series (per batch subgroup × lane) with per-lane UCL/LCL
            e = sp.get("ewma") or {}
            e_ucl, e_lcl = e.get("ucl") or [], e.get("lcl") or []
            for bidx, sh in enumerate(e.get("shifts") or []):
                rates, ew = sh.get("rates") or [], sh.get("ewma") or []
                for j in range(len(ew)):
                    ewma_rows.append([
                        line, LINES[line], pname, bidx, j + 1,
                        round(rates[j], 3) if j < len(rates) else 0,
                        round(ew[j], 3),
                        round(e_ucl[j], 3) if j < len(e_ucl) else 0,
                        round(e_lcl[j], 3) if j < len(e_lcl) else 0,
                    ])

    # 4-Shift Trend: for each shift (anchor), its downtime-category mix alongside
    # up to 3 previous shifts on the same line — mirrors
    # src/rendering/components.py::_build_downtime_trend's "Downtime Mix (Last 4 Shifts)",
    # anchored per-shift so it never mixes the two lines' shift sequences.
    shift_trend = []
    cat_hours_by_key = {
        key: {cat: v.get("hours", 0) for cat, v in (s.get("downtime_categories") or {}).items()}
        for key, (line, s) in records.items()
    }
    line_seq = {}
    for key, (line, s) in sorted(records.items()):
        line_seq.setdefault(line, []).append((s["shift_date"], s["shift_type"], key, s.get("label", "")))
    for line, seq in line_seq.items():
        for i, (_date, _stype, anchor_key, _label) in enumerate(seq):
            window = list(reversed(seq[max(0, i - 3):i + 1]))  # rank0=current .. rank3=oldest
            for rank, (d2, _t2, wkey, lbl2) in enumerate(window):
                cats = cat_hours_by_key.get(wkey, {})
                for cat_k, cat_label in CAT_LABELS.items():
                    shift_trend.append([
                        anchor_key, line, LINES[line], rank, wkey, lbl2, d2,
                        cat_k, cat_label, cats.get(cat_k, 0), CAT_COLORS.get(cat_k, "#b2bec3"),
                    ])

    # Dimensions
    lines_dim = [[lid, lbl] for lid, lbl in LINES.items()]
    date_dim = []
    for ds in sorted(dates):
        d = dt.date.fromisoformat(ds)
        date_dim.append([ds, d.year, d.month, d.strftime("%b"), d.day,
                         d.strftime("%a"), iso_week(ds)])

    return [
        ("Shifts",
         ["ShiftKey", "LineId", "Line", "ShiftDate", "ShiftType", "Label", "LineShiftLabel",
          "IsoWeek", "WeekLabel", "OEE", "Availability", "Performance", "Quality",
          "RunningHours", "ScheduledHours", "TotalStops",
          "TotalDowntimeHours", "LongestStopMin", "GoodCount", "TotalCount"], shifts),
        ("DowntimeCategories",
         ["ShiftKey", "LineId", "Category", "CategoryLabel", "Count", "Hours"], categories),
        ("SegmentDowntime",
         ["ShiftKey", "LineId", "Segment", "SegmentLabel", "Count", "Hours",
          "HaltedHours", "StoppedHours", "SegmentColor"], segments),
        ("SegmentClusters",
         ["ShiftKey", "LineId", "Segment", "Cluster", "Count", "Hours",
          "HaltedHours", "StoppedHours"], clusters),
        ("TopStops",
         ["ShiftKey", "LineId", "Segment", "Cluster", "Label", "Count", "Hours"], topstops),
        ("HourlyBreakdown",
         ["ShiftKey", "LineId", "HourLabel", "Segment", "SegmentLabel", "Minutes", "SegmentColor"], hourly),
        ("HourlyClusters",
         ["ShiftKey", "LineId", "HourLabel", "HourIdx", "Segment", "Cluster", "Minutes"], hourly_clusters),
        ("ShiftTrend",
         ["AnchorShiftKey", "LineId", "Line", "TrendRank", "TrendShiftKey", "TrendShiftLabel",
          "TrendShiftDate", "Category", "CategoryLabel", "Hours", "CategoryColor"], shift_trend),
        ("Anomalies",
         ["ShiftKey", "LineId", "ShiftLabel", "Segment", "Cluster", "CurrentShare",
          "BaselineMean", "ZScore", "Direction", "Headline", "Detail", "HeadlineML",
          "SegColor", "Rank"], anomalies),
        ("WeeklySummary",
         ["LineId", "Line", "WeekFrom", "WeekTo", "OEE", "Availability",
          "Performance", "Quality", "GoodCount", "TotalCount", "TotalStops",
          "TotalDowntimeHours", "LongestStopMin", "ProductionShifts",
          "PriorOEE", "PriorAvailability", "PriorPerformance", "PriorQuality"], weekly),
        ("WeeklyAnomalies",
         ["LineId", "Line", "Segment", "Cluster", "CurrentShare", "BaselineMean",
          "ZScore", "Direction", "Ratio", "Headline", "Detail", "HeadlineML",
          "SegColor", "Rank"], weekly_anoms),
        ("SPC",
         ["LineId", "Line", "Panel", "ShiftLabel", "LineShiftLabel", "Lane", "Rejects",
          "RatePct", "UclPct", "OOC", "NInspected", "OOCColor",
          "ShiftIdx", "PBar"], spc),
        ("EWMA",
         ["LineId", "Line", "Panel", "BatchIdx", "Lane", "Rate", "Ewma",
          "Ucl", "Lcl"], ewma_rows),
        ("KeyFindings",
         ["LineId", "Line", "Rank", "Text"], findings),
        ("Lines", ["LineId", "Line"], lines_dim),
        ("DimDate",
         ["Date", "Year", "MonthNum", "Month", "Day", "Weekday", "IsoWeek"], date_dim),
    ]


def main():
    os.makedirs(OUT, exist_ok=True)
    print("Writing CSVs:")
    for name, header, rows in build_tables():
        write_csv(f"{name}.csv", header, rows)
    print(f"Done -> {os.path.relpath(OUT, ROOT)}/")


if __name__ == "__main__":
    main()
