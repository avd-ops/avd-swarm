// avd-swarm bouncer verification spike: minimal disposable Azure resources for §6 assumptions.
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

@description('Azure region for all disposable resources.')
param location string = resourceGroup().location

@description('Spend run ID, tagged on every resource (docs/spend.md).')
param runId string

@description('Deploy a minimal empty AVD host pool + workspace for Assumption 5/4 testing (false if reusing an existing lab pool).')
param deployHostPool bool = false

param suffix string = take(uniqueString(resourceGroup().id), 6)

var tags = {
  'avd-swarm-run': runId
  'avd-swarm-part': 'bouncer-verify'
}

// 1. User-assigned managed identities (NO Graph app roles granted; Entra AU roles assigned by Invoke-Spike.ps1).
resource idQuarantine 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-bouncer-verify-quarantine-${suffix}'
  location: location
  tags: tags
}

resource idRevoke 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-bouncer-verify-revoke-${suffix}'
  location: location
  tags: tags
}

resource idDrain 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-bouncer-verify-drain-${suffix}'
  location: location
  tags: tags
}

// 2. Custom Azure RBAC role for Assumption 5: hostPools/read + sessionHosts/read + sessionHosts/write only.
resource drainRoleDef 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'avd-bouncer-verify-host-drain')
  properties: {
    roleName: 'avd-bouncer-verify-host-drain-${suffix}'
    description: 'Disposable verification role for bouncer.host-drain (docs/bouncer.md section 6).'
    type: 'CustomRole'
    assignableScopes: [
      resourceGroup().id
    ]
    permissions: [
      {
        actions: [
          'Microsoft.DesktopVirtualization/hostPools/read'
          'Microsoft.DesktopVirtualization/hostPools/sessionHosts/read'
          'Microsoft.DesktopVirtualization/hostPools/sessionHosts/write'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

resource drainRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, idDrain.id, drainRoleDef.id)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: drainRoleDef.id
    principalId: idDrain.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// 3. Log Analytics workspace + 1-min Scheduled Query Rule for Assumption 6 (alertsmanagementresources schema & lag).
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law-bouncer-verify-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource syntheticAlertRule 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'sq-bouncer-verify-synthetic-${suffix}'
  location: location
  tags: tags
  properties: {
    displayName: 'avd-swarm bouncer verify synthetic alert'
    description: 'Disposable 1-minute alert rule to measure alertsmanagementresources schema and lag (docs/bouncer.md section 6).'
    severity: 3
    enabled: true
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [
      law.id
    ]
    criteria: {
      allOf: [
        {
          query: 'Usage | summarize Count = count() | where Count >= 0'
          timeAggregation: 'Count'
          operator: 'GreaterThanOrEqual'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: false
  }
}

// 4. Optional minimal AVD host pool (no session hosts; add a lab host or pass -HostPoolResourceId for live write tests).
resource hostPool 'Microsoft.DesktopVirtualization/hostPools@2024-04-03' = if (deployHostPool) {
  name: 'hp-bouncer-verify-${suffix}'
  location: location
  tags: tags
  properties: {
    hostPoolType: 'Pooled'
    loadBalancerType: 'BreadthFirst'
    preferredAppGroupType: 'Desktop'
    maxSessionLimit: 2
  }
}

output quarantineIdentityId string = idQuarantine.id
output quarantinePrincipalId string = idQuarantine.properties.principalId
output quarantineClientId string = idQuarantine.properties.clientId
output revokeIdentityId string = idRevoke.id
output revokePrincipalId string = idRevoke.properties.principalId
output revokeClientId string = idRevoke.properties.clientId
output drainIdentityId string = idDrain.id
output drainPrincipalId string = idDrain.properties.principalId
output drainClientId string = idDrain.properties.clientId
output workspaceId string = law.id
output alertRuleId string = syntheticAlertRule.id
output alertRuleName string = syntheticAlertRule.name
output hostPoolId string = deployHostPool ? hostPool.id : ''
