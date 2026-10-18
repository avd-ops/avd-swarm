# avd-swarm S1a spike: tear everything down and prove it. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Deletes the spike resource group, purges the soft-deleted Foundry resource, deletes the MCP
server's Entra app, and checks that nothing is left, including the policy exemption, which the
spend ledger's resource check can't see. Run tenant-swarm's Stop-SpendRun.ps1 after it.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string] $RunId,
    [string] $Location = 'eastus2',
    [string] $McpAppId
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Spike.psm1') -Force

$context = Get-SpikeContext
$rgId = "/subscriptions/$($context.SubscriptionId)/resourceGroups/$SpikeResourceGroup"
$leftovers = [System.Collections.Generic.List[string]]::new()

$group = Invoke-SpikeAz -Step 'get-rg' -AllowNotFound -Arguments @('group', 'show', '-n', $SpikeResourceGroup)
$foundryNames = @()
if ($group) {
    $foundryNames = @(Invoke-SpikeAz -Step 'list-foundry' -Arguments @('cognitiveservices', 'account', 'list', '-g', $SpikeResourceGroup, '--query', '[].name'))
    if (-not $McpAppId) {
        $last = @(Invoke-SpikeAz -Step 'list-deployments' -Arguments @('deployment', 'group', 'list', '-g', $SpikeResourceGroup,
                '--query', "[?name=='avdswarm-spike-$RunId'].properties.outputs.mcpAppId.value"))
        $McpAppId = @($last | Where-Object { $_ })[0]
    }
    Write-SpikeStep "Deleting resource group $SpikeResourceGroup; this takes several minutes"
    if ($PSCmdlet.ShouldProcess($SpikeResourceGroup, 'delete resource group')) {
        $null = Invoke-SpikeAz -Step 'delete-rg' -Arguments @('group', 'delete', '-n', $SpikeResourceGroup, '--yes')
    }
}
else {
    Write-Host "Resource group $SpikeResourceGroup is already gone."
}

foreach ($name in @($foundryNames | Where-Object { $_ })) {
    Write-SpikeStep "Purging soft-deleted Foundry resource $name"
    if ($PSCmdlet.ShouldProcess($name, 'purge Foundry resource')) {
        $null = Invoke-SpikeAz -Step 'purge-foundry' -AllowNotFound -Arguments @('cognitiveservices', 'account', 'purge',
            '-l', $Location, '-g', $SpikeResourceGroup, '-n', $name)
    }
}

if ($McpAppId) {
    Write-SpikeStep "Deleting the MCP server's Entra app $McpAppId"
    if ($PSCmdlet.ShouldProcess($McpAppId, 'delete Entra app')) {
        $null = Invoke-SpikeAz -Step 'delete-app' -AllowNotFound -Arguments @('ad', 'app', 'delete', '--id', $McpAppId)
    }
    if (Invoke-SpikeAz -Step 'check-app' -AllowNotFound -Arguments @('ad', 'app', 'show', '--id', $McpAppId)) { $leftovers.Add("Entra app $McpAppId") }
}
else {
    Write-Warning 'No MCP app ID found; check Entra app registrations for "avdswarm spike Azure MCP Server" by hand.'
}

Write-SpikeStep 'Checking what is left'
if (Invoke-SpikeAz -Step 'check-rg' -AllowNotFound -Arguments @('group', 'show', '-n', $SpikeResourceGroup)) { $leftovers.Add("resource group $SpikeResourceGroup") }
$exemption = Invoke-SpikeAz -Step 'check-exemption' -AllowNotFound -Arguments @('rest', '--method', 'get', '--url',
    "https://management.azure.com$rgId/providers/Microsoft.Authorization/policyExemptions/avdswarm-spike-$($RunId)?api-version=2022-07-01-preview")
if ($exemption) { $leftovers.Add("policy exemption avdswarm-spike-$RunId") }
$deleted = @(Invoke-SpikeAz -Step 'check-soft-deleted' -Arguments @('cognitiveservices', 'account', 'list-deleted', '--query', "[?contains(name, 'avdswarm-spike')].name"))
foreach ($name in @($deleted | Where-Object { $_ })) { $leftovers.Add("soft-deleted Foundry resource $name") }

[pscustomobject]@{ step = 'remove-spike'; runId = $RunId; leftovers = @($leftovers) } | ConvertTo-Json -Compress
if ($leftovers.Count -gt 0) {
    Write-Host "RESULT teardown FAIL: $($leftovers -join '; ')"
    exit 1
}
Write-Host 'RESULT teardown PASS. Also delete the report-only Conditional Access policy from step 8 if you made one. Next: Stop-SpendRun.ps1.'
