// avd-swarm S1a spike. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
//
// Everything the spike needs except the model deployments (deployed by name from what the region
// offers, README step 4) and the hosted agent (deployed from code, README step 6).
// The Azure MCP Server part follows the setup in Azure-Samples/azmcp-foundry-aca-mi, with the
// virtualdesktop namespace, scale to zero, and Azure CLI pre-authorized so the operator can list tools.

extension microsoftGraphV1

targetScope = 'resourceGroup'

@description('Region for the Foundry resource and the Container App: one where Claude in Foundry is offered.')
param location string = 'eastus2'

@description('Region for the AVD objects. The landing zone region, so they need no policy exemption.')
param avdLocation string = resourceGroup().location

@description('Spend run ID, tagged on every resource (tenant-swarm docs/spend.md).')
param runId string

@description('Short unique suffix for globally unique names.')
param suffix string = take(uniqueString(resourceGroup().id), 6)

var tags = {
  workload: 'avdswarm-spike'
  'spend-run': runId
}
var desktopVirtualizationReaderRoleId = '49a72310-ab8d-41df-bbb0-79b649203868'
// Microsoft Azure CLI, a first-party app: lets the operator get a delegated token to list tools.
var azureCliAppId = '04b07795-8ddb-461a-bbee-02f9e1bf7b46'

// ---------- Foundry ----------
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
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

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
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
var mcpRoleValue = 'Mcp.Tools.ReadWrite.All'
var mcpRoleId = guid(resourceGroup().id, mcpRoleValue)
var mcpScopeValue = 'Mcp.Tools.ReadWrite'
var mcpScopeId = guid(resourceGroup().id, mcpScopeValue)
var mcpAppUniqueName = 'avdswarm-spike-mcp-${suffix}'

resource mcpApp 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: mcpAppUniqueName
  displayName: 'avdswarm spike Azure MCP Server (${runId})'
  signInAudience: 'AzureADMyOrg'
  appRoles: [
    {
      id: mcpRoleId
      value: mcpRoleValue
      displayName: 'Azure MCP tools'
      description: 'Call the spike Azure MCP Server (read-only tools only)'
      isEnabled: true
      allowedMemberTypes: ['Application']
    }
  ]
  api: {
    requestedAccessTokenVersion: 2
    oauth2PermissionScopes: [
      {
        id: mcpScopeId
        value: mcpScopeValue
        type: 'User'
        adminConsentDisplayName: 'Azure MCP tools'
        adminConsentDescription: 'Call the spike Azure MCP Server as the signed-in user'
        userConsentDisplayName: 'Azure MCP tools'
        userConsentDescription: 'Call the spike Azure MCP Server as you'
        isEnabled: true
      }
    ]
  }
}

// The identifier URI and pre-authorization need the app ID, so they are a second write of the same app.
resource mcpAppUpdate 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: mcpAppUniqueName
  displayName: mcpApp.displayName
  signInAudience: 'AzureADMyOrg'
  appRoles: mcpApp.appRoles
  identifierUris: ['api://${mcpApp.appId}']
  api: {
    requestedAccessTokenVersion: 2
    oauth2PermissionScopes: mcpApp.api.oauth2PermissionScopes
    preAuthorizedApplications: [
      {
        appId: azureCliAppId
        delegatedPermissionIds: [mcpScopeId]
      }
    ]
  }
}

resource mcpAppSp 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: mcpApp.appId
}

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
            { name: 'AzureAd__ClientId', value: mcpApp.appId }
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

output foundryName string = foundry.name
output projectEndpoint string = 'https://${foundry.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'
output mcpUrl string = 'https://${mcpServer.properties.configuration.ingress.fqdn}'
output mcpAppId string = mcpApp.appId
output mcpAppSpId string = mcpAppSp.id
output mcpAppRoleId string = mcpRoleId
