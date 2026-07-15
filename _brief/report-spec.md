# Report Spec — Hollister Dashboard PBI (Phase 1)

## Report identity
- **Report name:** Hollister Dashboard PBI (existing PBIP scaffold in repo root)
- **Semantic model:** New local import model (built into `Hollister Dashboard PBI.SemanticModel`)
- **Audience:** Line engineers / production leadership (operations)
- **Primary purpose:** Monitor OEE and diagnose downtime causes for NGP2 & HU3
- **Delivery target:** Local PBIP only (no Fabric publish this phase)

## User decisions and constraints
- **Scope:** Reconstruct the existing weekly + shift dashboards. OEE + downtime now; **SPC deferred to Phase 2.**
- **Lines:** Both NGP2 and HU3, with a global **Line** slicer.
- **Data source:** Phase 1 = import the flattened CSVs in `powerbi_data/` (produced by `bin/export_powerbi_csv.py` from the committed pipeline JSON). Phase 2 = DirectQuery, with **Python remaining the metric-compute engine** (no DAX re-implementation of OEE logic).
- **Metric logic:** DAX only *aggregates* pre-computed values; it never re-derives OEE. Cross-shift rollups use correct weighting (not naive averages).
- **Tooling:** Node ✅, Power BI Desktop (company app) ✅, no `az`/MCP.
- **Accessibility:** WCAG AA contrast, alt text on charts, searchable slicers for high-cardinality fields.

## Data source (import)
CSVs in `powerbi_data/` (regenerate anytime with `python bin/export_powerbi_csv.py`):
`Shifts`, `WeeklySummary`, `SegmentDowntime`, `SegmentClusters`, `DowntimeCategories`,
`TopStops`, `HourlyBreakdown`, `Anomalies`, `Lines`, `DimDate`.

## Model
**Fact:** `Shifts` (grain = Line × Date × ShiftType, key `ShiftKey`) — OEE, Availability,
Performance, Quality, RunningHours, ScheduledHours(12), TotalStops, TotalDowntimeHours,
LongestStopMin, GoodCount, TotalCount, IsoWeek.

**Detail (related by `ShiftKey`, single-direction 1→*):**
`SegmentDowntime`, `SegmentClusters`, `DowntimeCategories`, `TopStops`,
`HourlyBreakdown`, `Anomalies`.

**Dimensions:** `Lines` (LineId→Shifts.LineId), `DimDate` (Date→Shifts.ShiftDate).
`WeeklySummary` is a standalone table (exact pipeline weekly KPIs, 1 row/line).

Relationships:
- `Lines[LineId]` 1→* `Shifts[LineId]`
- `DimDate[Date]` 1→* `Shifts[ShiftDate]`
- `Shifts[ShiftKey]` 1→* each detail table `[ShiftKey]`

## Key DAX measures (correct aggregation)
```
Availability % = DIVIDE( SUM(Shifts[RunningHours]), SUM(Shifts[ScheduledHours]) ) * 100
Quality %      = DIVIDE( SUM(Shifts[GoodCount]),   SUM(Shifts[TotalCount]) ) * 100
Performance %  = DIVIDE( SUMX(Shifts, Shifts[Performance]*Shifts[RunningHours]),
                         SUM(Shifts[RunningHours]) )          -- running-time weighted
OEE %          = [Availability %] * [Performance %] * [Quality %] / 10000
Total Downtime (h) = SUM(Shifts[TotalDowntimeHours])
Total Stops        = SUM(Shifts[TotalStops])
Segment Downtime (h) = SUM(SegmentDowntime[Hours])
Shift Count        = DISTINCTCOUNT(Shifts[ShiftKey])
```
Weekly KPI tiles read `WeeklySummary` directly so they match the pipeline's published
week numbers exactly; the shift-level fact drives trends and drilldowns.

## Page plan
1. **Weekly OEE Overview** — *Executive Summary archetype*
   - KPI row: OEE, Availability, Performance, Quality (from `WeeklySummary`)
   - OEE-by-shift trend (line/column, `Shifts` over `Label`)
   - Downtime by segment (stacked bar, `SegmentDowntime` colored by segment)
   - Downtime category split (micro/halted/auto-long/manual)
   - Global **Line** slicer; week context.
2. **Shift Detail** — *Operational Monitor archetype*
   - Shift selector (slicer on `Label` / date + type)
   - Per-shift OEE + A/P/Q cards
   - Hourly breakdown (stacked column by segment, `HourlyBreakdown`)
   - Top stops table (`TopStops`: segment, cluster, label, count, hours)
   - Anomalies callout (`Anomalies`: cluster, current vs baseline share, z-score)

## Design identity
- **Tone:** Operational/industrial dashboard, matching the existing HTML (clean, data-dense, dark-neutral base via existing `CY26SU05` theme).
- **Signature:** Consistent per-segment color mapping taken from `src/core/lines.py`
  (NGP2: hydration `#e76f51`, foil `#e9c46a`, schubert `#2a9d8f`; HU3: web `#3B82F6`,
  sealing `#F59E0B`, handling `#8B5CF6`, pneumatic `#10B981`; manual/other greys).
- KPI tiles top, trends mid, detailed tables bottom. Alt text on every visual.

## Validation
- All CSVs import; TMDL + report JSON parse.
- `definition.pbir` points to the local semantic model.
- Open `.pbip` in Power BI Desktop; data loads; both pages render.
- Screenshot-review Weekly + Shift pages; fix layout/binding/contrast issues.

## Notes / risks
- SPC (Laney p′ control charts, NGP2) intentionally deferred to Phase 2.
- No live MCP/`az`, so model authoring is direct TMDL edits; visual validation is via
  opening the PBIP in Desktop.
- Performance% cross-shift weighting is an approximation of the pipeline's PPM-based
  calc; weekly tiles use the exact pipeline value from `WeeklySummary` to stay faithful.
