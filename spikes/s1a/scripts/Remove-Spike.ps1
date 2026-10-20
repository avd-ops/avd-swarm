# avd-swarm S1a spike: tear everything down and prove it. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Deletes the spike resource group, purges the soft-deleted Foundry resource, deletes the MCP
server's Entra app, and checks that nothing is left, including the policy exemption, which a
resource group cost check can't see. Its last line is the teardown evidence for the spend guard's
close command (spend/spend_guard.py).
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string] $RunId,
    [string] $Location = 'eastus2',
    [string] $McpAppId,
    # Anthropic: also delete the fallback's agent identity and blueprint (found by name, through Graph PowerShell).
    [ValidateSet('Foundry', 'Anthropic')][string] $Provider = 'Foundry'
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

if (-not $McpAppId) {
    # Registered by Start-Spike.ps1 with a uniqueName per run, so it can be found after the group is gone.
    $McpAppId = @((Invoke-SpikeGraph -Step 'find-app' -Method get -Path "/applications?`$filter=uniqueName eq 'avdswarm-spike-mcp-$RunId'").value)[0].appId
}
if ($McpAppId) {
    Write-SpikeStep "Deleting the MCP server's Entra app $McpAppId"
    if ($PSCmdlet.ShouldProcess($McpAppId, 'delete Entra app')) {
        $null = Invoke-SpikeAz -Step 'delete-app' -AllowNotFound -Arguments @('ad', 'app', 'delete', '--id', $McpAppId)
    }
    if (Invoke-SpikeAz -Step 'check-app' -AllowNotFound -Arguments @('ad', 'app', 'show', '--id', $McpAppId)) { $leftovers.Add("Entra app $McpAppId") }
}
else {
    Write-Host "No MCP server app is registered for run $RunId."
}

if ($Provider -eq 'Anthropic') {
    $claudeAppId = @((Invoke-SpikeGraph -Step 'find-claude-app' -Method get -Path "/applications?`$filter=uniqueName eq 'avdswarm-spike-claude-$RunId'").value | Where-Object { $_ })[0].appId
    if ($claudeAppId) {
        Write-SpikeStep "Deleting the Claude API audience app $claudeAppId"
        if ($PSCmdlet.ShouldProcess($claudeAppId, 'delete Entra app')) {
            $null = Invoke-SpikeAz -Step 'delete-claude-app' -AllowNotFound -Arguments @('ad', 'app', 'delete', '--id', $claudeAppId)
        }
        if (Invoke-SpikeAz -Step 'check-claude-app' -AllowNotFound -Arguments @('ad', 'app', 'show', '--id', $claudeAppId)) { $leftovers.Add("Entra app $claudeAppId") }
    }
    Connect-SpikeAgentGraph -TenantId $context.TenantId
    $identityName = "avdswarm-spike-hosts-$RunId"
    $blueprintName = "avdswarm-spike-agents-$RunId"
    foreach ($identity in @((Invoke-SpikeAgentGraph -Step 'find-agent-identity' -Method GET -Path "/v1.0/servicePrincipals?`$filter=displayName eq '$identityName'").value | Where-Object { $_ })) {
        Write-SpikeStep "Deleting the agent identity $($identity.appId)"
        if ($PSCmdlet.ShouldProcess($identity.id, 'delete agent identity')) {
            $null = Invoke-SpikeAgentGraph -Step 'delete-agent-identity' -Method DELETE -Path "/beta/serviceprincipals/$($identity.id)" -AllowNotFound
        }
    }
    foreach ($blueprint in @((Invoke-SpikeAgentGraph -Step 'find-blueprint' -Method GET -Path "/v1.0/applications?`$filter=displayName eq '$blueprintName'").value | Where-Object { $_ })) {
        Write-SpikeStep "Deleting the agent identity blueprint $($blueprint.appId) (its principal goes with it)"
        if ($PSCmdlet.ShouldProcess($blueprint.id, 'delete blueprint')) {
            $null = Invoke-SpikeAgentGraph -Step 'delete-blueprint' -Method DELETE -Path "/v1.0/applications/$($blueprint.id)" -AllowNotFound
        }
    }
    if (@((Invoke-SpikeAgentGraph -Step 'check-agent-identity' -Method GET -Path "/v1.0/servicePrincipals?`$filter=displayName eq '$identityName'").value | Where-Object { $_ }).Count) {
        $leftovers.Add("agent identity $identityName")
    }
    if (@((Invoke-SpikeAgentGraph -Step 'check-blueprint' -Method GET -Path "/v1.0/applications?`$filter=displayName eq '$blueprintName'").value | Where-Object { $_ }).Count) {
        $leftovers.Add("agent identity blueprint $blueprintName")
    }
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
Write-Host 'RESULT teardown PASS. Also delete the report-only Conditional Access policy from step 6 if you made one. Next: close the run with spend_guard.py close.'
if ($Provider -eq 'Anthropic') {
    Write-Host "Claude Console > Settings > Workload identity: delete (or disable) the federation rule for run $RunId. Its managed identity and audience app are gone, so it can't match anything, but it shouldn't linger."
}
