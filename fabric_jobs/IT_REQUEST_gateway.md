# Request: SQL Server connection on the enterprise data gateway

**Requested by:** christopher.young@hollister.com
**Purpose:** enable scheduled refresh for the NGP2 SPC Dashboard (Power BI / Fabric)
**Raised:** 2026-08-10

## Context — this is the follow-up we agreed on

On a recent call we tried to add the NGP2 SPC dashboard to the enterprise
gateway and couldn't, because its data source was a **Python script**. I said I
would remove the Python data source so it could be added.

**The rewrite is complete and tested, but not yet published — so if you look at
the `NPG2 SPC Dashboard` semantic model in the Smart Factory workspace today, it
will still show the Python source. That is expected.** It cannot be switched over
until the new connection exists, because the replacement reads from a Fabric
Lakehouse that this connection is what fills. Chicken and egg.

**Importantly, the connection I'm asking for is not for that semantic model.** It
is for a new **Dataflow Gen2** item that doesn't exist yet. Nothing about the
existing model needs to change for you to create the connection, and creating it
does not affect the existing model or report.

Sequence:

1. You create the SQL Server connection (this request) — nothing else changes
2. I build the Dataflow against it, which fills a Lakehouse
3. I repoint the semantic model at that Lakehouse — **at which point the Python
   source disappears from the model entirely**
4. The gateway is then only ever doing a plain SQL read

If it would help to see step 3 before doing step 1, say so — I can publish a
side-by-side copy of the model that reads from the Lakehouse, with no Python
source, for you to inspect. It won't refresh automatically until this connection
exists, but you can confirm the data source is a plain Lakehouse read.

## What is being asked for

On the existing enterprise gateway:

1. Add a **SQL Server** connection using the details below.
2. Grant `christopher.young@hollister.com` the **User** role on that connection,
   so it can be bound to a Fabric Dataflow Gen2 item.

Step 2 matters and is easy to miss: without it the connection exists but is
invisible to me, and a dataflow cannot be bound to it. (Currently my account sees
zero gateways and zero SQL connections — see the bottom of this document.)

**To be clear on scope: I do not need any gateway-level role.** Per Microsoft's
guidance, "a user who just needs to use the gateway to connect to a data source
doesn't need to belong to a gateway role — in this case they'll only have the
*User* connection role." So I am not asking to be a gateway admin or a connection
creator; just the **User** role on this one connection, which allows using it and
nothing else. Connection roles are managed from **Manage connections and
gateways**, or the Power Platform admin center under Data Gateways.

### Connection details

| Field | Value |
|---|---|
| Data source type | SQL Server |
| Server | `10.62.27.4` (port 1433) |
| Database | `db_ProcessData` |
| Authentication | Basic / SQL authentication |
| Account | `ssu_DataViewer` — existing **read-only** service account |
| Privacy level | Organizational |
| Target workspace | Smart Factory (`daff049b-5e21-4d61-8cf2-465032703de5`) |

> The password for `ssu_DataViewer` should be supplied by whoever owns that
> account rather than reused from any existing document — it was recently
> exposed in plaintext and is due for rotation.

### Access pattern

Read-only `SELECT` against **five** tables in `db_ProcessData`, already used
today by the existing dashboard:

- `dbo.tbl_SCP_NGP2_StatusBlocks`
- `dbo.tbl_TG_NGP2_HH_PartsPerMinute`
- `dbo.tbl_TG_NGP2_HH_OutputStats`
- `dbo.tbl_TG_NGP2_HH_StationsCounters_Hydration`
- `dbo.tbl_TG_NGP2_HH_StationsCounters_Foil`

Only a narrow column projection and a filtered subset of counter rows are read:
roughly **1.6M rows on the initial load**, then incremental (new rows only)
every 15 minutes. No writes, no schema changes, no stored procedures.

## Why this is needed

The NGP2 SPC Dashboard currently gets its data through Power BI's **Python
script** connector. That works in Power BI Desktop but cannot be scheduled,
because gateway policy does not permit Python script data sources.

The dashboard has been rearchitected so that **no Python runs on the gateway at
all**. The gateway is used only for a plain SQL Server read; the Python
computation now runs inside Microsoft Fabric, which needs no gateway. So this
request is for a standard SQL Server connection — not a policy exception.

```
Gateway  ──SQL Server read──>  Fabric Dataflow Gen2  ──>  Lakehouse
                                                            │  (no gateway)
                                       Fabric notebook ─────┘
                                                            │
                                       Power BI report  <───┘
```

## What this is not

- **Not a Python data source.** This is the change that unblocks what we tried on
  the call. No Python runtime is needed on the gateway host, and no Python
  script executes anywhere the gateway can see. (Python still computes the SPC
  statistics, but it now runs inside Fabric, which does not involve the gateway.)
- **Not the semantic model.** The Power BI dataset itself no longer needs a
  gateway connection either — it reads from the Fabric Lakehouse via Direct Lake.
  The only thing binding to the gateway is the Dataflow Gen2 extract.
- **Not the personal gateway.** My existing personal-mode gateway cannot be used:
  Fabric Dataflow Gen2 supports only standard and VNet gateways.
- No new service account, database, or firewall rule beyond reaching
  `10.62.27.4:1433` from the gateway host.

## Current state of my access, for context

The account `christopher.young@hollister.com` currently sees:

- `GET /v1/gateways` → 0 gateways
- `GET /v2.0/myorg/me/gatewayClusters` → 0 clusters
- 32 gateway data sources, all File / SharePoint / ODBC / Folder — no SQL Server
- All on-premises connections are `OnPremisesGatewayPersonal` (personal mode)

Everything else for this dashboard is built and verified; the gateway connection
is the only remaining dependency.
