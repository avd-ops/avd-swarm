# avd-swarm S1a spike: token counts per model deployment. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Q6 evidence: token totals per Claude deployment from the Foundry resource's Azure Monitor metrics.
Uses every metric whose name mentions tokens, so a renamed metric shows up instead of reading zero.
Metrics lag a few minutes behind the calls.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $FoundryName,
    [int] $Hours = 8
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Spike.psm1') -Force

$id = Invoke-SpikeAz -Step 'foundry-id' -Arguments @('cognitiveservices', 'account', 'show', '-g', $SpikeResourceGroup, '-n', $FoundryName, '--query', 'id')
Write-SpikeStep 'Listing token metrics on the Foundry resource'
$names = @(Invoke-SpikeAz -Step 'metric-definitions' -Arguments @('monitor', 'metrics', 'list-definitions', '--resource', $id,
        '--query', "[?contains(name.value, 'Token')].name.value"))
if (@($names | Where-Object { $_ }).Count -eq 0) { throw 'No token metrics on the Foundry resource.' }
Write-Host "Metrics: $($names -join ', ')"

$start = [datetime]::UtcNow.AddHours(-$Hours).ToString('yyyy-MM-ddTHH:mm:ssZ')
$rows = foreach ($name in $names) {
    $result = Invoke-SpikeAz -Step "metric-$name" -Arguments @('monitor', 'metrics', 'list', '--resource', $id, '--metric', $name,
        '--dimension', 'ModelDeploymentName', '--aggregation', 'Total', '--interval', 'PT1H', '--start-time', $start)
    foreach ($series in @($result.value[0].timeseries)) {
        $deployment = @($series.metadatavalues | Where-Object { $_.name.value -eq 'modeldeploymentname' })[0].value
        [pscustomobject]@{
            metric     = $name
            deployment = $deployment
            total      = ($series.data | Measure-Object -Property total -Sum).Sum
        }
    }
}
$rows | Format-Table -AutoSize | Out-String | Write-Host
[pscustomobject]@{ step = 'spike-tokens'; since = $start; rows = @($rows) } | ConvertTo-Json -Depth 5 -Compress
