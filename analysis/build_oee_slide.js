// Builds the OEE PowerPoint deck from analysis/oee_data.json:
//   1. target vs. baseline (Jan-Apr 2026) vs. last 3 months (May-Jul 2026), by family
//   2. the family-by-month figures behind that chart
//   3. the workcell-level figures behind those families
//
// Regenerate the data first:  python3 build_oee_data.py
// Then:                       node build_oee_slide.js

const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const D = JSON.parse(fs.readFileSync(path.join(__dirname, "oee_data.json"), "utf8"));

/* ---------- palette ---------- */
const NAVY   = "1F3864";   // last 3 months / dominant
const BLUE   = "93ADD2";   // baseline
const GREY   = "C2C6CF";   // target (reference)
const MAROON = "9E2A2B";   // accent
const INK    = "16203A";
const INK2   = "4A5670";
const INK3   = "77809A";
const TINT   = "F4F6F9";
const BAND   = "EDF1F6";   // table zebra
const RULE   = "DFE3EA";
const GOOD   = "1C6B45";
const WARN   = "9A6212";

const SERIF = "Cambria";
const SANS  = "Calibri";

/* ---------- helpers ---------- */
const pc  = (v) => (v == null ? "—" : (v * 100).toFixed(1));
const pc0 = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
const signed = (v) => (v == null ? "—" : (v > 0 ? "+" : v < 0 ? "\u2212" : "") + Math.abs(v * 100).toFixed(1));
const absPts = (v) => Math.abs(v * 100).toFixed(1);
const mean = (xs) => {
  const p = xs.filter((v) => v != null);
  return p.length ? p.reduce((a, b) => a + b, 0) / p.length : null;
};

const MONTHS   = [...D.baselineMonths, ...D.recentMonths];
const BASE_LBL = `${D.baselineMonths[0]}–${D.baselineMonths[D.baselineMonths.length - 1]} ${D.year}`;
const REC_LBL  = `${D.recentMonths[0]}–${D.recentMonths[D.recentMonths.length - 1]} ${D.year}`;

const FAMS = D.families.filter((f) => !f.excluded);          // charted families
const DROPPED = D.families.filter((f) => f.excluded);
const C = D.company;
const atTarget = FAMS.filter((f) => f.recent >= f.target).length;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";               // 13.33 x 7.5 in
pres.author = "Hollister OEE reporting";
pres.title  = `OEE: ${BASE_LBL} vs. ${REC_LBL}`;

/* shared chrome ------------------------------------------------------- */
const footNote =
  "Source: Monthly OEE Live File — “OEE% by Month” and “WC Target OEE” tabs. " +
  `Baseline = ${BASE_LBL}; last 3 months = ${REC_LBL} (July is the most recent complete month). ` +
  "April sits in the baseline: at 56.0% it ran below the Jan–Mar average and ranked 4th of the seven months. " +
  `${DROPPED.map((f) => f.label).join(" and ")} are excluded throughout. ` +
  "BFX1’s July reading is excluded from every average, so BFX Cells and the company figure are built from May–Jun for that cell. " +
  "Averages are the equal-weighted mean of each workcell’s monthly OEE; months with no production are excluded, months recorded as 0% are included.";

function addFooter(s, text) {
  s.addShape(pres.ShapeType.line, {
    x: 0.45, y: 7.06, w: 12.43, h: 0, line: { color: RULE, width: 0.75 },
  });
  s.addText(text, {
    x: 0.45, y: 7.12, w: 12.43, h: 0.28, margin: 0,
    fontFace: SANS, fontSize: 7.5, italic: true, color: INK3,
  });
}

function addHeading(s, title, deck) {
  s.addText(title, {
    x: 0.45, y: 0.26, w: 9.6, h: 0.5, margin: 0,
    fontFace: SERIF, fontSize: 30, bold: true, color: NAVY,
  });
  s.addText(deck, {
    x: 0.47, y: 0.74, w: 12.4, h: 0.26, margin: 0,
    fontFace: SANS, fontSize: 11, color: INK3,
  });
}

/* ===================================================================== */
/* Slide 1 — the chart                                                   */
/* ===================================================================== */
const s1 = pres.addSlide();
s1.background = { color: "FFFFFF" };

s1.addText(`OEE: ${BASE_LBL} vs. Last Three Months`, {
  x: 0.45, y: 0.26, w: 12.43, h: 0.5, margin: 0,
  fontFace: SERIF, fontSize: 30, bold: true, color: NAVY,
});
s1.addText(
  [
    { text: `${REC_LBL} vs. ${BASE_LBL}`, options: { bold: true, color: INK2 } },
    { text: `   •   ${C.n} workcells across ${FAMS.length} families   •   equal-weighted mean of monthly workcell OEE`,
      options: { color: INK3 } },
  ],
  { x: 0.47, y: 0.74, w: 12.4, h: 0.26, margin: 0, fontFace: SANS, fontSize: 11 }
);

/* KPI band */
const KPI_Y = 1.10, KPI_H = 0.92;
s1.addShape(pres.ShapeType.rect, {
  x: 0.45, y: KPI_Y, w: 12.43, h: KPI_H, fill: { color: TINT }, line: { color: RULE, width: 0.75 },
});
s1.addText("COMPANY OEE — LAST 3 MONTHS", {
  x: 0.62, y: KPI_Y + 0.10, w: 3.0, h: 0.2, margin: 0,
  fontFace: SANS, fontSize: 8.5, bold: true, charSpacing: 1.2, color: INK3,
});
s1.addText(pc0(C.recent), {
  x: 0.58, y: KPI_Y + 0.28, w: 1.90, h: 0.52, margin: 0,
  fontFace: SANS, fontSize: 32, bold: true, color: MAROON, valign: "middle",
});
s1.addText(
  [
    { text: `${signed(C.recent - C.baseline)} pts`, options: { bold: true, color: INK } },
    { text: " vs. baseline", options: { color: INK2, breakLine: true } },
    { text: `${signed(C.recent - C.target)} pts`, options: { bold: true, color: INK } },
    { text: " vs. target", options: { color: INK2 } },
  ],
  { x: 2.55, y: KPI_Y + 0.30, w: 2.25, h: 0.48, margin: 0,
    fontFace: SANS, fontSize: 10, valign: "middle", lineSpacingMultiple: 1.05 }
);

[
  { x: 5.25, lab: `BASELINE ${BASE_LBL.toUpperCase()}`, val: pc0(C.baseline), sub: "April included — see note" },
  { x: 7.75, lab: "FLEET TARGET", val: pc0(C.target), sub: "Equipment-weighted" },
  { x: 10.15, lab: "FAMILIES AT TARGET", val: `${atTarget} / ${FAMS.length}`, sub: "Dansac Drainable only" },
].forEach((k) => {
  s1.addShape(pres.ShapeType.line, {
    x: k.x - 0.30, y: KPI_Y + 0.14, w: 0, h: KPI_H - 0.28, line: { color: RULE, width: 1 },
  });
  s1.addText(k.lab, {
    x: k.x, y: KPI_Y + 0.10, w: 2.3, h: 0.2, margin: 0,
    fontFace: SANS, fontSize: 8.5, bold: true, charSpacing: 1.2, color: INK3,
  });
  s1.addText(k.val, {
    x: k.x - 0.02, y: KPI_Y + 0.28, w: 2.3, h: 0.32, margin: 0,
    fontFace: SANS, fontSize: 20, bold: true, color: INK,
  });
  s1.addText(k.sub, {
    x: k.x, y: KPI_Y + 0.62, w: 2.3, h: 0.22, margin: 0,
    fontFace: SANS, fontSize: 9.5, color: INK2,
  });
});

/* chart */
s1.addText(`OEE % by workcenter family — target vs. ${BASE_LBL} vs. last 3 months`, {
  x: 0.45, y: 2.20, w: 8.2, h: 0.24, margin: 0,
  fontFace: SANS, fontSize: 11.5, bold: true, color: INK,
});
s1.addText("Sorted by last-3-month OEE, highest first. Workcell count in brackets.", {
  x: 0.45, y: 2.43, w: 8.2, h: 0.2, margin: 0,
  fontFace: SANS, fontSize: 9, color: INK3,
});

// pptxgenjs plots bar categories bottom-up, so reverse to put the leader on top
const plot = [...FAMS].reverse();
const cats = plot.map((f) => `${f.label}  (${f.n})`);

s1.addChart(
  pres.ChartType.bar,
  [
    { name: "Target OEE",              labels: cats, values: plot.map((f) => +pc(f.target)) },
    { name: `Baseline (${BASE_LBL})`,  labels: cats, values: plot.map((f) => +pc(f.baseline)) },
    { name: `Last 3 months (${REC_LBL})`, labels: cats, values: plot.map((f) => +pc(f.recent)) },
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
    catAxisLabelFontSize: 10,
    catAxisLabelColor: INK,
    catAxisLineShow: false,
    catGridLine: { style: "none" },

    showValue: true,
    dataLabelPosition: "outEnd",
    dataLabelFontFace: SANS,
    dataLabelFontSize: 7.5,
    dataLabelColor: INK2,
    dataLabelFormatCode: "0.0",

    showLegend: true,
    legendPos: "t",
    legendFontFace: SANS,
    legendFontSize: 9.5,
    legendColor: INK2,
  }
);

/* insights */
const RX = 9.02, RW = 3.86;
s1.addText("Key insights", {
  x: RX, y: 2.20, w: RW, h: 0.24, margin: 0,
  fontFace: SANS, fontSize: 11.5, bold: true, color: INK,
});

const INSIGHTS = [
  {
    c: MAROON, h: "SLIPPING, NOT RECOVERING", y: 2.48,
    body: [
      { text: `${pc0(C.baseline)} → ${pc0(C.recent)}`, options: { bold: true, color: INK } },
      { text: ` (${signed(C.recent - C.baseline)} pts) and still ${absPts(C.recent - C.target)} pts under target. March was the high point at 58.9%; nothing since has matched it.`,
        options: { color: INK2 } },
    ],
  },
  {
    c: MAROON, h: "BIGGEST DETERIORATION", y: 3.30,
    body: [
      { text: "1-Piece Autocoiners", options: { bold: true, color: INK } },
      { text: " fell ", options: { color: INK2 } },
      { text: "5.8 pts", options: { bold: true, color: INK } },
      { text: " on two machines alone: Autocoiner #5 (74.5→43.8) and #3 (67.2→40.6). The other ten held. Hollister Closed Pouch is next at −5.5, led by PCH04 and PCH01.",
        options: { color: INK2 } },
    ],
  },
  {
    c: GOOD, h: "THE TWO BRIGHT SPOTS", y: 4.18,
    body: [
      { text: "Dansac Drainable", options: { bold: true, color: INK } },
      { text: " is the only family above target (+4.4 pts), up 4.6 on P16’s recovery. ", options: { color: INK2 } },
      { text: "2-Piece Autocoiners", options: { bold: true, color: INK } },
      { text: " gained 3.9 pts to lead the fleet at 61.4%, on AC08 (+21.9).", options: { color: INK2 } },
      { text: "", options: { color: INK2 } },
    ],
  },
  {
    c: WARN, h: "WATCH ITEM — FOUR STALLED ASSETS", y: 5.06,
    body: [
      { text: "Kiefel 2", options: { bold: true, color: INK } },
      { text: " (11.8%), ", options: { color: INK2 } },
      { text: "HF12", options: { bold: true, color: INK } },
      { text: " (14.4%, no target on file), ", options: { color: INK2 } },
      { text: "BIM 2", options: { bold: true, color: INK } },
      { text: " (20.2%) and ", options: { color: INK2 } },
      { text: "BIM 1", options: { bold: true, color: INK } },
      { text: " (fell to 23.9%) sit under 25%. Confirm status before reading their family gaps as performance.",
        options: { color: INK2 } },
    ],
  },
];

INSIGHTS.forEach((i) => {
  s1.addShape(pres.ShapeType.rect, {
    x: RX, y: i.y + 0.045, w: 0.09, h: 0.09, fill: { color: i.c }, line: { type: "none" },
  });
  s1.addText(i.h, {
    x: RX + 0.19, y: i.y - 0.015, w: RW - 0.19, h: 0.2, margin: 0,
    fontFace: SANS, fontSize: 8.5, bold: true, charSpacing: 1.2, color: i.c,
  });
  s1.addText(i.body, {
    x: RX + 0.19, y: i.y + 0.19, w: RW - 0.19, h: 0.62, margin: 0,
    fontFace: SANS, fontSize: 9.5, lineSpacingMultiple: 1.0,
  });
});

const FY = 5.94;
s1.addShape(pres.ShapeType.rect, {
  x: RX, y: FY, w: RW, h: 1.06, fill: { color: NAVY }, line: { type: "none" },
});
s1.addText("RECOMMENDED FOCUS", {
  x: RX + 0.16, y: FY + 0.07, w: RW - 0.32, h: 0.18, margin: 0,
  fontFace: SANS, fontSize: 8.5, bold: true, charSpacing: 1.2, color: "A9B8D2",
});
s1.addText(
  [
    { text: "1  Rule on the four stalled assets", options: { bold: true, color: "FFFFFF", breakLine: true } },
    { text: "2  Root-cause Autocoiner #3 and #5", options: { bold: true, color: "FFFFFF", breakLine: true } },
    { text: "3  Run BIM as a programme (17 assets)", options: { bold: true, color: "FFFFFF" } },
  ],
  { x: RX + 0.16, y: FY + 0.30, w: RW - 0.32, h: 0.68, margin: 0,
    fontFace: SANS, fontSize: 9, color: "FFFFFF", lineSpacingMultiple: 1.25 }
);

addFooter(s1, footNote);
s1.addNotes(
  `Company OEE for the last three months (${REC_LBL}) is ${pc0(C.recent)}, against ${pc0(C.baseline)} for the ${BASE_LBL} baseline - a slip of ${signed(C.recent - C.baseline)} points, and ${signed(C.recent - C.target)} against target. ` +
  "April is in the baseline because it ran below the Jan-Mar average and ranked 4th of the seven months, so it is not part of any recent improvement. " +
  `${DROPPED.map((f) => f.label).join(" and ")} are excluded from the chart and the company average. ` +
  "The largest deterioration is 1-Piece Autocoiners at -5.8 points, caused by two machines. Dansac Drainable is the only family above target. " +
  "Slides 2 and 3 carry the family-by-month and workcell-level figures behind the chart."
);

/* ===================================================================== */
/* Slide 2 — family figures behind the chart                             */
/* ===================================================================== */
const s2 = pres.addSlide();
s2.background = { color: "FFFFFF" };
addHeading(s2, "The Data Behind the Chart",
  `Monthly OEE % by workcenter family, ${D.year}. Baseline and last-3-month columns are the averages plotted on slide 1.`);

const HDR = { fill: { color: NAVY }, color: "FFFFFF", bold: true };
const cellBase = { fontFace: SANS, fontSize: 10, color: INK, valign: "middle" };

const monthHead = MONTHS.map((m) => ({
  text: m, options: { ...HDR, align: "center", fill: { color: D.recentMonths.includes(m) ? NAVY : "35507F" } },
}));

const famRows = [[
  { text: "Workcenter family", options: { ...HDR, align: "left" } },
  { text: "WC", options: { ...HDR, align: "center" } },
  { text: "Target", options: { ...HDR, align: "center" } },
  ...monthHead,
  { text: BASE_LBL, options: { ...HDR, align: "center" } },
  { text: REC_LBL, options: { ...HDR, align: "center" } },
  { text: "Δ", options: { ...HDR, align: "center" } },
  { text: "vs target", options: { ...HDR, align: "center" } },
]];

FAMS.forEach((f, i) => {
  const zebra = i % 2 ? { fill: { color: BAND } } : {};
  const d = f.recent - f.baseline;
  const g = f.recent - f.target;
  famRows.push([
    { text: f.label, options: { ...zebra, align: "left", bold: true } },
    { text: String(f.n), options: { ...zebra, align: "center", color: INK2 } },
    { text: pc(f.target), options: { ...zebra, align: "center", color: INK2 } },
    ...MONTHS.map((m) => ({
      text: pc(f.months[m]),
      options: { ...zebra, align: "center", color: INK2 },
    })),
    { text: pc(f.baseline), options: { ...zebra, align: "center" } },
    { text: pc(f.recent), options: { ...zebra, align: "center", bold: true } },
    { text: signed(d), options: { ...zebra, align: "center", bold: true, color: d >= 0 ? GOOD : MAROON } },
    { text: signed(g), options: { ...zebra, align: "center", color: g >= 0 ? GOOD : MAROON } },
  ]);
});

const totalStyle = { fill: { color: "DCE3EE" }, bold: true, color: INK };
famRows.push([
  { text: `Company (${FAMS.length} families)`, options: { ...totalStyle, align: "left" } },
  { text: String(C.n), options: { ...totalStyle, align: "center" } },
  { text: pc(C.target), options: { ...totalStyle, align: "center" } },
  ...MONTHS.map((m) => ({ text: pc(C.months[m]), options: { ...totalStyle, align: "center" } })),
  { text: pc(C.baseline), options: { ...totalStyle, align: "center" } },
  { text: pc(C.recent), options: { ...totalStyle, align: "center" } },
  { text: signed(C.recent - C.baseline), options: { ...totalStyle, align: "center", color: MAROON } },
  { text: signed(C.recent - C.target), options: { ...totalStyle, align: "center", color: MAROON } },
]);

s2.addTable(famRows, {
  x: 0.45, y: 1.22, w: 12.43,
  colW: [2.55, 0.45, 0.68, ...MONTHS.map(() => 0.66), 0.95, 0.95, 0.62, 0.71],
  rowH: 0.30,
  border: { type: "solid", color: RULE, pt: 0.5 },
  fontFace: SANS, fontSize: 10, color: INK, valign: "middle",
  margin: 0.04,
});

/* what the periods mean + what was dropped */
s2.addText("How the periods are built", {
  x: 0.45, y: 4.72, w: 6.0, h: 0.22, margin: 0,
  fontFace: SANS, fontSize: 11, bold: true, color: INK,
});
s2.addText(
  [
    { text: "Baseline ", options: { bold: true, color: INK } },
    { text: `= mean of ${D.baselineMonths.join(", ")}.  `, options: { color: INK2 } },
    { text: "Last 3 months ", options: { bold: true, color: INK } },
    { text: `= mean of ${D.recentMonths.join(", ")}.`, options: { color: INK2, breakLine: true } },
    { text: "April was tested against the Q1 average before being placed. At ", options: { color: INK2 } },
    { text: "56.0%", options: { bold: true, color: INK } },
    { text: " it came in ", options: { color: INK2 } },
    { text: "0.5 pts below", options: { bold: true, color: INK } },
    { text: " the Jan–Mar mean of 56.5% and ranked 4th of the seven months, so it is not evidence of recent improvement and sits in the baseline rather than with May–Jul.",
      options: { color: INK2 } },
  ],
  { x: 0.45, y: 4.96, w: 6.0, h: 0.95, margin: 0,
    fontFace: SANS, fontSize: 10, lineSpacingMultiple: 1.05 }
);

s2.addText("Data quality — one reading to correct at source", {
  x: 0.45, y: 6.02, w: 6.0, h: 0.22, margin: 0,
  fontFace: SANS, fontSize: 11, bold: true, color: MAROON,
});
s2.addText(
  [
    { text: "2Pc Autocoiner 3 (796)", options: { bold: true, color: INK } },
    { text: " records ", options: { color: INK2 } },
    { text: "\u22129.5% OEE for May", options: { bold: true, color: MAROON } },
    { text: ", which is not physically possible. It is carried through as-is rather than silently altered, and it pulls 2-Piece Autocoiners’ last-3-month figure down to ",
      options: { color: INK2 } },
    { text: "61.4%", options: { bold: true, color: INK } },
    { text: "; excluding that one reading the family reads ", options: { color: INK2 } },
    { text: "64.5%", options: { bold: true, color: INK } },
    { text: ".", options: { color: INK2 } },
  ],
  { x: 0.45, y: 6.26, w: 6.0, h: 0.72, margin: 0,
    fontFace: SANS, fontSize: 10, lineSpacingMultiple: 1.05 }
);

s2.addText("One reading dropped by request", {
  x: 6.9, y: 6.32, w: 5.98, h: 0.22, margin: 0,
  fontFace: SANS, fontSize: 11, bold: true, color: INK,
});
s2.addText(
  [
    { text: "BFX1’s July reading (26.4%) is excluded", options: { bold: true, color: INK } },
    { text: " from BFX Cells and the company average. It ends a steady four-month slide — 71.6 → 64.7 → 45.3 → 26.4 — not a one-off, so 55.0% understates an ongoing decline.",
      options: { color: INK2 } },
  ],
  { x: 6.9, y: 6.56, w: 5.98, h: 0.46, margin: 0, fontFace: SANS, fontSize: 9.5, color: INK2 }
);

s2.addText("Families excluded from this analysis", {
  x: 6.9, y: 4.72, w: 5.98, h: 0.22, margin: 0,
  fontFace: SANS, fontSize: 11, bold: true, color: INK,
});
const dropRows = [[
  { text: "Family", options: { ...HDR, align: "left" } },
  { text: "WC", options: { ...HDR, align: "center" } },
  { text: "Target", options: { ...HDR, align: "center" } },
  { text: BASE_LBL, options: { ...HDR, align: "center" } },
  { text: REC_LBL, options: { ...HDR, align: "center" } },
]];
DROPPED.forEach((f, i) => {
  const zebra = i % 2 ? { fill: { color: BAND } } : {};
  dropRows.push([
    { text: f.label, options: { ...zebra, align: "left" } },
    { text: String(f.n), options: { ...zebra, align: "center", color: INK2 } },
    { text: pc(f.target), options: { ...zebra, align: "center", color: INK2 } },
    { text: pc(f.baseline), options: { ...zebra, align: "center", color: INK2 } },
    { text: pc(f.recent), options: { ...zebra, align: "center", color: INK2 } },
  ]);
});
s2.addTable(dropRows, {
  x: 6.9, y: 4.96, w: 5.98,
  colW: [2.42, 0.5, 0.84, 1.11, 1.11],
  rowH: 0.28,
  border: { type: "solid", color: RULE, pt: 0.5 },
  fontFace: SANS, fontSize: 10, color: INK, valign: "middle",
  margin: 0.04,
});
s2.addText(
  "Shown for reference only — excluded from the chart, the company average and every figure on slide 1.",
  { x: 6.9, y: 5.98, w: 5.98, h: 0.22, margin: 0, fontFace: SANS, fontSize: 9.5, color: INK3 }
);

addFooter(s2, footNote);
s2.addNotes("Family-by-month OEE behind the slide 1 chart, plus the April placement test and the two excluded families.");

/* ===================================================================== */
/* Slide 3 — workcell figures behind the families                        */
/* ===================================================================== */
const s3 = pres.addSlide();
s3.background = { color: "FFFFFF" };
addHeading(s3, "Workcell Detail",
  `Every workcell in the ${FAMS.length} charted families, ${C.n} in total. Sorted by change, weakest first — the top of the left column is where the losses are.`);

const included = D.machines.filter((m) => !D.excludedFamilies.includes(m.familyLabel));
const rows = included.map((m) => {
  const b = mean(D.baselineMonths.map((k) => m.months[k]));
  const r = mean(D.recentMonths.map((k) => m.months[k]));
  return { name: m.name, fam: m.familyLabel, target: m.target, b, r, d: b != null && r != null ? r - b : null };
});
rows.sort((a, z) => (a.d == null ? 1 : z.d == null ? -1 : a.d - z.d));

const SHORT = {
  "2-Piece Autocoiners": "2Pc AC", "1-Piece Autocoiners": "1Pc AC", "BIM Machines": "BIM",
  "BFX Cells": "BFX", "Hollister Closed Pouch": "Hol Closed", "Hollister Drainable Pouch": "Hol Drain",
  "Dansac Closed Pouch": "Dan Closed", "Dansac Drainable Pouch": "Dan Drain",
};

const half = Math.ceil(rows.length / 2);
[rows.slice(0, half), rows.slice(half)].forEach((chunk, col) => {
  const body = [[
    { text: "Workcell", options: { ...HDR, align: "left" } },
    { text: "Family", options: { ...HDR, align: "left" } },
    { text: "Target", options: { ...HDR, align: "center" } },
    { text: BASE_LBL, options: { ...HDR, align: "center" } },
    { text: REC_LBL, options: { ...HDR, align: "center" } },
    { text: "Δ", options: { ...HDR, align: "center" } },
  ]];
  chunk.forEach((m, i) => {
    const zebra = i % 2 ? { fill: { color: BAND } } : {};
    body.push([
      { text: m.name, options: { ...zebra, align: "left" } },
      { text: SHORT[m.fam] || m.fam, options: { ...zebra, align: "left", color: INK2 } },
      { text: m.target ? pc(m.target) : "—", options: { ...zebra, align: "center", color: INK2 } },
      { text: pc(m.b), options: { ...zebra, align: "center", color: INK2 } },
      { text: pc(m.r), options: { ...zebra, align: "center", bold: true } },
      { text: signed(m.d), options: { ...zebra, align: "center", bold: true, color: m.d >= 0 ? GOOD : MAROON } },
    ]);
  });
  s3.addTable(body, {
    x: col === 0 ? 0.45 : 6.72, y: 1.22, w: 6.16,
    colW: [1.72, 1.24, 0.72, 0.86, 0.86, 0.76],
    rowH: 0.185,
    border: { type: "solid", color: RULE, pt: 0.5 },
    fontFace: SANS, fontSize: 8, color: INK, valign: "middle",
    margin: 0.03,
  });
});

addFooter(s3,
  "Target “—” means no target is recorded for that workcell in the “WC Target OEE” tab; those workcells count toward the OEE averages but not the target average. " +
  `Baseline = ${BASE_LBL}; last 3 months = ${REC_LBL}. Source: Monthly OEE Live File — “OEE% by Month” and “WC Target OEE” tabs.`);
s3.addNotes("Workcell-level baseline and last-3-month OEE for every asset in the charted families, sorted weakest change first.");

pres.writeFile({ fileName: path.join(__dirname, "oee-q1-vs-last3.pptx") })
  .then((f) => console.log("wrote", f));
