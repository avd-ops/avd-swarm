---
id: intune.policy-drift
version: 1
title: Intune platform assignment filters and unfiltered All-Devices policies
trigger:
  manual: true
  runMode: read-only
agent: intune
reads:
  - id: filters
    tool: intune_assignment_filter_list
  - id: policies
    tool: intune_configuration_policy_list
expect:
  - fact: assignmentFilters
    from: filters
    measure: rows
    op: ge
    value: 2
    onFail: report
  - fact: unfilteredAllDevicesPolicies
    from: policies
    measure: sum:unfilteredAllDevices
    op: eq
    value: 0
    onFail: investigate
evidence:
  - c11.intune.multiSessionFilterPresent
  - c11.intune.cloudPcFilterPresent
  - c11.intune.unfilteredAllDevicesPolicies
cites:
  - avd-ops/avd-second-brain@1454728f32dc9db0678274e9faa6227534304e68:docs/knowledge/operations/README.md#desktop-devops-avd-windows-365-and-intune
budget:
  tokensUsd: 0.40
---
# Intune platform assignment filters and unfiltered All-Devices policies

Checks that Microsoft Intune separates AVD multi-session hosts (`Windows 10/11 Enterprise multi-session`) and
Windows 365 Cloud PCs (`device.model -startsWith "Cloud PC"`) with platform assignment filters, and that no
workstation-only configuration policy is assigned to All Devices without an include or exclude filter.

## When this is a finding

- **`assignmentFilters < 2` (`report`):** the tenant is missing one or both platform assignment filters
  (`avd-multisession`, `win365-cloudpc`) from `avd-intune`. Reported without calling a model.
- **`unfilteredAllDevicesPolicies > 0` (`investigate`):** at least one configuration policy targets All Devices
  with no assignment filter, risking policy bleed onto AVD multi-session hosts or Cloud PCs. The Intune agent
  summarizes which policy categories need an assignment filter in an `avd-intune` policy-as-code pull request.
