# Evidence: NGP2 SPC automated refresh is blocked by the gateway version

**Date:** 2026-08-12
**Prepared by:** Christopher Young
**Summary in one line:** the on-premises data gateway is 11 months out of date and
Microsoft Fabric refuses to run our data refresh against it.

---

## Plain summary

The NGP2 SPC dashboard needs to refresh automatically every 15 minutes. All of the
work on our side is complete and tested — the calculations have been verified as
producing numbers identical to the current dashboard, and IT has created and
shared the database connection we need.

The refresh still cannot run. Microsoft Fabric rejects it with an explicit error
stating the gateway version is not supported.

The gateway (`HOLL_PBI_GATEWAY`) is running version **3000.286.14**, which is the
**September 2025** release — 11 months old. Microsoft supports only the most recent
six monthly releases, so this version is roughly four releases outside support.

**Required fix:** update the gateway to **3000.322**. Nothing else is outstanding.

---

## The error, exactly as Microsoft returns it

```json
{
  "status": "Failed",
  "failureReason": {
    "errorCode": "UnsupportedGatewayVersion",
    "message": "The gateway version you are using is not supported.
                Please update to the latest on-prem gateway version and try again.",
    "isRetriable": false
  }
}
```

Note `isRetriable: false` — Microsoft classifies this as a permanent failure, not
a transient one. Retrying cannot succeed.

## Reproduced twice, 26 hours apart, with everything else fixed in between

| When (UTC) | Error code | Context |
|---|---|---|
| **2026-08-11 12:21** | `UnsupportedGatewayVersion` | First attempt, run with IT during setup |
| 2026-08-12 13:45 – 14:12 | *(5 attempts, unrelated cause)* | My own API-created test items — see note below |
| **2026-08-12 14:35** | `UnsupportedGatewayVersion` | After credentials and permissions were fully corrected |

The two attempts that matter are the **first and last**. Between them, every other
variable was fixed: the SQL connection was created, access was granted to my
account, and credentials were entered manually and confirmed working. The error
did not change. That isolates the gateway version as the cause.

> **Note on the five middle attempts, for completeness.** Those were test
> dataflows I created programmatically while diagnosing. They failed faster
> (3–9 seconds) with generic errors because of a problem with how I was creating
> them, not with the gateway. They are not evidence of the gateway problem and
> are listed only so the full history is accounted for.

## Evidence that everything else is working

- The SQL connection to `10.62.27.4 / db_ProcessData` previews **real data
  successfully** in the Power Query editor — timestamps, fault codes, live rows.
  The database read is fine.
- My account's access to the connection is confirmed in use by Fabric's own
  connection records.
- The failing attempt ran for a full **35 seconds**, meaning it reached actual
  gateway execution rather than failing validation up front.

---

## How to verify this independently

### Option 1 — Fabric portal (no tooling needed)

1. Open the **Smart Factory** workspace
2. Open the **NGP2 SPC Bronze** dataflow
3. Open **Refresh history** / **Recent runs**
4. The failed runs and the `UnsupportedGatewayVersion` message are listed there

### Option 2 — Fabric REST API

```bash
az login --allow-no-subscriptions --tenant db08e4ba-c0c6-4893-8bbe-8f3b86b87652

az rest --method get \
  --resource "https://api.fabric.microsoft.com" \
  --url "https://api.fabric.microsoft.com/v1/workspaces/daff049b-5e21-4d61-8cf2-465032703de5/dataflows/0f7c96be-16f5-4dee-a0fe-d78435a1a49a/jobs/instances"
```

Returns the full refresh history including the error codes above.

### Option 3 — check the version on the gateway machine

Open the **On-premises Data Gateway** app on the gateway host. The **Status** tab
shows the installed version, and there is a **Check for updates** button.

---

## Version context

| Release | Version |
|---|---|
| **Installed — September 2025** | **3000.286** |
| January 2026 | 3000.302 |
| February 2026 | 3000.306 |
| March 2026 | 3000.310 |
| April 2026 | 3000.314 |
| **June 2026 — latest** | **3000.322** |

Microsoft ships monthly and supports the last six releases.

Separately, the specific Fabric feature we need — **Dataflow Gen2 with CI/CD** —
was tied to the **October 2025** release, `3000.290`. The installed version is one
release below even that. And since **April 2026 Microsoft has removed the option
to create the older dataflow type**, so there is no way to build this using
something the current gateway supports.

---

## What is being asked for

Update **HOLL_PBI_GATEWAY (Primary)** (id `23abfba2-f963-4c8d-ac82-acfbbf7e33e2`)
to **3000.322**.

Practical points for whoever owns the gateway:

- **The portal's update button will not work for this jump.** UI-triggered updates
  require the November 2025 baseline or later; 3000.286 predates it. This first
  update must be run from the installer on the gateway host. Updates after that
  can be triggered from the portal.
- Existing connections and configuration are **preserved** — it is an in-place
  upgrade, not a rebuild.
- Requires **10 GB free disk** on the gateway host.
- The gateway currently serves roughly 32 other connections which are all working
  normally. This request is not caused by any of those failing — the gateway is
  healthy for its current load and simply too old for this newer Fabric workload.
- Bringing it inside Microsoft's support window is arguably worth doing
  independently of this project.

## Also outstanding, separately

IT has reported that enterprise Fabric compute is exhausted. That is a distinct
issue from the gateway and may also need resolving before the 15-minute refresh
can run. Our workload is very small — four tables and roughly 350 rows of results
— so the required amount of compute is minimal, but it does need to exist.
