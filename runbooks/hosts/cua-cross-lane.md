---
id: hosts.cua-cross-lane
version: 1
title: Cross-lane CUA and AVD sign-in posture
trigger:
  schedule: "45 6 * * 1-5"  # 06:45 UTC on weekdays
  runMode: read-only
agent: hosts
reads:
  - id: crosslane
    kql: wvd-cua-cross-lane-signins-24h
expect:
  - fact: cuaCrossLaneConnections
    from: crosslane
    measure: sum:Connections
    op: lt
    value: 1000
    onFail: investigate
evidence: [c11.hostPool.count]
cites:
  - avd-ops/avd-ops@61e92e650fa2937d213b54a6c97fe9b1830e6036:docs/cua-executor-spec.md
  - https://learn.microsoft.com/entra/identity/conditional-access/overview
budget: {tokensUsd: 0.50}
---
# Cross-lane CUA and AVD sign-in posture

**What it checks.** Summarizes `WVDConnections` joined with `SigninLogs` Entra Agent ID signals (`Agent.agentType`) over the last 24 hours (`wvd-cua-cross-lane-signins-24h`) across host pools and client types.

**Clear** when connection volume is within expected bounds and no unexpected cross-lane agent sign-in appears on human host pools.

**When it isn't clear,** the Hosts agent inspects the summarized counts by `HostPoolName`, `ClientType`, `IsAgent`, and `AgentType` to flag agent identities connecting to human pools or unexpected client shifts.

**When it can't run,** the outcome is `could-not-run`, never `clear`.

**Privacy.** The query returns counts only. No user name or IP address leaves the workspace.
