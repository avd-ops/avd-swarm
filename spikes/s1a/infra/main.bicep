// avd-swarm S1a spike. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
//
// Everything the spike needs except the model deployments (deployed by name from what the region
// offers, README step 4) and the hosted agent (deployed from code, README step 6).
// provider 'anthropic' is the fallback without Foundry: no Foundry resource; the agents run in a
// Container App that calls Anthropic's API, with a user-assigned managed identity as the credential
// of an Entra agent identity blueprint (Start-Spike.ps1 creates the blueprint through Graph).
// The Azure MCP Server part follows the setup in Azure-Samples/azmcp-foundry-aca-mi, with the
// virtualdesktop namespace and scale to zero.

targetScope = 'resourceGroup'

@description('Region for the Foundry resource and the Container App: one where Claude in Foundry is offered.')
param location string = 'eastus2'

@description('Region for the AVD objects. The landing zone region, so they need no policy exemption.')
param avdLocation string = resourceGroup().location

@description('Spend run ID from spend/spend_guard.py, tagged on every resource (docs/spend.md).')
param runId string

@description('foundry: Claude in Foundry and hosted agents. anthropic: the fallback, Anthropic\'s API from a Container App.')
@allowed(['foundry', 'anthropic'])
param provider string = 'foundry'

@description('Short unique suffix for globally unique names.')
param suffix string = take(uniqueString(resourceGroup().id), 6)

var tags = {
  workload: 'avdswarm-spike'
  'spend-run': runId
}
var desktopVirtualizationReaderRoleId = '49a72310-ab8d-41df-bbb0-79b649203868'
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

// ---------- Foundry ----------
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = if (provider == 'foundry') {
  name: 'avdswarm-spike-${suffix}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'avdswarm-spike-${suffix}'
    allowProjectManagement: true
    disableLocalAuth: true // Entra only: no keys to leak or remember
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (provider == 'foundry') {
  parent: foundry
  name: 'spike'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {}
}

// ---------- Logs (Container Apps and MCP server) ----------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-avdswarm-spike-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.5') } // a hard stop on ingestion cost
  }
}

// ---------- Azure MCP Server ----------
// The server's Entra app is registered by Start-Spike.ps1 through the Azure CLI, not the Bicep Graph extension:
// a real run (2026-10-18) got Authorization_RequestDenied from the extension in Cloud Shell for a Global Administrator.
@description('Client ID of the MCP server\'s Entra app (Start-Spike.ps1 registers it).')
param mcpAppId string

resource mcpEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-avdswarm-spike-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource mcpServer 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-avdswarm-mcp-${suffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: mcpEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        allowInsecure: false
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'azure-mcp'
          image: 'mcr.microsoft.com/azure-sdk/azure-mcp:latest'
          args: [
            '--transport', 'http'
            '--outgoing-auth-strategy', 'UseHostingEnvironmentIdentity'
            '--mode', 'all'
            '--read-only' // never remove: without it the server offers write tools
            '--namespace', 'virtualdesktop'
          ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'ASPNETCORE_ENVIRONMENT', value: 'Production' }
            { name: 'ASPNETCORE_URLS', value: 'http://+:8080' }
            { name: 'AZURE_TOKEN_CREDENTIALS', value: 'managedidentitycredential' }
            { name: 'AZURE_MCP_INCLUDE_PRODUCTION_CREDENTIALS', value: 'true' }
            { name: 'AZURE_MCP_COLLECT_TELEMETRY', value: 'false' }
            { name: 'AzureAd__Instance', value: environment().authentication.loginEndpoint }
            { name: 'AzureAd__TenantId', value: tenant().tenantId }
            { name: 'AzureAd__ClientId', value: mcpAppId }
            // HTTPS ends at the Container Apps ingress; inside the pod it's HTTP (same as the sample).
            { name: 'AZURE_MCP_DANGEROUSLY_DISABLE_HTTPS_REDIRECTION', value: 'true' }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// The MCP server reads AVD objects in this resource group only.
resource mcpReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, mcpServer.id, desktopVirtualizationReaderRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', desktopVirtualizationReaderRoleId)
    principalId: mcpServer.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------- Something for virtualdesktop to read: no session hosts, so nothing that bills ----------
resource hostPool 'Microsoft.DesktopVirtualization/hostPools@2024-04-03' = {
  name: 'hp-avdswarm-spike'
  location: avdLocation
  tags: tags
  properties: {
    hostPoolType: 'Pooled'
    loadBalancerType: 'BreadthFirst'
    maxSessionLimit: 4
    preferredAppGroupType: 'Desktop'
    validationEnvironment: true
  }
}

resource desktopGroup 'Microsoft.DesktopVirtualization/applicationGroups@2024-04-03' = {
  name: 'dag-avdswarm-spike'
  location: avdLocation
  tags: tags
  properties: {
    applicationGroupType: 'Desktop'
    hostPoolArmPath: hostPool.id
  }
}

resource workspace 'Microsoft.DesktopVirtualization/workspaces@2024-04-03' = {
  name: 'ws-avdswarm-spike'
  location: avdLocation
  tags: tags
  properties: {
    applicationGroupReferences: [desktopGroup.id]
  }
}

// ---------- Fallback: the agents in a Container App, calling Anthropic's API ----------
@description('anthropic only: client ID of the agent identity blueprint (Start-Spike.ps1 creates it).')
param blueprintAppId string = ''

@description('anthropic only: object ID of the person running the spike; the only caller the agent service accepts.')
param operatorObjectId string = ''

@description('anthropic only: base URL of agent_api/ at the pushed commit (raw.githubusercontent.com); the container installs and runs it.')
param agentCodeUrl string = ''

@description('anthropic only: the run\'s cap on Claude spend in USD, metered from each response before the next call (agent_api/meter.py).')
param tokenSpendCapUsd string = '15'

@description('anthropic only: client ID of the app registration Entra issues Claude API tokens for (workload identity federation, Start-Spike.ps1 creates it).')
param claudeAudienceAppId string = ''

@description('anthropic only: scan the question and every tool result with Prompt Shields (agent_api/shield.py, red team R26). Off by default; the free tier allows one Content Safety F0 resource per subscription.')
param promptShields bool = false

@description('anthropic only: observe counts hits and passes text through; enforce withholds flagged documents.')
@allowed(['observe', 'enforce'])
param promptShieldsMode string = 'observe'

var scanOn = provider == 'anthropic' && promptShields

// Two jobs, both without a secret: the blueprint's federated credential (FIC subject), and the identity
// Anthropic's workload identity federation rule matches (oid) to let this app call Claude.
resource agentsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (provider == 'anthropic') {
  name: 'id-avdswarm-agents-${suffix}'
  location: location
  tags: tags
}

resource agentsApp 'Microsoft.App/containerApps@2024-03-01' = if (provider == 'anthropic') {
  name: 'ca-avdswarm-agents-${suffix}'
  location: location
  tags: tags
  // The scan's role assignment exists before the app starts (it can still take minutes to apply; scan errors are counted).
  dependsOn: [contentSafetyUser]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${agentsIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: mcpEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        allowInsecure: false
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'agents'
          image: 'docker.io/library/python:3.13-slim'
          command: ['sh', '-c']
          // No image build: install the pinned requirements and fetch main.py, meter.py and shield.py from the pushed commit, then run it.
          args: [
            'pip install --no-cache-dir -q -r ${agentCodeUrl}/requirements.txt && python -c "import urllib.request as u; [u.urlretrieve(\'${agentCodeUrl}/\' + f, \'/tmp/\' + f) for f in (\'main.py\', \'meter.py\', \'shield.py\')]" && exec python /tmp/main.py'
          ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'CLAUDE_AUDIENCE_APP_ID', value: claudeAudienceAppId }
            // Anthropic federation IDs (organization, service account, rule, workspace): not secrets, set by Invoke-Spike.ps1 once the rule exists
            { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
            { name: 'BLUEPRINT_APP_ID', value: blueprintAppId }
            { name: 'UAMI_CLIENT_ID', value: provider == 'anthropic' ? agentsIdentity!.properties.clientId : '' }
            { name: 'MCP_APP_ID', value: mcpAppId }
            { name: 'MCP_URL', value: 'https://${mcpServer.properties.configuration.ingress.fqdn}' }
            { name: 'OPERATOR_OID', value: operatorObjectId }
            { name: 'HOSTS_ALLOWED_TOOLS', value: '' } // set after Q3
            { name: 'HOSTS_EFFORT', value: 'medium' }
            { name: 'COORDINATOR_EFFORT', value: 'low' }
            { name: 'TOKEN_SPEND_CAP_USD', value: tokenSpendCapUsd }
            { name: 'CONTENT_SAFETY_ENDPOINT', value: scanOn ? contentSafety!.properties.endpoint : '' }
            { name: 'PROMPT_SHIELDS_MODE', value: promptShieldsMode }
          ]
        }
      ]
      // One replica that stays up for the run: jobs live in memory, and installing at start takes a minute.
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// ---------- Prompt Shields for the Anthropic path (red team R26), off by default ----------
// Foundry's guardrails never see calls to Anthropic's API, so the agent service scans untrusted text itself.
// Entra only: local auth is disabled, so no key exists, and the agents' identity calls it with its own token.
resource contentSafety 'Microsoft.CognitiveServices/accounts@2025-06-01' = if (scanOn) {
  name: 'cs-avdswarm-spike-${suffix}'
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: { name: 'F0' } // 5,000 text records a month free; the S1a run needs a few hundred
  properties: {
    customSubDomainName: 'cs-avdswarm-spike-${suffix}' // Entra tokens need a custom subdomain endpoint
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

// Cognitive Services User on this one resource only. Its listKeys action is moot: local auth is off.
resource contentSafetyUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (scanOn) {
  name: guid(contentSafety.id, agentsIdentity.id, cognitiveServicesUserRoleId)
  scope: contentSafety
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: agentsIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output foundryName string = provider == 'foundry' ? foundry!.name : ''
output projectEndpoint string = provider == 'foundry' ? 'https://${foundry!.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project!.name}' : ''
output agentsUrl string = provider == 'anthropic' ? 'https://${agentsApp!.properties.configuration.ingress.fqdn}' : ''
output agentsAppName string = provider == 'anthropic' ? agentsApp!.name : ''
output agentsIdentityPrincipalId string = provider == 'anthropic' ? agentsIdentity!.properties.principalId : ''
output mcpUrl string = 'https://${mcpServer.properties.configuration.ingress.fqdn}'
output mcpAppId string = mcpAppId
output promptShields string = scanOn ? promptShieldsMode : 'off'
