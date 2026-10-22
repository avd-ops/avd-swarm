// avd-swarm alert path: one read role for one principal at the scope this module is deployed to.
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
targetScope = 'resourceGroup'

param principalId string
param roleId string

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, principalId, roleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
