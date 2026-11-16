// Smart Notification, Escalation & Multi-Platform Approval Router (docs/notifications.md; decision 0011).
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
//
// Deploys an independent Consumption host so halt-all and stop-spend notifications survive avd-buddy stopping:
// - Event Grid custom topic for avd-swarm signals + dead-letter blob container
// - Azure Storage account with Table Storage (`NotificationRouterState`) for escalation timers and quotas
// - Azure Functions Consumption (Y1 Linux Python 3.12) with User-Assigned Managed Identity
// - Least-privilege RBAC: Storage Table Data Contributor + Key Vault Secrets User (when keyVaultName is provided)

targetScope = 'resourceGroup'

@description('Azure region for all notification router resources.')
param location string = resourceGroup().location

@description('Short naming prefix (letters/numbers only, 2-8 chars).')
@minLength(2)
@maxLength(8)
param prefix string = 'avd'

@description('Environment label (e.g. lab, dev, prod).')
@minLength(2)
@maxLength(8)
param env string = 'lab'

@description('Hostname of the avd-buddy Container App for /plans/<planId> deep links.')
param buddyHost string = 'avd-buddy.example.invalid'

@description('Optional Key Vault name in the same resource group holding VAPID, Teams webhook, ACS/Twilio, and co-sign keys.')
param keyVaultName string = ''

var suffix = uniqueString(resourceGroup().id, prefix, env)
var storageName = take(toLower('st${prefix}${env}${suffix}'), 24)
var topicName = toLower('evgt-${prefix}-${env}-swarm')
var planName = toLower('asp-${prefix}-${env}-notif')
var funcName = toLower('func-${prefix}-${env}-notif-${take(suffix, 6)}')
var identityName = toLower('id-${prefix}-${env}-notif')

// Built-in Microsoft RBAC role definition IDs (listed in scripts/public-ids.txt)
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource routerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource deadLetterContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'deadletter'
  properties: {
    publicAccess: 'None'
  }
}

resource tableServices 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource routerStateTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableServices
  name: 'NotificationRouterState'
}

resource tableDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, routerIdentity.id, storageTableDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
    principalId: routerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource existingKeyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (!empty(keyVaultName)) {
  name: keyVaultName
}

resource keyVaultSecretsAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(keyVaultName)) {
  name: guid(resourceGroup().id, keyVaultName, routerIdentity.id, keyVaultSecretsUserRoleId)
  scope: existingKeyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: routerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource swarmEventGridTopic 'Microsoft.EventGrid/topics@2023-12-15-preview' = {
  name: topicName
  location: location
  properties: {
    inputSchema: 'EventGridSchema'
    publicNetworkAccess: 'Enabled'
  }
}

resource consumptionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource routerFunctionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: funcName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${routerIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: consumptionPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storageAccount.name
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'AzureWebJobsStorage__clientId'
          value: routerIdentity.properties.clientId
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: routerIdentity.properties.clientId
        }
        {
          name: 'AZURE_TABLES_ENDPOINT'
          value: storageAccount.properties.primaryEndpoints.table
        }
        {
          name: 'NOTIFICATIONS_TABLE_NAME'
          value: 'NotificationRouterState'
        }
        {
          name: 'KEY_VAULT_URL'
          value: !empty(keyVaultName) ? 'https://${keyVaultName}${environment().suffixes.keyvaultDns}' : ''
        }
        {
          name: 'AVD_BUDDY_HOST'
          value: buddyHost
        }
      ]
    }
  }
  dependsOn: [
    tableDataContributorAssignment
    routerStateTable
  ]
}

output routerFunctionAppName string = routerFunctionApp.name
output routerIdentityPrincipalId string = routerIdentity.properties.principalId
output swarmEventGridTopicEndpoint string = swarmEventGridTopic.properties.endpoint
output stateTableEndpoint string = storageAccount.properties.primaryEndpoints.table
