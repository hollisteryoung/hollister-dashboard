# All viable Fabric options for NGP2 SPC — analysis

Written 2026-08-12, after hitting two blockers: the on-prem gateway is 11 months
out of date and can't easily be updated, and IT reports enterprise Fabric compute
is exhausted.

The key move is to stop treating this as one problem. It's **three separable
problems**, and the blockers only affect some of them.

| Problem | Question |
|---|---|
| **Ingest** | how does on-prem SQL data get into Fabric? |
| **Compute** | where does the SPC maths run? |
| **Serve** | how does the report read the results? |

---

## Two findings that change everything

### 1. Pipeline Copy activity needs a *much* older gateway than Dataflow Gen2

| Workload | Minimum gateway | Installed `3000.286.14`? |
|---|---|---|
| Dataflow Gen2 (CI/CD) | `3000.290` (Oct 2025) | ✗ one release short |
| **Fabric Data Pipeline Copy activity** | **`3000.214.2`** | ✓ **comfortably above** |

We picked the one Fabric ingestion method that our gateway is too old for. Copy
activity clears the bar by a wide margin — **on the current gateway, no update
needed.**

### 2. OneLake accepts direct writes — no gateway involved at all

OneLake is ADLS Gen2 under the hood and exposes the standard Blob/ADLS APIs. A
script on an on-prem machine can write Delta tables straight into a Lakehouse
using `deltalake` (delta-rs) against an `abfss://` path — **no gateway, no
Dataflow, no Fabric compute for ingestion.**

We already have this working: `deltalake` is installed, `spc_gold_refresh.py`
already writes the Gold Delta tables locally, and
`deploy/sync_code_to_lakehouse.py` already writes to OneLake over the ADLS API.

---

## Ingest options

| # | Option | Gateway? | Version OK? | Verdict |
|---|---|---|---|---|
| A | Dataflow Gen2 (CI/CD) | yes | ✗ needs `3000.290` | **Blocked** — what we tried |
| B | **Pipeline Copy activity** | yes | ✓ needs `3000.214.2` | **Viable now, untested** |
| C | Notebook direct to SQL | n/a | n/a | **Impossible** — notebooks have no gateway support, at all |
| D | **Local script → OneLake (ADLS API)** | **none** | n/a | **Viable now, mostly proven** |
| E | Managed Private Endpoint | none | n/a | Separate network-engineering project |
| F | VNet data gateway | different | n/a | Needs on-prem reachable from an Azure VNet (ExpressRoute/VPN) |
| G | Mirroring for SQL Server | yes | unverified | Worth checking; continuous replication may be overkill |
| H | Open mirroring landing zone (push) | none | n/a | Push Parquet into a landing zone; more machinery than D for the same result |

Notes on B: Copy activity accepts a **custom SQL query** as source, so the exact
narrow projection/filter set we already verified can be used as-is. Caveats to
check: it needs outbound `*.frontend.clouddatahub.net` from the gateway host, and
some sink types require an Azure Storage Account for staging — we have **no Azure
subscription**, so if staging is demanded for on-prem → Lakehouse, B dies.

Notes on E/F: both need IT network work of the same order as the original Python
gateway problem. Not obviously easier than just updating the gateway.

---

## Compute options

| # | Option | Runs where | Fabric CU cost | Status |
|---|---|---|---|---|
| 1 | Python notebook + DuckDB | Fabric | small but non-zero | Built, verified locally |
| 2 | PySpark notebook | Fabric | larger (Spark cold start ~1-2 min) | Rejected: too slow for 15-min cadence |
| 3 | **Local machine (current pipeline)** | on-prem | **zero** | **Already proven — 251s against live SQL** |

Option 3 matters more than it looks. The SPC maths is the *only* reason we needed
Python in Fabric. If it runs locally, the notebook, the Bronze layer, and the
whole Dataflow question all disappear — Fabric only stores and serves results.

---

## Serve options

| # | Option | Capacity needed | Refresh cadence achievable |
|---|---|---|---|
| i | **Direct Lake** | **F SKU required** (not Pro; PPU unconfirmed/unlikely) | **No refresh at all** — reads Delta directly |
| ii | Import from Lakehouse SQL endpoint | model on Pro/PPU, but Lakehouse still sits on capacity | 8/day Pro, 48/day PPU |
| iii | Import from OneLake files (`AzureStorage.DataLake`) | minimal — storage read only | 8/day Pro, 48/day PPU |
| iv | Import via Python.Execute | — | **Blocked** — the original problem |

### The hard triangle

**15-minute freshness + Power BI + no Fabric capacity — pick two.**

Scheduled refresh is capped at 8/day (Pro) and 48/day (PPU). 15 minutes needs
96/day. So:

- **Direct Lake needs no refresh at all** → satisfies 15-min trivially → but
  requires F SKU capacity
- **Import mode** on PPU maxes out at 30-minute cadence
- The XMLA endpoint reportedly isn't subject to the 48/day cap, which *may* allow
  15-min on PPU with Import mode — **unverified, worth confirming before relying
  on it**

This is why the capacity question can't be dodged: the hard 15-minute requirement
is what forces Direct Lake, and Direct Lake is what forces capacity.

---

## Capacity options

| Option | Notes |
|---|---|
| Wait for enterprise headroom | Unknown timeline; needs IT answer |
| **Argue our footprint is negligible** | See below — probably the strongest play |
| Trial capacity (FTL64 × 3 available, Active) | 60 days. **Critical catch: trial capacity does _not_ grant free-license users viewer access** — fine for proving it works, useless for distributing to operators |
| PPU | No Direct Lake. And **every viewer needs a PPU licence** — kills floor distribution |
| Dedicated F SKU | Costs money; needs a business case |

### The negligible-footprint argument

If compute runs locally (option 3) and ingestion is a direct OneLake write
(option D), our entire Fabric consumption is:

- Storage: four small Delta tables, 3-day rolling history (order 10⁴ rows)
- Direct Lake serving: a **~350-row** model

That's a rounding error against a 64-CU capacity. "Compute is exhausted" is
almost certainly about other workloads — Spark notebooks, the S&OP models, the
Eventstream. Worth going back with a *quantified* ask rather than accepting a
blanket no.

---

## Recommended combinations, ranked

### 1st choice — Local compute → OneLake → Direct Lake  ★

`D + 3 + i`

- **No gateway at all** — the 11-month-old gateway becomes irrelevant
- **No Dataflow Gen2** — the CI/CD version requirement becomes irrelevant
- **No Fabric compute for the pipeline** — near-zero CU
- 15-minute cadence satisfied with no refresh scheduling
- Mostly built already: local pipeline proven, `deltalake` working, Gold tables
  verified, OneLake write pattern already written

Only Fabric requirement: a workspace on *some* F capacity with room for a
350-row model. Trades a gateway dependency for a dependency on the always-on
machine — which we already had anyway.

### 2nd choice — Pipeline Copy activity → notebook → Direct Lake

`B + 1 + i`

- Fully Fabric-native, nothing depends on a local machine
- **Works with the current gateway** — this is the finding that revives it
- Costs real CU (Copy + notebook), and needs the `clouddatahub.net` firewall rule
- Dies if Copy activity demands an Azure Storage Account for staging

Worth testing precisely because it needs no gateway update and no local dependency.

### 3rd choice — Local compute → OneLake → Import mode, 30-min cadence

`D + 3 + iii`

- Needs **no Fabric capacity at all** — runs on Pro/PPU
- Concedes the 15-minute requirement (30-min on PPU, or 8/day on Pro)
- Genuine fallback if capacity is a hard no

### Also viable — gateway update, then original plan

`A + 1 + i`

The design is built and verified; it just needs `3000.322`. Worth keeping alive in
parallel since 11 months out of support is a problem IT arguably should fix
regardless.

---

## What to test next, cheapest first

1. **Write a Delta table from this machine into OneLake** and confirm it appears
   as a Lakehouse table. Proves the whole of choice 1. Needs no IT, no gateway.
2. **Confirm Direct Lake works on an available capacity** — even a trial one — and
   that the report renders against it.
3. **Try a Pipeline Copy activity** on the current gateway. Proves or kills choice 2.
4. **Re-ask IT about capacity with numbers**, not "do you have capacity."

Steps 1 and 2 are entirely within our control and settle the recommended path.
