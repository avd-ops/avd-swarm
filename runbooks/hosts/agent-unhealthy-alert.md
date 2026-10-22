---
id: hosts.agent-unhealthy-alert
version: 1
title: Session host agent unhealthy (alert)
trigger:
  alert: "avd-*-agent-health*"     # the alert rule name pattern this answers (a glob, case-insensitive)
  runMode: read-only
agent: hosts
reads:
  - id: health
    kql: wvd-agent-health-latest-7d
  - id: hosts                       # for the agent when it investigates: the live status of every host
    tool: virtualdesktop_hostpool_sessionhost_list
    args: {hostpool-resource-id: "${hostPoolId}"}   # draft name until the S1a run lists the tool's parameters
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
# Session host agent unhealthy (alert)

**What it is.** The same checks as [hosts.agent-unhealthy](agent-unhealthy.md), started by an Azure Monitor alert instead of a schedule: an alert rule whose name matches `avd-*-agent-health*` (for example a log search alert on `WVDAgentHealthStatus`) fires, its action group hands the alert ID to the alert path, and the alert job asks the agent API to run this runbook (core-and-adapters.md WP6). The alert itself is never trusted: the job reads it back from Azure Monitor and uses only the rule's name to pick runbooks.

**Clear, finding, could-not-run** as in the scheduled runbook. A clear run never reaches a model; missing data is never clear.

**Provenance.** Findings carry `threadStartedBy: alert`. Nobody in avd-buddy asked, so the gate refuses a plan whose only evidence is this run (V1-4).

**Privacy.** As the scheduled runbook: host names stay in the plan store and avd-buddy, never in the job's log (R17).
