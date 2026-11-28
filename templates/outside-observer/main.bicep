// avd-swarm 3-layer near-zero-cost hardened Outside Observer (docs/outside-observer-spec.md; governance.md §2.2-2.4).
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
//
// Isolated RG boundary:
//   - Zero avd-swarm identities hold any RBAC role on this resource group.
//   - Observer User-Assigned Managed Identity holds zero write/reset roles on AVD or credentials.
//   - Zero Log Analytics workspaces and zero Microsoft Sentinel ingestion ($0 workspace cost).
//   - Layer 1: Consumption Azure Function (Y1 Linux Python 3.12) 5-min timer corroborator + immutable audit container.
//   - Layer 2: Azure Monitor Activity Log Alerts + Action Group wired to the Kill Switch Logic App `trip` trigger.
//   - Layer 3: Stateless HTTP `verdict` endpoint on the same Consumption Function App (Entra/HMAC authenticated).

targetScope = 'resourceGroup'

@description('Azure region for the observer Function App, storage, and managed identity.')
param location string = resourceGroup().location

@description('Spend run ID, tagged on every resource (docs/spend.md).')
param runId string

@description('Optional lowercase batch identifier (defaults to lowercase runId).')
param batchId string = ''

@description('UTC ISO-8601 expiry timestamp (YYYY-MM-DDTHH:MM:SSZ) for automated orphan sweepers (defaults to UTC now + 4h).')
param expireOn string = dateTimeAdd(utcNow(), 'PT4H')

@description('Resource ID of the Kill Switch Logic App (templates/kill-switch/main.bicep).')
param killSwitchLogicAppId string

@secure()
@description('Callback URL of the Kill Switch Logic App `trip` HTTP trigger.')
param killSwitchTripCallbackUrl string

@description('Principal ID of the `approve` managed identity monitored for unapproved writes.')
param approvePrincipalId string = ''

@description('Principal ID of Azure SRE Agent monitored for any write (governance.md §2.4).')
param sreAgentPrincipalId string = ''

@description('Optional external dead-man switch URL pinged on healthy 5-min corroborator runs.')
param deadmanSwitchUrl string = ''

@description('Optional owner email for Action Group notifications.')
param ownerEmail string = ''

@description('Cloud environment profile (C26): commercial or usgov.')
@allowed([
  'commercial'
  'usgov'
])
param cloudEnvironment string = 'commercial'

param suffix string = take(uniqueString(resourceGroup().id), 6)

var effectiveBatchId = empty(batchId) ? toLower(runId) : toLower(batchId)
var tags = {
  'avd-swarm-run': runId
  'avd-swarm-part': 'outside-observer'
  'batch-id': effectiveBatchId
  'expire-on': expireOn
}

var armEndpoint = cloudEnvironment == 'usgov' ? 'https://management.usgovcloudapi.net' : 'https://management.azure.com'
var graphEndpoint = cloudEnvironment == 'usgov' ? 'https://graph.microsoft.us' : 'https://graph.microsoft.com'

// Built-in Microsoft RBAC role definition IDs (listed in scripts/public-ids.txt)
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'

resource observerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-avd-observer-${suffix}'
  location: location
  tags: tags
}

// Runtime storage for Consumption Function App (shared key disabled, TLS 1.2, no public blob access)
resource runtimeStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take(toLower('stobsrun${suffix}'), 24)
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
  }
}

// Immutable audit storage for signed CloudEvents (1-year WORM retention; R12, decision 0016)
resource auditStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take(toLower('stobsaud${suffix}'), 24)
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource auditBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: auditStorage
  name: 'default'
}

resource auditEventsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: auditBlobService
  name: 'audit-events'
  properties: {
    publicAccess: 'None'
  }
}

resource auditImmutabilityPolicy 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = {
  parent: auditEventsContainer
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: 365
    allowProtectedAppendWrites: true
  }
}

// Least-privilege storage roles for the observer Function App runtime (scoped only to runtimeStorage)
resource runtimeBlobOwnerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(runtimeStorage.id, observerIdentity.id, storageBlobDataOwnerRoleId)
  scope: runtimeStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalId: observerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource runtimeQueueAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(runtimeStorage.id, observerIdentity.id, storageQueueDataContributorRoleId)
  scope: runtimeStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
    principalId: observerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource runtimeTableAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(runtimeStorage.id, observerIdentity.id, storageTableDataContributorRoleId)
  scope: runtimeStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
    principalId: observerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Read-only access to the immutable audit container
resource auditBlobReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(auditStorage.id, observerIdentity.id, storageBlobDataReaderRoleId)
  scope: auditStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalId: observerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource consumptionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'asp-avd-observer-${suffix}'
  location: location
  tags: tags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

// Layer 1 + Layer 3 Consumption Function App
resource observerFunctionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: 'func-avd-observer-${suffix}'
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${observerIdentity.id}': {}
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
          value: runtimeStorage.name
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'AzureWebJobsStorage__clientId'
          value: observerIdentity.properties.clientId
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: observerIdentity.properties.clientId
        }
        {
          name: 'CLOUD_ENVIRONMENT'
          value: cloudEnvironment
        }
        {
          name: 'ARM_ENDPOINT'
          value: armEndpoint
        }
        {
          name: 'GRAPH_ENDPOINT'
          value: graphEndpoint
        }
        {
          name: 'AUDIT_STORAGE_BLOB_ENDPOINT'
          value: auditStorage.properties.primaryEndpoints.blob
        }
        {
          name: 'AUDIT_CONTAINER_NAME'
          value: auditEventsContainer.name
        }
        {
          name: 'KILL_SWITCH_TRIP_URL'
          value: killSwitchTripCallbackUrl
        }
        {
          name: 'DEADMAN_SWITCH_URL'
          value: deadmanSwitchUrl
        }
        {
          name: 'SRE_AGENT_PRINCIPAL_ID'
          value: sreAgentPrincipalId
        }
        {
          name: 'OBSERVER_SILENCE_MINUTES'
          value: '15'
        }
      ]
    }
  }
  dependsOn: [
    runtimeBlobOwnerAssignment
    runtimeQueueAssignment
    runtimeTableAssignment
    auditBlobReaderAssignment
  ]
}

// Layer 2: Action Group wired directly to Kill Switch Logic App `trip` trigger (zero Log Analytics cost)
resource observerActionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-avd-observer-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'avdobsvr'
    enabled: true
    logicAppReceivers: [
      {
        name: 'kill-switch-trip'
        resourceId: killSwitchLogicAppId
        callbackUrl: killSwitchTripCallbackUrl
        useCommonAlertSchema: true
      }
    ]
    emailReceivers: !empty(ownerEmail) ? [
      {
        name: 'owner'
        emailAddress: ownerEmail
        useCommonAlertSchema: true
      }
    ] : []
  }
}

// Layer 2 Alert 1: Unapproved writes by the `approve` identity
resource alertApproveWrite 'Microsoft.Insights/activityLogAlerts@2020-10-01' = if (!empty(approvePrincipalId)) {
  name: 'ala-obs-approve-write-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    scopes: [
      subscription().id
    ]
    description: 'Trips kill switch when the approve identity performs an Administrative write.'
    condition: {
      allOf: [
        {
          field: 'category'
          equals: 'Administrative'
        }
        {
          field: 'caller'
          equals: approvePrincipalId
        }
      ]
    }
    actions: {
      actionGroups: [
        {
          actionGroupId: observerActionGroup.id
        }
      ]
    }
  }
}

// Layer 2 Alert 2: RBAC or Azure Policy changes (roleAssignments/write, policyAssignments/write, policyExemptions/write)
resource alertRbacOrPolicyWrite 'Microsoft.Insights/activityLogAlerts@2020-10-01' = {
  name: 'ala-obs-rbac-policy-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    scopes: [
      subscription().id
    ]
    description: 'Trips kill switch on RBAC role assignment or Azure Policy / exemption writes.'
    condition: {
      allOf: [
        {
          field: 'category'
          equals: 'Administrative'
        }
        {
          anyOf: [
            {
              field: 'operationName'
              equals: 'Microsoft.Authorization/roleAssignments/write'
            }
            {
              field: 'operationName'
              equals: 'Microsoft.Authorization/policyAssignments/write'
            }
            {
              field: 'operationName'
              equals: 'Microsoft.Authorization/policyExemptions/write'
            }
          ]
        }
      ]
    }
    actions: {
      actionGroups: [
        {
          actionGroupId: observerActionGroup.id
        }
      ]
    }
  }
}

// Layer 2 Alert 3: Defender for Cloud configuration writes (Microsoft.Security/*)
resource alertDefenderWrite 'Microsoft.Insights/activityLogAlerts@2020-10-01' = {
  name: 'ala-obs-defender-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    scopes: [
      subscription().id
    ]
    description: 'Trips kill switch on Microsoft.Security Defender plan or automation writes.'
    condition: {
      allOf: [
        {
          field: 'category'
          equals: 'Administrative'
        }
        {
          anyOf: [
            {
              field: 'operationName'
              equals: 'Microsoft.Security/pricings/write'
            }
            {
              field: 'operationName'
              equals: 'Microsoft.Security/automations/write'
            }
            {
              field: 'operationName'
              equals: 'Microsoft.Security/settings/write'
            }
          ]
        }
      ]
    }
    actions: {
      actionGroups: [
        {
          actionGroupId: observerActionGroup.id
        }
      ]
    }
  }
}

// Layer 2 Alert 4: Any Azure SRE Agent write (governance.md §2.4)
resource alertSreAgentWrite 'Microsoft.Insights/activityLogAlerts@2020-10-01' = if (!empty(sreAgentPrincipalId)) {
  name: 'ala-obs-sre-agent-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    scopes: [
      subscription().id
    ]
    description: 'Trips kill switch immediately if Azure SRE Agent performs any Administrative write.'
    condition: {
      allOf: [
        {
          field: 'category'
          equals: 'Administrative'
        }
        {
          field: 'caller'
          equals: sreAgentPrincipalId
        }
      ]
    }
    actions: {
      actionGroups: [
        {
          actionGroupId: observerActionGroup.id
        }
      ]
    }
  }
}

output observerIdentityId string = observerIdentity.id
output observerIdentityPrincipalId string = observerIdentity.properties.principalId
output observerIdentityClientId string = observerIdentity.properties.clientId
output observerFunctionAppName string = observerFunctionApp.name
output observerActionGroupId string = observerActionGroup.id
output auditStorageName string = auditStorage.name
