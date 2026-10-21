---
id: hosts.agent-unhealthy
version: 1
title: Session host agent unhealthy
trigger:
  schedule: "0 6 * * 1-5"   # 06:00 UTC on weekdays
  runMode: read-only
agent: hosts
reads:
  - id: health
    kql: wvd-agent-health-latest-7d
  - id: hosts                       # for the agent when it investigates: the live status of every host
    tool: virtualdesktop_hostpool_sessionhost_list
expect:
  - fact: reportingHosts            # no report at all is a finding of its own, never clear
    from: health
    measure: rows
    op: ge
    value: 1
    onFail: report
  - fact: unhealthyHosts
    from: health
    measure: sum:Problem
    op: eq
    value: 0
    onFail: investigate
evidence: [c11.hostPool.sessionHosts.total]
cites:
  - avd-ops/avd-second-brain@f59a1c5207f89ba71f4752fe2a52a091ca6fd376:docs/knowledge/operations/README.md#diagnosing
  - https://learn.microsoft.com/azure/virtual-desktop/troubleshoot-agent
budget: {tokensUsd: 0.50}
---
# Session host agent unhealthy

**What it checks.** Each session host's latest AVD agent report in the last seven days: whether it says anything other than `Available` (a `Shutdown` host is deallocated, not unhealthy), and whether a host that isn't shut down has gone silent for two hours. The agent reports its status and the health checks it ran (domain join, FSLogix, SxS stack, URL access and others) to `WVDAgentHealthStatus`; a host that isn't `Available` takes no new sessions.

**Clear** when at least one host reports and no host is unhealthy or silent. A clear run never reaches a model. **No reports at all** (no hosts, or the workspace lost its diagnostic setting) is a finding reported without a model: missing data is not health.

**When it isn't clear,** the Hosts agent gets the failing hosts and their failed health checks, and the session host list, and answers three questions with citations: which hosts, which check failed on each, and whether the host is deallocated on purpose (a deallocated host is not unhealthy: spec section 5a). It proposes nothing in v1. The operator's next step is the fix for that health check in [Troubleshoot the Azure Virtual Desktop Agent](https://learn.microsoft.com/azure/virtual-desktop/troubleshoot-agent); restarting the agent becomes a catalog action at build step 3, with every host precondition in spec section 5a.

**When it can't run** (the workspace or the tool didn't answer), the outcome is `could-not-run`, never `clear`: missing data is not health.

**Privacy.** The query returns host names and check results, never user names. Host names stay in the plan store and avd-buddy, never in a job summary (R17).
