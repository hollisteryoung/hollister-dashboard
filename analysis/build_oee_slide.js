// Builds a single-slide PowerPoint summarising OEE by workcenter family:
// target vs. Q1 2026 (Jan-Mar) vs. the last three months (May-Jul 2026).
// Figures are produced by build_oee_slide_data.py from the monthly OEE live file.

const pptxgen = require("pptxgenjs");

/* ---------- palette ---------- */
const NAVY   = "1F3864";   // last 3 months / dominant
const BLUE   = "93ADD2";   // Q1 2026
const GREY   = "C2C6CF";   // target (reference)
const MAROON = "9E2A2B";   // accent
const INK    = "16203A";
const INK2   = "4A5670";
const INK3   = "77809A";
const TINT   = "F4F6F9";
const RULE   = "DFE3EA";
const GOOD   = "1C6B45";
const WARN   = "9A6212";

const SERIF = "Cambria";
const SANS  = "Calibri";

/* ---------- data (sorted by last-3-month OEE, highest first) ---------- */
const ROWS = [
  { fam: "2-Piece Autocoiners",       n: 8,  target: 75.6, q1: 57.7, l3: 61.4 },
  { fam: "Dansac Drainable Pouch",    n: 3,  target: 56.4, q1: 55.9, l3: 60.8 },
  { fam: "1-Piece Autocoiners",       n: 12, target: 71.7, q1: 64.5, l3: 57.5 },
  { fam: "Hollister Drainable Pouch", n: 8,  target: 59.6, q1: 57.1, l3: 56.4 },
  { fam: "BIM Machines",              n: 17, target: 71.1, q1: 53.7, l3: 54.7 },
  { fam: "Hollister Closed Pouch",    n: 6,  target: 69.3, q1: 54.7, l3: 49.7 },
  { fam: "Urostomy (HU) Cells",       n: 3,  target: 54.7, q1: 39.2, l3: 46.8 },
  { fam: "BFX Cells",                 n: 1,  target: 58.9, q1: 57.6, l3: 45.5 },
  { fam: "Dansac Closed Pouch",       n: 3,  target: 62.9, q1: 41.0, l3: 40.5 },
  { fam: "Ring / Extruder",           n: 3,  target: 66.1, q1: 37.1, l3: 37.0 },
];

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";               // 13.33 x 7.5 in
pres.author = "Hollister OEE reporting";
pres.title  = "OEE: Q1 2026 vs. Last Three Months";

const s = pres.addSlide();
s.background = { color: "FFFFFF" };

/* ---------- header ---------- */
s.addText("OEE: Q1 2026 vs. Last Three Months", {
  x: 0.45, y: 0.26, w: 9.4, h: 0.5, margin: 0,
  fontFace: SERIF, fontSize: 30, bold: true, color: NAVY,
});
s.addText(
  [
    { text: "May–Jun–Jul 2026 vs. Jan–Feb–Mar 2026", options: { bold: true, color: INK2 } },
    { text: "   •   64 workcenters across 10 families   •   equal-weighted mean of monthly workcenter OEE",
      options: { color: INK3 } },
  ],
  { x: 0.47, y: 0.74, w: 11.0, h: 0.26, margin: 0, fontFace: SANS, fontSize: 11 }
);

/* ---------- KPI band ---------- */
const KPI_Y = 1.10, KPI_H = 0.92;
s.addShape(pres.ShapeType.rect, {
  x: 0.45, y: KPI_Y, w: 12.43, h: KPI_H, fill: { color: TINT }, line: { color: RULE, width: 0.75 },
});

// hero
s.addText("COMPANY OEE — LAST 3 MONTHS", {
  x: 0.62, y: KPI_Y + 0.10, w: 3.0, h: 0.2, margin: 0,
  fontFace: SANS, fontSize: 8.5, bold: true, charSpacing: 1.2, color: INK3,
});
s.addText("54.1%", {
  x: 0.58, y: KPI_Y + 0.28, w: 1.90, h: 0.52, margin: 0,
  fontFace: SANS, fontSize: 32, bold: true, color: MAROON, valign: "middle",
});
s.addText(
  [
    { text: "−0.6 pts", options: { bold: true, color: INK } },
    { text: " vs. Q1", options: { color: INK2, breakLine: true } },
    { text: "−13.7 pts", options: { bold: true, color: INK } },
    { text: " vs. target", options: { color: INK2 } },
  ],
  { x: 2.55, y: KPI_Y + 0.30, w: 2.25, h: 0.48, margin: 0,
    fontFace: SANS, fontSize: 10, valign: "middle", lineSpacingMultiple: 1.05 }
);

const stats = [
  { x: 5.25, lab: "Q1 2026 BASELINE",   val: "54.7%", sub: "Jan–Mar average" },
  { x: 7.75, lab: "FLEET TARGET",       val: "67.8%", sub: "Equipment-weighted" },
  { x: 10.15, lab: "FAMILIES AT TARGET", val: "1 / 10", sub: "Dansac Drainable only" },
];
stats.forEach((k) => {
  s.addShape(pres.ShapeType.line, {
    x: k.x - 0.30, y: KPI_Y + 0.14, w: 0, h: KPI_H - 0.28, line: { color: RULE, width: 1 },
  });
  s.addText(k.lab, {
    x: k.x, y: KPI_Y + 0.10, w: 2.3, h: 0.2, margin: 0,
    fontFace: SANS, fontSize: 8.5, bold: true, charSpacing: 1.2, color: INK3,
  });
  s.addText(k.val, {
    x: k.x - 0.02, y: KPI_Y + 0.28, w: 2.3, h: 0.32, margin: 0,
    fontFace: SANS, fontSize: 20, bold: true, color: INK,
  });
  s.addText(k.sub, {
    x: k.x, y: KPI_Y + 0.62, w: 2.3, h: 0.22, margin: 0,
    fontFace: SANS, fontSize: 9.5, color: INK2,
  });
});

/* ---------- chart ---------- */
s.addText("OEE % by workcenter family — target vs. Q1 vs. last 3 months", {
  x: 0.45, y: 2.20, w: 8.2, h: 0.24, margin: 0,
  fontFace: SANS, fontSize: 11.5, bold: true, color: INK,
});
s.addText("Sorted by last-3-month OEE, highest first. Workcenter count in brackets.", {
  x: 0.45, y: 2.43, w: 8.2, h: 0.2, margin: 0,
  fontFace: SANS, fontSize: 9, color: INK3,
});

// pptxgenjs plots bar categories bottom-up, so reverse to put the leader on top
const plot = [...ROWS].reverse();
const cats = plot.map((r) => `${r.fam}  (${r.n})`);

s.addChart(
  pres.ChartType.bar,
  [
    { name: "Target OEE",              labels: cats, values: plot.map((r) => r.target) },
    { name: "Q1 2026 (Jan–Mar)",       labels: cats, values: plot.map((r) => r.q1) },
    { name: "Last 3 months (May–Jul)", labels: cats, values: plot.map((r) => r.l3) },
  ],
  {
    x: 0.30, y: 2.62, w: 8.35, h: 4.35,
    barDir: "bar",
    barGrouping: "clustered",
    barGapWidthPct: 45,
    chartColors: [GREY, BLUE, NAVY],

    valAxisMinVal: 0,
    valAxisMaxVal: 90,
    valAxisMajorUnit: 10,
    valAxisLabelFormatCode: '0"%"',
    valAxisLabelFontFace: SANS,
    valAxisLabelFontSize: 8.5,
    valAxisLabelColor: INK3,
    valAxisLineShow: false,
    valGridLine: { color: "E9ECF1", size: 0.75 },

    catAxisLabelFontFace: SANS,
    catAxisLabelFontSize: 9.5,
    catAxisLabelColor: INK,
    catAxisLineShow: false,
    catGridLine: { style: "none" },

    showValue: true,
    dataLabelPosition: "outEnd",
    dataLabelFontFace: SANS,
    dataLabelFontSize: 7,
    dataLabelColor: INK2,
    dataLabelFormatCode: "0.0",

    showLegend: true,
    legendPos: "t",
    legendFontFace: SANS,
    legendFontSize: 9.5,
    legendColor: INK2,
  }
);

/* ---------- insights ---------- */
const RX = 9.02, RW = 3.86;
s.addText("Key insights", {
  x: RX, y: 2.20, w: RW, h: 0.24, margin: 0,
  fontFace: SANS, fontSize: 11.5, bold: true, color: INK,
});

const INSIGHTS = [
  {
    c: MAROON, h: "FLAT, NOT RECOVERING", y: 2.48,
    body: [
      { text: "54.7% → 54.1%", options: { bold: true, color: INK } },
      { text: " — inside the noise band. Five families improved, five declined; the moves cancelled. Nothing structural changed.", options: { color: INK2 } },
    ],
  },
  {
    c: MAROON, h: "BIGGEST DETERIORATION", y: 3.30,
    body: [
      { text: "1-Piece Autocoiners", options: { bold: true, color: INK } },
      { text: " fell ", options: { color: INK2 } },
      { text: "7.0 pts", options: { bold: true, color: INK } },
      { text: " (64.5% → 57.5%) on two machines alone: Autocoiner #5 (75→44) and #3 (68→41). The other ten held.", options: { color: INK2 } },
    ],
  },
  {
    c: GOOD, h: "BIGGEST IMPROVEMENT", y: 4.12,
    body: [
      { text: "Urostomy cells", options: { bold: true, color: INK } },
      { text: " gained ", options: { color: INK2 } },
      { text: "7.6 pts", options: { bold: true, color: INK } },
      { text: " as HU2 recovered 18% → 38%. ", options: { color: INK2 } },
      { text: "Dansac Drainable", options: { bold: true, color: INK } },
      { text: " is the only family above target (+4.4 pts).", options: { color: INK2 } },
    ],
  },
  {
    c: WARN, h: "WATCH ITEM — DEAD ASSETS", y: 4.94,
    body: [
      { text: "Extruder 4", options: { bold: true, color: INK } },
      { text: " has logged ", options: { color: INK2 } },
      { text: "0.0%", options: { bold: true, color: INK } },
      { text: " every month of 2026; Kiefel 2 averages 11.8% and HF12 14.4%. These three alone sink Ring/Extruder and Dansac Closed.", options: { color: INK2 } },
    ],
  },
];

INSIGHTS.forEach((i) => {
  s.addShape(pres.ShapeType.rect, {
    x: RX, y: i.y + 0.045, w: 0.09, h: 0.09, fill: { color: i.c }, line: { type: "none" },
  });
  s.addText(i.h, {
    x: RX + 0.19, y: i.y - 0.015, w: RW - 0.19, h: 0.2, margin: 0,
    fontFace: SANS, fontSize: 8.5, bold: true, charSpacing: 1.2, color: i.c,
  });
  s.addText(i.body, {
    x: RX + 0.19, y: i.y + 0.19, w: RW - 0.19, h: 0.58, margin: 0,
    fontFace: SANS, fontSize: 9.5, lineSpacingMultiple: 1.0,
  });
});

/* ---------- recommended focus ---------- */
const FY = 5.80;
s.addShape(pres.ShapeType.rect, {
  x: RX, y: FY, w: RW, h: 1.20, fill: { color: NAVY }, line: { type: "none" },
});
s.addText("RECOMMENDED FOCUS", {
  x: RX + 0.16, y: FY + 0.07, w: RW - 0.32, h: 0.18, margin: 0,
  fontFace: SANS, fontSize: 8.5, bold: true, charSpacing: 1.2, color: "A9B8D2",
});
s.addText(
  [
    { text: "1  Rule on the zero-output assets", options: { bold: true, color: "FFFFFF", breakLine: true } },
    { text: "2  Root-cause Autocoiner #3 and #5", options: { bold: true, color: "FFFFFF", breakLine: true } },
    { text: "3  Run BIM as a programme (17 assets)", options: { bold: true, color: "FFFFFF" } },
  ],
  { x: RX + 0.16, y: FY + 0.32, w: RW - 0.32, h: 0.72, margin: 0,
    fontFace: SANS, fontSize: 9, color: "FFFFFF", lineSpacingMultiple: 1.25 }
);

/* ---------- footer ---------- */
s.addShape(pres.ShapeType.line, {
  x: 0.45, y: 7.06, w: 12.43, h: 0, line: { color: RULE, width: 0.75 },
});
s.addText(
  "Source: Monthly OEE Live File — “OEE% by Month” and “WC Target OEE” tabs. Q1 = Jan–Mar 2026; last 3 months = May–Jul 2026 (July is the most recent complete month). " +
  "Family averages are the equal-weighted mean of each workcenter’s monthly OEE; months with no production are excluded, months recorded as 0% are included. OEE = Availability × Performance × Quality.",
  { x: 0.45, y: 7.12, w: 12.43, h: 0.28, margin: 0,
    fontFace: SANS, fontSize: 7.5, italic: true, color: INK3 }
);

s.addNotes(
  "Company OEE for the last three months (May-Jul 2026) is 54.1%, versus 54.7% in Q1 - flat, not a recovery. " +
  "Five families improved and five declined. The largest deterioration is 1-Piece Autocoiners at -7.0 pts, caused by two machines. " +
  "The largest gain is Urostomy at +7.6 pts. Dansac Drainable is the only family above target. " +
  "Three assets (Extruder 4, Kiefel 2, HF12) run at or near zero and should have their status confirmed before their family gaps are treated as performance problems."
);

pres.writeFile({ fileName: "/home/user/hollister-dashboard/analysis/oee-q1-vs-last3.pptx" })
  .then((f) => console.log("wrote", f));
