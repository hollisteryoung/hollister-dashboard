# Viable Fabric options for NGP2 SPC — analysis

Revised 2026-08-12 after two constraints were established:

1. **No local machine.** Nothing may depend on a laptop or workstation staying on.
   Everything must run in Fabric. *(This rules out the local-compute option that
   an earlier version of this document recommended.)*
2. The on-prem gateway is 11 months out of date and updating it is not something
   we can request easily; IT reports enterprise Fabric compute is exhausted.

Treating this as **three separable problems** is still the right frame — the
blockers don't affect all of them equally.

| Problem | Question |
|---|---|
| **Ingest** | how does on-prem SQL data get into Fabric? |
| **Compute** | where does the SPC maths run? |
| **Serve** | how does the report read the results? |

---

## The finding that matters most

**We picked the one Fabric ingestion method our gateway is too old for.**

| Workload | Minimum gateway | Installed `3000.286.14`? |
|---|---|---|
| Dataflow Gen2 (CI/CD) | `3000.290` (Oct 2025) | ✗ one release short |
| **Fabric Pipeline Copy activity** | **`3000.214.2`** | ✓ **comfortably above** |

Fabric Data Pipelines have a far lower gateway floor than Dataflow Gen2. Copy
activity also accepts a **custom SQL query** as its source, so the exact narrow
column/filter set already verified for parity can be used unchanged.

And the obvious worry doesn't apply: **staging (and therefore an Azure Storage
Account, which we don't have) is only required for _Warehouse_ destinations.**
Lakehouse destinations use a different ingestion path and copy directly from an
on-premises source.

**Consequence: the gateway update is no longer a blocker — it becomes optional.**

---

## Ingest options

| # | Option | Gateway? | Works on current gateway? | Verdict |
|---|---|---|---|---|
| A | Dataflow Gen2 (CI/CD) | yes | ✗ needs `3000.290` | Blocked — this is what we tried |
| B | **Pipeline Copy activity** | yes | ✓ needs `3000.214.2` | **Best available. Untested.** |
| C | Notebook connecting directly to SQL | — | — | **Impossible.** Notebooks have no gateway support at all |
| D | Local script writing to OneLake | none | — | **Ruled out** by the no-laptop constraint |
| E | Managed Private Endpoint | none | — | Separate network-engineering project |
| F | VNet data gateway | different | — | Needs on-prem reachable from an Azure VNet (ExpressRoute/VPN) |
| G | Mirroring for SQL Server | yes | unverified | Continuous replication; heavier than needed, and version floor unknown |

### Copy activity is also markedly cheaper than Dataflow Gen2

This matters because capacity is the binding constraint. Measured comparisons put
Copy activity at roughly **4.6× cheaper than Dataflow Gen2** for SQL Server
ingestion, and Gen2 at **5–9× the CU of an equivalent Gen1 dataflow**.

The reason is Gen2's billing model — it charges across several engines at once,
approximately:

```
(Mashup hours × 16) + (SQL hours × 6) + (FastCopy hours × 1.5)
```

Gen2 **stages data by default**, pushing it through both the Mashup engine and a
Warehouse SQL engine. Staging is reported as the single largest avoidable
multiplier, on the order of 700 CU of overhead per run. (The
`StagingLakehouseForDataflows` and `StagingWarehouseForDataflows` items already in
the workspace are that machinery.) Copy activity has none of it for a Lakehouse
destination.

At 96 runs per day the difference compounds directly onto the resource IT says is
exhausted. So even once the gateway is updated and Gen2 becomes available, **Copy
activity remains the better choice on cost grounds alone.**

> The ~4.6× *ratio* is the reliable finding and is expected to hold across SKU
> sizes. Absolute CU figures for this specific workload need measuring once it
> runs — the published benchmarks used different data volumes.

Two things to confirm when testing B:

- Outbound `*.frontend.clouddatahub.net` must be allowed from the gateway host.
  One practitioner note suggests existing firewall rules usually already cover
  this, since it isn't typically distinguished from Dataflow Gen2 traffic.
- Copy activity can only use one gateway per activity — fine here, we have one.

---

## Compute options

With local compute ruled out, this has to run inside Fabric.

| # | Option | Suitability |
|---|---|---|
| 1 | **Python notebook + DuckDB** | **Correct choice.** Starts in seconds; reuses the existing pipeline unchanged via `DB_BACKEND=duckdb`; parity already verified as numerically identical |
| 2 | PySpark notebook | Rejected — 1–2 minute cold start is a large slice of a 15-minute window, for no benefit at this data size. **Also a cost decision:** in published benchmarks the Spark-session notebook burned ~46% more CU than Copy activity purely from spinning up Spark. A Python notebook avoids that penalty |
| 3 | Rewrite SPC maths in SQL / Warehouse | Rejected — the counter-reset detection and Laney p′ logic are intricate; parity was verified specifically to avoid reimplementing them |
| 4 | Local machine | Ruled out by constraint |

Option 1 is already built and tested. Nothing about the no-laptop constraint
changes it — it was always designed to run as a Fabric notebook.

---

## Serve options

| # | Option | Capacity needed | Refresh cadence achievable |
|---|---|---|---|
| i | **Direct Lake** | **F SKU required** (not Pro; PPU unconfirmed and unlikely) | **No refresh at all** — reads Delta directly |
| ii | Import from Lakehouse SQL endpoint | model can be Pro/PPU, but the Lakehouse still sits on capacity | 8/day Pro · 48/day PPU |
| iii | Import from OneLake files | minimal — storage read only | 8/day Pro · 48/day PPU |
| iv | Import via `Python.Execute` | — | **Blocked** — the original problem |

### The hard triangle

**15-minute freshness + Power BI + no Fabric capacity — pick two.**

Scheduled refresh caps at 8/day (Pro) and 48/day (PPU). 15 minutes needs 96/day.
Direct Lake needs **no refresh at all**, which is precisely why it solves this —
and precisely why it requires capacity.

The XMLA endpoint reportedly isn't subject to the 48/day cap, which *might* allow
15-minute Import refreshes on PPU. **Unverified — confirm before relying on it.**

---

## Capacity — now the single remaining blocker

| Option | Notes |
|---|---|
| Enterprise headroom | Needs an answer from IT: temporary spike, or the state for months? |
| **Quantified ask** | See below — likely the strongest play |
| Trial capacity (3 × FTL64, Active) | 60 days. **Critical catch: trial capacity does _not_ grant free-license users viewer access.** Good for proving the design, useless for distributing to the floor |
| PPU | No Direct Lake, and **every viewer needs a PPU licence** |
| Dedicated F SKU | Costs money; needs a business case — which a working trial deployment would supply |

### Being honest about our footprint

With compute now inside Fabric rather than local, our consumption is **modest but
no longer negligible**:

- Copy activity: 5 small incremental table copies per run
- Python notebook: roughly 1–2 minutes of small-node compute per run, every 15
  minutes — call it a 10–15% duty cycle on one node
- Direct Lake serving: a ~350-row model, trivial
- Storage: four small Delta tables, 3-day rolling history

That's a real but small draw against a 64-CU capacity. Worth going back to IT with
those numbers rather than accepting a blanket "no capacity" — the exhaustion is
almost certainly driven by the Spark notebooks, S&OP models and Eventstream
already in the workspace, not by anything we'd add.

---

## Recommended path

### Pipeline Copy activity → Lakehouse → Python notebook → Direct Lake  ★

`B + 1 + i`

- **Works with the current gateway** — no update required
- **No Azure Storage Account needed** — Lakehouse destination avoids staging
- **Fully cloud-hosted** — nothing depends on a machine staying on
- 15-minute cadence satisfied with no refresh scheduling
- Compute layer is already built and parity-verified
- Reuses the SQL connection IT already created and shared

**Single remaining dependency: Fabric capacity.**

### Fallback if capacity is a hard no

`B + 1 + iii` — same ingest and compute, but Import mode instead of Direct Lake,
conceding a 30-minute cadence on PPU. Still fully cloud-hosted. Only worth it if
capacity genuinely cannot be obtained, since it gives up the 15-minute target.

### Keep alive in parallel

The gateway update to `3000.322`. No longer blocking, but 11 months out of
Microsoft's support window is a problem worth closing on its own merits — and it
would re-open the Dataflow Gen2 route as an alternative.

---

## What to test next, cheapest first

1. **Build a Pipeline with a Copy activity** against the existing SQL connection,
   one small table, into a Lakehouse. This is the make-or-break test: it either
   proves the current gateway is sufficient, or it doesn't. Needs capacity to run,
   but only briefly.
2. **Confirm Direct Lake renders** the report against a Lakehouse table.
3. **Re-ask IT about capacity with the numbers above**, not "do you have capacity."

Step 1 is the one that matters. If Copy activity works on the current gateway, the
entire gateway problem — the thing that has consumed the most time — disappears.
