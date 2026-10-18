# avd-swarm S1a spike: create the resource group, deploy, and deploy the models. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Steps 2 to 4 of the spike runbook: resource group and same-day policy exemption, the Bicep
deployment, your Foundry roles, and the two Claude deployments. Prints one JSON line with
everything the later steps need.

.DESCRIPTION
Run it after Start-SpendRun.ps1 has opened the run, with that run's ID. Every write is to the
spike's own resource group, except the policy exemption (scoped to that group), the MCP server's
Entra app registration, and your own role assignments on the Foundry resource.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._-]+$')][string] $RunId,
    [string] $ResourceGroupLocation = 'northcentralus',
    [string] $Location = 'eastus2',
    [string] $CoordinatorModel = 'claude-haiku-5-5',
    [string] $HostsModel = 'claude-opus-5-5',
    [string] $CoordinatorVersion,
    [string] $HostsVersion
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Spike.psm1') -Force

$context = Get-SpikeContext
Write-Host "Subscription: $($context.SubscriptionName) ($($context.SubscriptionId)); you: $($context.UserPrincipal)"
$rgId = "/subscriptions/$($context.SubscriptionId)/resourceGroups/$SpikeResourceGroup"

# ---- Step 2: resource group and exemption ----
Write-SpikeStep "Creating resource group $SpikeResourceGroup in $ResourceGroupLocation"
if ($PSCmdlet.ShouldProcess($SpikeResourceGroup, 'create resource group')) {
    $null = Invoke-SpikeAz -Step 'create-rg' -Arguments @('group', 'create', '-n', $SpikeResourceGroup, '-l', $ResourceGroupLocation,
        '--tags', 'workload=avdswarm-spike', "spend-run=$RunId")
}

Write-SpikeStep "Looking for the landing zone's location policy ($ExemptedAssignment)"
$assignment = Invoke-SpikeAz -Step 'get-assignment' -AllowNotFound -Arguments @('rest', '--method', 'get', '--url',
    "https://management.azure.com/subscriptions/$($context.SubscriptionId)/providers/Microsoft.Authorization/policyAssignments/$($ExemptedAssignment)?api-version=2023-04-01")
if ($assignment) {
    $expires = [datetime]::UtcNow.Date.AddDays(1).ToString('yyyy-MM-ddTHH:mm:ssZ')
    $exemptionName = "avdswarm-spike-$RunId"
    Write-SpikeStep "Exempting $SpikeResourceGroup only from $ExemptedAssignment until $expires"
    $body = @{ properties = @{
            policyAssignmentId = $assignment.id
            exemptionCategory  = 'Waiver'
            expiresOn          = $expires
            displayName        = "avd-swarm S1a spike ($RunId)"
            description        = "Spike resources in $Location for Claude in Foundry. Deleted at teardown; spend run $RunId."
        } } | ConvertTo-Json -Depth 5 -Compress
    $bodyFile = New-TemporaryFile
    Set-Content -LiteralPath $bodyFile.FullName -Value $body
    if ($PSCmdlet.ShouldProcess($exemptionName, 'create policy exemption')) {
        $null = Invoke-SpikeAz -Step 'create-exemption' -Arguments @('rest', '--method', 'put', '--url',
            "https://management.azure.com$rgId/providers/Microsoft.Authorization/policyExemptions/$($exemptionName)?api-version=2022-07-01-preview",
            '--body', "@$($bodyFile.FullName)")
    }
    Remove-Item -LiteralPath $bodyFile.FullName
}
else {
    Write-Host 'No landing zone location policy here; no exemption needed.'
}

# ---- Step 3: deploy ----
Write-SpikeStep 'Deploying infra/main.bicep (Foundry, MCP server, logs, empty host pool); this takes several minutes'
$deployment = Invoke-SpikeAz -Step 'deploy' -Arguments @('deployment', 'group', 'create', '-g', $SpikeResourceGroup,
    '-n', "avdswarm-spike-$RunId", '-f', (Join-Path $PSScriptRoot '..' 'infra' 'main.bicep'),
    '-p', "location=$Location", "runId=$RunId")
$out = $deployment.properties.outputs
$foundryName = $out.foundryName.value
$foundryId = "$rgId/providers/Microsoft.CognitiveServices/accounts/$foundryName"

Write-SpikeStep 'Giving you Foundry User and Foundry Project Manager on the Foundry resource (data plane: call models, create agents)'
foreach ($names in @(@('Foundry User', 'Azure AI User'), @('Foundry Project Manager', 'Azure AI Project Manager'))) {
    $roleId = Get-SpikeRoleId -Names $names
    $null = Invoke-SpikeAz -Step "assign-$($names[0])" -Arguments @('role', 'assignment', 'create', '--assignee-object-id', $context.UserId,
        '--assignee-principal-type', 'User', '--role', $roleId, '--scope', $foundryId)
}

# ---- Step 4: models ----
Write-SpikeStep "Listing the models the Foundry resource offers"
$models = @(Invoke-SpikeAz -Step 'list-models' -Arguments @('cognitiveservices', 'account', 'list-models', '-g', $SpikeResourceGroup, '-n', $foundryName))
$deployed = @{}
foreach ($pick in @(@{ Model = $CoordinatorModel; Version = $CoordinatorVersion }, @{ Model = $HostsModel; Version = $HostsVersion })) {
    $candidates = @($models | Where-Object { $_.name -eq $pick.Model -and (-not $pick.Version -or $_.version -eq $pick.Version) })
    if ($candidates.Count -ne 1) {
        $similar = $models | Where-Object { $_.name -match 'claude' } | ForEach-Object { "$($_.name) $($_.version) ($($_.format))" }
        throw "Model $($pick.Model): $($candidates.Count) matches. Claude models offered here: $(@($similar) -join '; ')`nIf none are offered, Q1 is answered: this subscription can't deploy Claude in Foundry (README, 'If Q1 fails')."
    }
    $model = $candidates[0]
    Write-SpikeStep "Deploying $($model.name) $($model.version) ($($model.format)), Global Standard"
    $null = Invoke-SpikeAz -Step "deploy-$($model.name)" -Arguments @('cognitiveservices', 'account', 'deployment', 'create',
        '-g', $SpikeResourceGroup, '-n', $foundryName, '--deployment-name', $model.name, '--model-name', $model.name,
        '--model-version', $model.version, '--model-format', $model.format, '--sku-name', 'GlobalStandard', '--sku-capacity', '1')
    $deployed[$pick.Model] = $model.name
}

[pscustomobject]@{
    step                  = 'start-spike'
    runId                 = $RunId
    resourceGroup         = $SpikeResourceGroup
    foundryName           = $foundryName
    projectEndpoint       = $out.projectEndpoint.value
    mcpUrl                = $out.mcpUrl.value
    mcpAppId              = $out.mcpAppId.value
    mcpAppSpId            = $out.mcpAppSpId.value
    mcpAppRoleId          = $out.mcpAppRoleId.value
    coordinatorDeployment = $deployed[$CoordinatorModel]
    hostsDeployment       = $deployed[$HostsModel]
} | ConvertTo-Json -Compress
