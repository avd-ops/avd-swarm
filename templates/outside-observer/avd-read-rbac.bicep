// Optional cross-RG read-only RBAC assignment for the Outside Observer identity on a target AVD resource group.
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
//
// Grants ONLY `Reader` (never Contributor, Owner, or any write/reset role).

targetScope = 'resourceGroup'

@description('Principal ID of the Outside Observer User-Assigned Managed Identity.')
param observerPrincipalId string

// Built-in Microsoft RBAC role definition ID: Reader (listed in scripts/public-ids.txt)
var readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'

resource avdReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, observerPrincipalId, readerRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', readerRoleId)
    principalId: observerPrincipalId
    principalType: 'ServicePrincipal'
  }
}
