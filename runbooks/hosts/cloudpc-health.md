---
id: hosts.cloudpc-health
version: 1
title: Windows 365 Cloud PC Azure Network Connection and provisioning posture
trigger:
  manual: true
  runMode: read-only
agent: hosts
reads:
  - id: ancs
    tool: cloudpc_anc_list
  - id: policies
    tool: cloudpc_provisioning_policy_list
expect:
  - fact: failedAncChecks
    from: ancs
    measure: sum:failedChecks
    op: eq
    value: 0
    onFail: investigate
  - fact: localAdminEnabledPolicies
    from: policies
    measure: sum:localAdminEnabled
    op: eq
    value: 0
    onFail: report
evidence:
  - c11.intune.win365.ancFailedCount
  - c11.intune.win365.ssoEnabledOnAllPolicies
  - c11.intune.win365.localAdminDisabled
cites:
  - avd-ops/avd-second-brain@1454728f32dc9db0678274e9faa6227534304e68:docs/knowledge/operations/README.md#desktop-devops-avd-windows-365-and-intune
budget:
  tokensUsd: 0.40
---
# Windows 365 Cloud PC Azure Network Connection and provisioning posture

Checks Windows 365 Cloud PC Azure Network Connection (ANC) health checks and provisioning policy security
posture (`singleSignOn` and local administrator user settings).

## When this is a finding

- **`failedAncChecks > 0` (`investigate`):** an Azure Network Connection failed its DNS, subnet or domain-join
  health check, blocking Cloud PC provisioning. The Hosts agent correlates the failed ANC check with provisioning
  policy assignments.
- **`localAdminEnabledPolicies > 0` (`report`):** a Cloud PC user setting grants persistent local administrator
  rights instead of Entra PIM or Endpoint Privilege Management. Reported without calling a model.
