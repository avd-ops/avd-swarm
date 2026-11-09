// avd-swarm kill switch and watchdog receiver: Consumption Logic App + capability-removing custom role + ABAC condition
// (governance.md sections 2.3 and 2.4; R5, R6, R13, V1-15).
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
//
// A TEMPLATE for an adopter's private deployment. The switch runs as its own Consumption Logic App with its own
// user-assigned managed identity holding custom roles that can only take capability away (R5):
//   1. Microsoft.Resources/tags/write on the swarm resource group (`avd-swarm-switch` tag)
//   2. Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/delete on the `approve` identity
//   3. Microsoft.Authorization/roleAssignments/delete with a conditionVersion 2.0 delegation condition limiting removal
//      strictly to the swarm's agent principal IDs (never the landing zone deploy identity, operator, or `stop-spend`; V1-15)
//   4. Microsoft.App/containerApps/stop/action on the agent API, MCP tool layer, and avd-buddy Container Apps
//
// It cannot create a credential, grant a role, start a Container App, or reset the switch to `run` (R6: only a person
// resets from Cloud Shell). Stopping spend (`stop-spend` identity) is never touched at any switch position.

@description('Region for the switch Logic App and its user-assigned managed identity.')
param location string = resourceGroup().location

@description('Spend run ID, tagged on every resource (docs/spend.md).')
param runId string

@description('Name of the approve user-assigned managed identity whose federated credential is deleted at halt-actions and above.')
param approveIdentityName string

@description('Name of the GitHub OIDC federated credential on the approve identity.')
param approveFederatedCredentialName string = 'github-approve-environment'

@description('Name of the agent API Container App stopped at halt-agents and above.')
param agentContainerAppName string

@description('Name of the avd-buddy Container App stopped at halt-all.')
param buddyContainerAppName string

@description('Principal ID of the Hosts specialist agent identity (for the V1-15 ABAC roleAssignments/delete condition).')
param agentHostsPrincipalId string

@description('Principal ID of the Cost specialist agent identity (for the V1-15 ABAC roleAssignments/delete condition).')
param agentCostPrincipalId string

@description('Principal ID of the Intune specialist agent identity (for the V1-15 ABAC roleAssignments/delete condition).')
param agentIntunePrincipalId string

@description('Resource Manager endpoint base URL (https://management.azure.com for commercial, https://management.usgovcloudapi.net for usgov; C26).')
@allowed([
  'https://management.azure.com'
  'https://management.usgovcloudapi.net'
])
param armEndpoint string = 'https://management.azure.com'

param suffix string = take(uniqueString(resourceGroup().id), 6)

var tags = { 'avd-swarm-run': runId, 'avd-swarm-part': 'kill-switch' }

resource switchIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'avd-swarm-switch-${suffix}'
  location: location
  tags: tags
}

// Capability-removing custom role (R5): can only write tags, delete federated credentials, delete role assignments, and stop Container Apps.
resource switchRoleDef 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'avd-swarm-kill-switch-role')
  properties: {
    roleName: 'avd-swarm-kill-switch-${suffix}'
    description: 'Capability-removing role for the avd-swarm kill switch Logic App (R5, V1-15). Can never grant roles, create credentials, or start compute.'
    type: 'CustomRole'
    assignableScopes: [
      resourceGroup().id
    ]
    permissions: [
      {
        actions: [
          'Microsoft.Resources/tags/read'
          'Microsoft.Resources/tags/write'
          'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/read'
          'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/delete'
          'Microsoft.Authorization/roleAssignments/read'
          'Microsoft.Authorization/roleAssignments/delete'
          'Microsoft.App/containerApps/read'
          'Microsoft.App/containerApps/stop/action'
        ]
        notActions: [
          'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/write'
          'Microsoft.Authorization/roleAssignments/write'
          'Microsoft.Authorization/policyExemptions/write'
          'Microsoft.Authorization/policyAssignments/write'
          'Microsoft.Security/*'
          'Microsoft.App/containerApps/start/action'
          'Microsoft.App/containerApps/write'
        ]
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

// ABAC delegation condition (red team V1-15): roleAssignments/delete is restricted to the swarm's agent principal IDs
// so the switch can never remove the landing zone's deploy identity, the stop-spend identity, or the human operator.
var abacCondition = '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:PrincipalId] StringEqualsIgnoreCase \'${agentHostsPrincipalId}\' OR @Resource[Microsoft.Authorization/roleAssignments:PrincipalId] StringEqualsIgnoreCase \'${agentCostPrincipalId}\' OR @Resource[Microsoft.Authorization/roleAssignments:PrincipalId] StringEqualsIgnoreCase \'${agentIntunePrincipalId}\'))'

resource switchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, switchIdentity.id, switchRoleDef.id)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: switchRoleDef.id
    principalId: switchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    conditionVersion: '2.0'
    condition: abacCondition
  }
}

resource switchLogicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'avd-swarm-switch-${suffix}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${switchIdentity.id}': {}
    }
  }
  dependsOn: [
    switchRoleAssignment
  ]
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        trip: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
              required: [
                'position'
                'reason'
              ]
              properties: {
                position: {
                  type: 'string'
                  enum: [
                    'halt-actions'
                    'halt-agents'
                    'halt-all'
                  ]
                }
                reason: {
                  type: 'string'
                }
              }
            }
          }
        }
      }
      actions: {
        Refuse_reset_to_run: {
          type: 'If'
          runAfter: {}
          expression: {
            and: [
              {
                equals: [
                  '@triggerBody()?[\'position\']'
                  'run'
                ]
              }
            ]
          }
          actions: {
            Terminate_refused_reset: {
              type: 'Terminate'
              inputs: {
                runStatus: 'Failed'
                runError: {
                  code: 'ResetForbiddenByMachine'
                  message: 'Only a person running the Cloud Shell reset block can return the switch to run (governance.md section 2.3, R6).'
                }
              }
            }
          }
        }
        Layer1_set_switch_tag: {
          type: 'Http'
          runAfter: {
            Refuse_reset_to_run: [
              'Succeeded'
            ]
          }
          inputs: {
            method: 'PATCH'
            uri: '${armEndpoint}${resourceGroup().id}/providers/Microsoft.Resources/tags/default?api-version=2022-09-01'
            headers: {
              'Content-Type': 'application/json'
            }
            body: {
              operation: 'Merge'
              properties: {
                tags: {
                  'avd-swarm-switch': '@{triggerBody()?[\'position\']}'
                }
              }
            }
            authentication: {
              type: 'ManagedServiceIdentity'
              identity: switchIdentity.id
              audience: '${armEndpoint}/'
            }
          }
        }
        Layer2_delete_approve_federated_credential: {
          type: 'Http'
          runAfter: {
            Layer1_set_switch_tag: [
              'Succeeded'
            ]
          }
          inputs: {
            method: 'DELETE'
            uri: '${armEndpoint}${resourceGroup().id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${approveIdentityName}/federatedIdentityCredentials/${approveFederatedCredentialName}?api-version=2023-01-31'
            authentication: {
              type: 'ManagedServiceIdentity'
              identity: switchIdentity.id
              audience: '${armEndpoint}/'
            }
          }
        }
        Layer6_stop_agent_container_app_at_halt_agents_or_all: {
          type: 'If'
          runAfter: {
            Layer2_delete_approve_federated_credential: [
              'Succeeded'
            ]
          }
          expression: {
            or: [
              {
                equals: [
                  '@triggerBody()?[\'position\']'
                  'halt-agents'
                ]
              }
              {
                equals: [
                  '@triggerBody()?[\'position\']'
                  'halt-all'
                ]
              }
            ]
          }
          actions: {
            Stop_agent_container_app: {
              type: 'Http'
              inputs: {
                method: 'POST'
                uri: '${armEndpoint}${resourceGroup().id}/providers/Microsoft.App/containerApps/${agentContainerAppName}/stop?api-version=2025-01-01'
                authentication: {
                  type: 'ManagedServiceIdentity'
                  identity: switchIdentity.id
                  audience: '${armEndpoint}/'
                }
              }
            }
          }
        }
        Layer5_stop_buddy_container_app_at_halt_all: {
          type: 'If'
          runAfter: {
            Layer6_stop_agent_container_app_at_halt_agents_or_all: [
              'Succeeded'
            ]
          }
          expression: {
            and: [
              {
                equals: [
                  '@triggerBody()?[\'position\']'
                  'halt-all'
                ]
              }
            ]
          }
          actions: {
            Stop_buddy_container_app: {
              type: 'Http'
              inputs: {
                method: 'POST'
                uri: '${armEndpoint}${resourceGroup().id}/providers/Microsoft.App/containerApps/${buddyContainerAppName}/stop?api-version=2025-01-01'
                authentication: {
                  type: 'ManagedServiceIdentity'
                  identity: switchIdentity.id
                  audience: '${armEndpoint}/'
                }
              }
            }
          }
        }
      }
    }
  }
}

resource watchdogActionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'avd-swarm-watchdog-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'swarmswtch'
    enabled: true
    logicAppReceivers: [
      {
        name: 'kill-switch'
        resourceId: switchLogicApp.id
        callbackUrl: listCallbackUrl(resourceId('Microsoft.Logic/workflows/triggers', switchLogicApp.name, 'trip'), '2019-05-01').value
        useCommonAlertSchema: true
      }
    ]
  }
}

output switchLogicAppId string = switchLogicApp.id
output switchPrincipalId string = switchIdentity.properties.principalId
output watchdogActionGroupId string = watchdogActionGroup.id
