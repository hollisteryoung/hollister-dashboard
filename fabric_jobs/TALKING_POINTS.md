# Explaining the gateway change — talking points

Plain-language companion to [`IT_REQUEST_gateway.md`](IT_REQUEST_gateway.md).
That document is written for IT to action; this one is for the conversation.

## The 20-second version

> "Last time, the dashboard was asking the gateway to run a Python program to get
> its data, and the gateway isn't allowed to do that. I've changed it so the
> Python now runs up in Fabric instead. All the gateway has to do is read some
> rows out of SQL Server — the normal thing it does for every other report. So
> the connection you couldn't create should now just be a standard SQL Server
> one."

## The analogy

The gateway is a **courier** between the factory network and the cloud.

- **Before:** we were asking the courier to also do the cooking. Couriers aren't
  allowed to cook — that's the security rule we hit.
- **Now:** the courier just picks up raw ingredients and drops them off. The
  cooking happens in the cloud kitchen (Fabric), where it's allowed.

Same meal at the end. The courier's job got simpler, not more complicated.

## The actual ask

> "Please add a SQL Server connection on the gateway pointing at the Ignition
> database, and give me permission to use it. It's read-only, one service
> account, five tables."

Then the part people forget — **existing isn't enough, it has to be shared:**

> "Also add me as a user on that connection, otherwise I can't see it and can't
> hook anything up to it. I don't need any gateway admin rights — just the User
> role on that one connection."

## Likely questions

**"So is the Python gone or not?"**

> "Gone from the gateway. Nothing gets installed on the gateway machine and it
> never runs any code of mine. The Python still does the statistics, but it runs
> in Fabric now."

Don't say "there's no Python anywhere" — it isn't true, and it's the kind of
thing that unravels later. The honest line is *no Python on the gateway*.

**"Which report do I add to the gateway?"**

> "None, actually — that's changed. The report itself doesn't go through the
> gateway any more. It's a new separate thing in Fabric that connects to it."

Worth leading with, because they'll go looking for the dataset they tried last
time and won't find it.

**"What's it doing to the database?"**

> "Reading, nothing else. Five tables, the same ones the current dashboard
> already reads, with the read-only account. Big-ish read the first time, then
> just new rows every 15 minutes."

**"Why every 15 minutes?"**

> "It's a process control chart — operators need to see a lane drifting out of
> spec while the shift is still running, not the next morning."

**"Will this put load on the historian?"**

> "Less than today. It only pulls the columns it needs and only new rows after
> the first load — about 1.6 million rows once, then very little."

## If someone technical wants detail

> "The gateway feeds a Fabric Dataflow, which lands the raw tables in a
> Lakehouse. A Python notebook in Fabric does the SPC maths off that, and the
> report reads the results straight out of the Lakehouse. So the only gateway hop
> is the SQL read at the very start."

And if they ask whether the numbers changed:

> "No — I ran the old and new versions side by side against the same data and
> diffed the output. Identical."

Have that ready. It's usually what turns a change like this from a question into
a nod.

## What not to over-claim

| Don't say | Say instead |
|---|---|
| "There's no Python any more" | "No Python on the gateway" |
| "Nothing changed" | "The numbers are identical; the plumbing changed" |
| "It's just a permissions thing" | "A new SQL connection, plus access for me to use it" |
| "It'll be live today" | "Once the connection exists, I can wire it up" |

## Round 2 — the connection exists, refresh still fails (2026-08-12)

IT created the SQL connection and granted access. Confirmed working on our side:
the connection is visible, and its credentials have genuinely been used from this
account. That part is done — don't re-ask for it.

But every refresh attempt against it has failed. Three attempts, three failures,
none transient (retried twice back-to-back, same result both times):

- First attempt: explicit `UnsupportedGatewayVersion`
- Two retries on the same dataflow: generic `EntityUserFailure` ("something went
  wrong, please try again later")

Neither of us has visibility into the gateway itself (that needs a gateway-level
role, which is a separate thing from the connection-level access already
granted) — the portal shows "Online" for the gateway, but that only means the
service is running and connected, not which version is installed. The first
error named the version specifically, and Microsoft documents both later errors
as generic symptoms of the same class of problem (outdated or unhealthy
gateway).

### The ask — no Fabric portal access needed for this one

> "Refreshing this still fails — the first attempt named the cause directly:
> the gateway version isn't supported. Could someone open the On-premises Data
> Gateway app on the gateway machine itself? The Status tab shows the installed
> version and has a 'Check for updates' button right there. If it's behind,
> updating it should be enough — no Fabric changes needed on your end beyond
> that."

This doesn't require any Fabric/Power BI role at all — just local access to the
gateway host, which whoever manages it already has.
