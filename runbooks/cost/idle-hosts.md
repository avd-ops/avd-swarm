---
id: cost.idle-hosts
version: 1
title: Idle hosts and the cost tail
trigger:
  schedule: "0 7 * * 1"     # 07:00 UTC on Mondays
  runMode: read-only
agent: cost
reads:
  - id: idle
    kql: wvd-hosts-without-connections-7d
  - id: cost
    tool: cost_month_to_date_by_resource
expect:
  - fact: idleRunningHosts
    from: idle
    measure: rows
    op: eq
    value: 0
    onFail: investigate
evidence: [c11.hostPool.sessionHosts.total, c11.hostPool.startVmOnConnect]
cites:
  - avd-ops/avd-second-brain@f59a1c5207f89ba71f4752fe2a52a091ca6fd376:docs/knowledge/cost/README.md#capping
budget: {tokensUsd: 0.50}
---
# Idle hosts and the cost tail

**What it checks.** Session hosts that reported in the last day, so they are running and billing, but had no user connection in seven days. A host like that is money spent on nothing, unless it's kept warm on purpose.

**Clear** when every running host had a connection in the week. A clear run never reaches a model.

**When it isn't clear,** the Cost agent gets the idle hosts and month-to-date cost by resource, and answers with citations: which hosts, what each has cost this month, and whether the host pool has Start VM on Connect and auto shutdown, so the host could be stopped without users noticing. It proposes nothing in v1; Stop becomes a catalog action at build step 3, not power-locked and above the availability floor (spec section 5a). The operator's step today is the landing zone's own auto shutdown ([decision 0011](https://github.com/avd-ops/avd-landing-zone/blob/master/build/decisions/0011-auto-shutdown.md)).

**When it can't run,** the outcome is `could-not-run`, never `clear`.

**Privacy.** Host names and dollars, no user names. Host names stay out of job summaries (R17).
