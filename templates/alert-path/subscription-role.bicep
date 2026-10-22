// avd-swarm alert path: one read role for one principal on the subscription.
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
targetScope = 'subscription'

param principalId string
param roleId string

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, principalId, roleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
