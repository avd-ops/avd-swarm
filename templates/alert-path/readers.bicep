// avd-swarm alert path: the job identity's Reader on the swarm's resource group (the kill switch tag only).
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
param principalId string
param swarmResourceGroup string

module flagReader 'reader-role.bicep' = {
  name: 'flag-reader'
  scope: resourceGroup(swarmResourceGroup)
  params: {
    principalId: principalId
    roleId: 'acdd72a7-3385-48ef-bd42-f606fba81ae7' // Reader
  }
}
