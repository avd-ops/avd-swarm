# avd-swarm S1a spike: create the resource group, deploy, and deploy the models. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Steps 2 to 4 of the spike runbook: resource group and same-day policy exemption, the Bicep
deployment, your Foundry roles, and the Claude deployments (coordinator, Hosts, and the extra
models the Hosts agent is compared on). Prints one JSON line with
everything the later steps need.

.DESCRIPTION
Run it after spend/spend_guard.py open has opened the run, with that run's ID. Every write is to the
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
    [string] $HostsVersion,
    # Also deployed so Q6 can run the Hosts agent on each model. The coordinator's model is reused, not deployed twice.
    [string[]] $CompareModels = @('claude-sonnet-5-5'),
    # Foundry sends these to Anthropic with every Claude deployment (modelProviderData). Empty means the tenant's
    # own organization name and country from Graph.
    [string] $OrganizationName,
    [ValidatePattern('^([A-Z]{2})?$')][string] $CountryCode,
    [ValidateSet('technology', 'finance', 'healthcare', 'education', 'retail', 'manufacturing', 'government', 'media', 'other')]
    [string] $Industry = 'technology',
    # Thousands of tokens per minute per deployment. 1 is too small: one agent call with tool results exceeds it.
    [ValidateRange(1, 1000)][int] $Capacity = 50,
    # Anthropic: the fallback without Foundry (Anthropic's API from a Container App, Entra Agent ID through Graph).
    # No API key: the agents app calls Claude with Anthropic workload identity federation (its managed identity).
    [ValidateSet('Foundry', 'Anthropic')][string] $Provider = 'Foundry'
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
if ($assignment -and $Location -eq $ResourceGroupLocation) {
    Write-Host "Everything deploys in $Location, which the policy allows; no exemption needed."
}
elseif ($assignment) {
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

# ---- MCP server's Entra app (Graph, through the Azure CLI) ----
# Registered here rather than with the Bicep Graph extension, which a real run denied in Cloud Shell
# (Authorization_RequestDenied for a Global Administrator, 2026-10-18).
$appUniqueName = "avdswarm-spike-mcp-$RunId"
$azureCliAppId = '04b07795-8ddb-461a-bbee-02f9e1bf7b46'  # Microsoft Azure CLI (first-party): lets you list tools as yourself
Write-SpikeStep "Registering the MCP server's Entra app $appUniqueName"
$app = @((Invoke-SpikeGraph -Step 'find-app' -Method get -Path "/applications?`$filter=uniqueName eq '$appUniqueName'").value)[0]
if (-not $app) {
    $roleId = [guid]::NewGuid().Guid
    $scopeId = [guid]::NewGuid().Guid
    if ($PSCmdlet.ShouldProcess($appUniqueName, 'create Entra app')) {
        $app = Invoke-SpikeGraph -Step 'create-app' -Method post -Path '/applications' -Body @{
            uniqueName     = $appUniqueName
            displayName    = "avdswarm spike Azure MCP Server ($RunId)"
            signInAudience = 'AzureADMyOrg'
            appRoles       = @(@{
                    id = $roleId; value = 'Mcp.Tools.ReadWrite.All'; displayName = 'Azure MCP tools'; isEnabled = $true
                    description = 'Call the spike Azure MCP Server (read-only tools only)'; allowedMemberTypes = @('Application')
                })
            api            = @{
                requestedAccessTokenVersion = 2
                oauth2PermissionScopes      = @(@{
                        id = $scopeId; value = 'Mcp.Tools.ReadWrite'; type = 'User'; isEnabled = $true
                        adminConsentDisplayName = 'Azure MCP tools'; adminConsentDescription = 'Call the spike Azure MCP Server as the signed-in user'
                        userConsentDisplayName = 'Azure MCP tools'; userConsentDescription = 'Call the spike Azure MCP Server as you'
                    })
            }
        }
    }
}
$appRoleId = @($app.appRoles | Where-Object { $_.value -eq 'Mcp.Tools.ReadWrite.All' })[0].id
$scope = @($app.api.oauth2PermissionScopes | Where-Object { $_.value -eq 'Mcp.Tools.ReadWrite' })[0]
if ($PSCmdlet.ShouldProcess($appUniqueName, 'set identifier URI and pre-authorize the Azure CLI')) {
    $null = Invoke-SpikeGraph -Step 'update-app' -Method patch -Path "/applications/$($app.id)" -Body @{
        identifierUris = @("api://$($app.appId)")
        api            = @{
            requestedAccessTokenVersion = 2
            oauth2PermissionScopes      = @($app.api.oauth2PermissionScopes)
            preAuthorizedApplications   = @(@{ appId = $azureCliAppId; delegatedPermissionIds = @($scope.id) })
        }
    }
}
$appSp = @((Invoke-SpikeGraph -Step 'find-sp' -Method get -Path "/servicePrincipals?`$filter=appId eq '$($app.appId)'").value)[0]
if (-not $appSp -and $PSCmdlet.ShouldProcess($app.appId, 'create service principal')) {
    $appSp = Invoke-SpikeGraph -Step 'create-sp' -Method post -Path '/servicePrincipals' -Body @{ appId = $app.appId }
}
Write-Host "MCP app $($app.appId), service principal $($appSp.id)"

# ---- Fallback: Anthropic's API, and Entra Agent ID without Foundry ----
if ($Provider -eq 'Anthropic') {
    # The audience Entra issues Claude API tokens for (Anthropic workload identity federation, no API key).
    # v2.0 tokens, identifier URI api://<appId>, and a service principal so the audience resolves in the tenant.
    $claudeUniqueName = "avdswarm-spike-claude-$RunId"
    Write-SpikeStep "Registering the Claude API audience app $claudeUniqueName (workload identity federation)"
    $claudeApp = @((Invoke-SpikeGraph -Step 'find-claude-app' -Method get -Path "/applications?`$filter=uniqueName eq '$claudeUniqueName'").value | Where-Object { $_ })[0]
    if (-not $claudeApp -and $PSCmdlet.ShouldProcess($claudeUniqueName, 'create Entra app')) {
        $claudeApp = Invoke-SpikeGraph -Step 'create-claude-app' -Method post -Path '/applications' -Body @{
            uniqueName = $claudeUniqueName; displayName = "avdswarm spike Claude API audience ($RunId)"; signInAudience = 'AzureADMyOrg'
            api = @{ requestedAccessTokenVersion = 2 }
        }
    }
    if ($PSCmdlet.ShouldProcess($claudeUniqueName, 'set identifier URI')) {
        $null = Invoke-SpikeGraph -Step 'update-claude-app' -Method patch -Path "/applications/$($claudeApp.id)" -Body @{ identifierUris = @("api://$($claudeApp.appId)") }
    }
    $claudeSp = @((Invoke-SpikeGraph -Step 'find-claude-sp' -Method get -Path "/servicePrincipals?`$filter=appId eq '$($claudeApp.appId)'").value | Where-Object { $_ })[0]
    if (-not $claudeSp -and $PSCmdlet.ShouldProcess($claudeApp.appId, 'create service principal')) {
        $null = Invoke-SpikeGraph -Step 'create-claude-sp' -Method post -Path '/servicePrincipals' -Body @{ appId = $claudeApp.appId }
    }
    Write-Host "Claude API audience app $($claudeApp.appId)"

    Connect-SpikeAgentGraph -TenantId $context.TenantId

    # The blueprint: an app registration of the agent identity blueprint type, with you as sponsor and owner.
    $blueprintName = "avdswarm-spike-agents-$RunId"
    Write-SpikeStep "Creating the agent identity blueprint $blueprintName"
    $blueprint = @((Invoke-SpikeAgentGraph -Step 'find-blueprint' -Method GET -Path "/v1.0/applications?`$filter=displayName eq '$blueprintName'").value)[0]
    if (-not $blueprint -and $PSCmdlet.ShouldProcess($blueprintName, 'create agent identity blueprint')) {
        $me = "https://graph.microsoft.com/v1.0/users/$($context.UserId)"
        $blueprint = Invoke-SpikeAgentGraph -Step 'create-blueprint' -Method POST -Path '/v1.0/applications/' -Body @{
            '@odata.type' = 'Microsoft.Graph.AgentIdentityBlueprint'; displayName = $blueprintName
            'sponsors@odata.bind' = @($me); 'owners@odata.bind' = @($me)
        }
    }
    $blueprintSp = @((Invoke-SpikeAgentGraph -Step 'find-blueprint-principal' -Method GET -Path "/v1.0/servicePrincipals?`$filter=appId eq '$($blueprint.appId)'").value)[0]
    if (-not $blueprintSp -and $PSCmdlet.ShouldProcess($blueprint.appId, 'create blueprint principal')) {
        $blueprintSp = Invoke-SpikeAgentGraph -Step 'create-blueprint-principal' -Method POST `
            -Path '/v1.0/serviceprincipals/microsoft.graph.agentIdentityBlueprintPrincipal' -Body @{ appId = $blueprint.appId }
    }
    Write-Host "Blueprint $($blueprint.appId), principal $($blueprintSp.id)"

    # Blueprints create agent identities with AgentIdentity.CreateAsManager. Entra stops granting it automatically in
    # November 2026 (message center MC1506728), so grant it explicitly; until then a failure here only warns.
    Write-SpikeStep 'Granting the blueprint AgentIdentity.CreateAsManager on Microsoft Graph'
    $graphSp = @((Invoke-SpikeAgentGraph -Step 'find-graph' -Method GET -Path "/v1.0/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'&`$select=id,appRoles").value)[0]
    $createRole = @($graphSp.appRoles | Where-Object { $_.value -eq 'AgentIdentity.CreateAsManager' })[0]
    if (-not $createRole) { Write-Warning 'Microsoft Graph has no AgentIdentity.CreateAsManager app role here yet; relying on the automatic grant.' }
    elseif ($PSCmdlet.ShouldProcess($blueprintSp.id, 'grant AgentIdentity.CreateAsManager')) {
        try {
            $null = Invoke-SpikeAgentGraph -Step 'grant-create-as-manager' -Method POST -Path "/v1.0/servicePrincipals/$($blueprintSp.id)/appRoleAssignments" `
                -Body @{ principalId = $blueprintSp.id; resourceId = $graphSp.id; appRoleId = $createRole.id }
        }
        catch { Write-Warning "Grant failed; relying on the automatic grant until November 2026. $($_.Exception.Message)" }
    }

    # The agent code comes from the pushed commit; the container installs and runs it.
    $commit = git -C $PSScriptRoot rev-parse HEAD
    $agentCodeUrl = "https://raw.githubusercontent.com/nickprignano/avd-swarm/$commit/spikes/s1a/agent_api"
    Write-SpikeStep "Deploying infra/main.bicep (MCP server, agents Container App, logs, empty host pool) with agent code at $commit; this takes several minutes"
    $deployment = Invoke-SpikeAz -Step 'deploy' -Arguments @('deployment', 'group', 'create', '-g', $SpikeResourceGroup,
        '-n', "avdswarm-spike-$RunId", '-f', (Join-Path $PSScriptRoot '..' 'infra' 'main.bicep'),
        '-p', "location=$Location", "runId=$RunId", "mcpAppId=$($app.appId)", 'provider=anthropic', "blueprintAppId=$($blueprint.appId)",
        "operatorObjectId=$($context.UserId)", "agentCodeUrl=$agentCodeUrl", "claudeAudienceAppId=$($claudeApp.appId)")
    $out = $deployment.properties.outputs

    Write-SpikeStep "Making the agents' managed identity the blueprint's credential (federated identity credential)"
    $fics = @((Invoke-SpikeAgentGraph -Step 'list-fic' -Method GET -Path "/v1.0/applications/$($blueprint.id)/federatedIdentityCredentials").value)
    if (-not ($fics | Where-Object { $_.subject -eq $out.agentsIdentityPrincipalId.value }) -and $PSCmdlet.ShouldProcess($blueprint.appId, 'add federated identity credential')) {
        $null = Invoke-SpikeAgentGraph -Step 'add-fic' -Method POST -Path "/v1.0/applications/$($blueprint.id)/federatedIdentityCredentials" -Body @{
            name = 'agents-managed-identity'; issuer = "https://login.microsoftonline.com/$($context.TenantId)/v2.0"
            subject = $out.agentsIdentityPrincipalId.value; audiences = @('api://AzureADTokenExchange')
        }
    }

    $agentsUrl = $out.agentsUrl.value
    Write-SpikeStep "Waiting for the agents app to install and start at $agentsUrl (up to 10 minutes)"
    $healthy = $false
    for ($i = 0; $i -lt 60 -and -not $healthy; $i++) {
        try { $healthy = (Invoke-RestMethod -Uri "$agentsUrl/healthz" -TimeoutSec 10).ok -eq $true } catch { Start-Sleep -Seconds 10 }
    }
    if (-not $healthy) { throw "The agents app didn't answer $agentsUrl/healthz in 10 minutes. Its logs: az containerapp logs show -g $SpikeResourceGroup -n $($out.agentsAppName.value) --tail 100" }

    # The blueprint creates the agent identity itself (its own token, from the managed identity); a new FIC can take a minute to work.
    $identityName = "avdswarm-spike-hosts-$RunId"
    Write-SpikeStep "Asking the blueprint to create the agent identity $identityName"
    $operatorToken = az account get-access-token --scope "api://$($app.appId)/Mcp.Tools.ReadWrite" --query accessToken -o tsv
    $identity = @((Invoke-SpikeAgentGraph -Step 'find-agent-identity' -Method GET -Path "/v1.0/servicePrincipals?`$filter=displayName eq '$identityName'").value)[0]
    for ($i = 0; $i -lt 6 -and -not $identity; $i++) {
        $response = Invoke-WebRequest -Uri "$agentsUrl/setup" -Method Post -SkipHttpErrorCheck -ContentType 'application/json' `
            -Headers @{ Authorization = "Bearer $operatorToken" } -Body (@{ displayName = $identityName } | ConvertTo-Json -Compress)
        if ($response.StatusCode -eq 200) { $identity = $response.Content | ConvertFrom-Json; break }
        Write-Host "  /setup returned $($response.StatusCode): $($response.Content)"
        Start-Sleep -Seconds 20
    }
    if (-not $identity) { throw 'The blueprint could not create the agent identity (errors above). This is a Q2 finding; the spike tears down next.' }
    Write-Host "Agent identity $($identity.appId), object $($identity.id)"
    if ($identity.blueprintTokenClaims) { Write-Host "Blueprint token claims: $($identity.blueprintTokenClaims | ConvertTo-Json -Compress)" }

    [pscustomobject]@{
        step                  = 'start-spike'
        provider              = 'anthropic'
        runId                 = $RunId
        resourceGroup         = $SpikeResourceGroup
        mcpUrl                = $out.mcpUrl.value
        mcpAppId              = $out.mcpAppId.value
        mcpAppSpId            = $appSp.id
        mcpAppRoleId          = $appRoleId
        agentsUrl             = $agentsUrl
        agentsAppName         = $out.agentsAppName.value
        agentsIdentityPrincipalId = $out.agentsIdentityPrincipalId.value
        claudeAudienceAppId   = $claudeApp.appId
        tenantId              = $context.TenantId
        blueprintAppId        = $blueprint.appId
        agentIdentityId       = $identity.id
        agentIdentityAppId    = $identity.appId
        coordinatorDeployment = $CoordinatorModel
        hostsDeployment       = $HostsModel
        compareDeployments    = @(@($HostsModel) + $CompareModels + @($CoordinatorModel) | Select-Object -Unique)
    } | ConvertTo-Json -Compress
    return
}

# ---- Step 3: deploy ----
Write-SpikeStep 'Deploying infra/main.bicep (Foundry, MCP server, logs, empty host pool); this takes several minutes'
$deployment = Invoke-SpikeAz -Step 'deploy' -Arguments @('deployment', 'group', 'create', '-g', $SpikeResourceGroup,
    '-n', "avdswarm-spike-$RunId", '-f', (Join-Path $PSScriptRoot '..' 'infra' 'main.bicep'),
    '-p', "location=$Location", "runId=$RunId", "mcpAppId=$($app.appId)")
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
if (-not $OrganizationName -or -not $CountryCode) {
    $org = @((Invoke-SpikeGraph -Step 'read-organization' -Method get -Path '/organization?$select=displayName,countryLetterCode').value)[0]
    if (-not $OrganizationName) { $OrganizationName = $org.displayName }
    if (-not $CountryCode) { $CountryCode = $org.countryLetterCode }
}
if (-not $OrganizationName -or -not $CountryCode) { throw 'Claude deployments need an organization name and country; pass -OrganizationName and -CountryCode.' }
Write-Host "Claude deployments register organization '$OrganizationName', country $CountryCode, industry $Industry (Foundry passes these to Anthropic)"
$picks = @(@{ Model = $CoordinatorModel; Version = $CoordinatorVersion }, @{ Model = $HostsModel; Version = $HostsVersion }) +
    @($CompareModels | Where-Object { $_ -notin @($CoordinatorModel, $HostsModel) } | ForEach-Object { @{ Model = $_; Version = $null } })
foreach ($pick in $picks) {
    $candidates = @($models | Where-Object { $_.name -eq $pick.Model -and (-not $pick.Version -or $_.version -eq $pick.Version) })
    if (-not $pick.Version -and $candidates.Count -gt 1) {
        # Foundry offers several versions of one model (for example 1 and 2); without a pinned version take the newest.
        $candidates = @($candidates | Sort-Object { [long]($_.version -replace '\D', '') } -Descending | Select-Object -First 1)
        Write-Host "$($pick.Model): several versions offered, using the newest ($($candidates[0].version))"
    }
    if ($candidates.Count -ne 1) {
        $similar = $models | Where-Object { $_.name -match 'claude' } | ForEach-Object { "$($_.name) $($_.version) ($($_.format))" }
        throw "Model $($pick.Model): $($candidates.Count) matches. Claude models offered here: $(@($similar) -join '; ')`nIf none are offered, Q1 is answered: this subscription can't deploy Claude in Foundry (README, 'If Q1 fails')."
    }
    $model = $candidates[0]
    Write-SpikeStep "Deploying $($model.name) $($model.version) ($($model.format)), Global Standard"
    # REST, because az cognitiveservices account deployment create can't pass modelProviderData.
    $deploymentUrl = "https://management.azure.com$rgId/providers/Microsoft.CognitiveServices/accounts/$foundryName/deployments/$($model.name)?api-version=2025-10-01-preview"
    $bodyFile = New-TemporaryFile
    try {
        Set-Content -LiteralPath $bodyFile.FullName -Value (@{
            sku        = @{ name = 'GlobalStandard'; capacity = $Capacity }
            properties = @{
                model             = @{ format = $model.format; name = $model.name; version = $model.version }
                modelProviderData = @{ organizationName = $OrganizationName; countryCode = $CountryCode; industry = $Industry }
            }
        } | ConvertTo-Json -Depth 10 -Compress)
        $null = Invoke-SpikeAz -Step "deploy-$($model.name)" -Arguments @('rest', '--method', 'put', '--url', $deploymentUrl,
            '--body', "@$($bodyFile.FullName)", '--headers', 'Content-Type=application/json')
    }
    finally { Remove-Item -LiteralPath $bodyFile.FullName -ErrorAction SilentlyContinue }
    # The PUT returns while the deployment is still Creating; wait for it, at most 10 minutes.
    $state = $null
    for ($i = 0; $i -lt 60; $i++) {
        $state = (Invoke-SpikeAz -Step "check-$($model.name)" -Arguments @('rest', '--method', 'get', '--url', $deploymentUrl)).properties.provisioningState
        if ($state -in @('Succeeded', 'Failed', 'Canceled')) { break }
        Start-Sleep -Seconds 10
    }
    if ($state -ne 'Succeeded') { throw "Deployment $($model.name) ended in state '$state'. Check it in the Foundry portal; the spike tears down next." }
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
    mcpAppSpId            = $appSp.id
    mcpAppRoleId          = $appRoleId
    coordinatorDeployment = $deployed[$CoordinatorModel]
    hostsDeployment       = $deployed[$HostsModel]
    # Every model the Hosts agent runs on in Q6: the main one first, then the comparisons and the coordinator's model.
    compareDeployments    = @(@($HostsModel) + $CompareModels + @($CoordinatorModel) | Select-Object -Unique | ForEach-Object { $deployed[$_] })
} | ConvertTo-Json -Compress
