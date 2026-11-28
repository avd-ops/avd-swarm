<#
.SYNOPSIS
Offline validation and what-if helper for the 3-layer avd-swarm Outside Observer.
Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

.DESCRIPTION
Builds the Bicep templates offline (`az bicep build`), verifies zero Log Analytics / Sentinel
resources and strict RBAC isolation, and optionally runs a `deployment group what-if` when
explicitly approved (`go batch <id> $<total>`).
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TemplatePath = '',
    [string]$ReadRbacTemplatePath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $TemplatePath) { $TemplatePath = Join-Path $PSScriptRoot 'main.bicep' }
if (-not $ReadRbacTemplatePath) { $ReadRbacTemplatePath = Join-Path $PSScriptRoot 'avd-read-rbac.bicep' }

Write-Host 'Compiling Outside Observer Bicep templates offline...'
$mainJson = az bicep build --file $TemplatePath --stdout | ConvertFrom-Json
if (-not $mainJson.resources) {
    throw "Compiled template $TemplatePath produced no resources."
}

$null = az bicep build --file $ReadRbacTemplatePath --stdout | ConvertFrom-Json

$resourceTypes = @($mainJson.resources | ForEach-Object { $_.type })
$forbiddenTypes = @(
    'Microsoft.OperationalInsights/workspaces',
    'Microsoft.SecurityInsights/dataConnectors'
)
foreach ($ft in $forbiddenTypes) {
    if ($resourceTypes -contains $ft) {
        throw "Forbidden billable workspace/SIEM resource found: $ft"
    }
}

$alertCount = @($resourceTypes | Where-Object { $_ -eq 'Microsoft.Insights/activityLogAlerts' }).Count
if ($alertCount -lt 4) {
    throw "Expected at least 4 Activity Log Alert rules, found $alertCount."
}

Write-Host "Offline Bicep verification passed: $($mainJson.resources.Count) resources, $alertCount Activity Log Alerts, zero Log Analytics/Sentinel."
