---
id: hosts.connection-errors
version: 1
title: Failed connections and sign-ins
trigger:
  schedule: "15 6 * * 1-5"  # 06:15 UTC on weekdays
  runMode: read-only
agent: hosts
reads:
  - id: errors
    kql: wvd-connection-errors-last-24h
expect:
  - fact: connectionErrors
    from: errors
    measure: sum:Errors
    op: lt
    value: 25
    onFail: investigate
evidence: [c11.hostPool.count]
cites:
  - avd-ops/avd-second-brain@f59a1c5207f89ba71f4752fe2a52a091ca6fd376:docs/knowledge/operations/README.md#diagnosing
  - https://learn.microsoft.com/azure/virtual-desktop/troubleshoot-azure-monitor
budget: {tokensUsd: 0.50}
---
# Failed connections and sign-ins

**What it checks.** How many errors AVD recorded in the last 24 hours (`WVDErrors`: connection, feed and management activity), counted by activity, source and error code. Users who can't sign in or connect show up here before anyone opens a ticket.

**Clear** when there are fewer than 25 errors in the day. The threshold is a starting point for one small estate, not a standard: tune it from this runbook's own history (an adopter's private repo changes it with a new `version`), never from a model's opinion.

**When it isn't clear,** the Hosts agent gets the counts by code and answers with citations: which codes dominate, whether they are service errors (`ServiceError` true: Microsoft's side, check Service Health) or customer errors, and which activity they came from. It never asks for user names; a question about one user is the Users agent's, after v1.

**When it can't run,** the outcome is `could-not-run`, never `clear`.

**Privacy.** The query returns counts only. No user name, client IP address or host name leaves the workspace.
