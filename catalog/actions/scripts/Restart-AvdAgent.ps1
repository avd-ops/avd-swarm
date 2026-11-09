#Requires -Version 5.1
# avd-swarm fixed Run Command script: restart the AVD Agent BootLoader service on a drained, empty session host.
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
# Fixed file with no parameters (spec section 5a, brain H6): pinned by SHA-256 in catalog/actions/host.restart-agent.yml.
<#
.SYNOPSIS
Restarts the RDAgentBootLoader service on a drained, empty session host through the fixed Run Command action (host.restart-agent) and verifies that the service reaches the Running state.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$serviceName = 'RDAgentBootLoader'
$svc = Get-Service -Name $serviceName -ErrorAction Stop
Restart-Service -Name $serviceName -Force -ErrorAction Stop
$after = Get-Service -Name $serviceName -ErrorAction Stop
if ($after.Status -ne 'Running') {
    throw "Service $serviceName did not reach Running state (status: $($after.Status))."
}
[pscustomobject]@{
    service        = $serviceName
    previousStatus = [string]$svc.Status
    currentStatus  = [string]$after.Status
} | ConvertTo-Json -Compress
