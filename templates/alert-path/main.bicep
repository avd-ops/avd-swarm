// avd-swarm alert path: alert rule -> action group -> Consumption Logic App -> storage queue -> Container Apps Job
// (core-and-adapters.md WP6; spec section 3c).
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
//
// A TEMPLATE for an adopter's private deployment, deployed at resource group scope next to the agent API. Every hop is
// a managed identity: the Logic App's identity may only add queue messages, the job's identity may only process them,
// read alerts (Monitoring Reader on the subscription, where alert IDs live) and read the kill switch tag (Reader on the swarm's
// resource group). The message holds the alert ID and nothing else; the job reads the alert back from Azure Monitor.
// Nothing here holds a GitHub credential or a key (shared key access is off), and no workflow is started by an alert.
// Billing: the Logic App and the job bill per run; the job scales to zero; the storage account bills for what it holds.
//
// After deploying: point the alert rules that should start runbooks at the `actionGroupId` output (a runbook's
// trigger.alert pattern matches the rule's name), and give the job identity the agent API's `runbook.run` app role
// (docs/identities.md). Remove everything with the resource group or the spike's cleanup.

@description('Region for the storage account, the Logic App, the job and its identity.')
param location string = resourceGroup().location

@description('Spend run ID, tagged on every resource (docs/spend.md).')
param runId string

@description('Resource ID of the Container Apps environment the agent API runs in.')
param managedEnvironmentId string

@description('The agent API\'s URL and its app ID URI (the audience the job requests a token for).')
param agentApiUrl string
param agentApiResource string

@description('Commit the job runs: the same public repository and commit the agent API was deployed from, so both read the same runbooks (a runbook only in a private repo needs an image build, not this download).')
param repoCommit string

@description('The public repository the job downloads anonymously, as owner/name: the agent API\'s.')
param repo string = 'avd-ops/avd-swarm'

@description('Resource group holding the kill switch tag (the swarm\'s resource group).')
param swarmResourceGroup string = resourceGroup().name

param suffix string = take(uniqueString(resourceGroup().id), 6)

var tags = { 'avd-swarm-run': runId, 'avd-swarm-part': 'alert-path' }
var queueName = 'alerts'
var roles = {
  queueSender: 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'    // Storage Queue Data Message Sender
  queueProcessor: '8a0f0c08-91a1-4084-bc3d-661d67233fed' // Storage Queue Data Message Processor
  queueReader: '19e7f393-937e-4f77-808e-94535e297925'    // Storage Queue Data Reader: the scaler reads the queue length
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'avdswarmalerts${suffix}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource queue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: queueName
}

// The Logic App: takes the common alert schema from the action group and queues the alert ID, base64 encoded.
resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'avd-swarm-alerts-${suffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: { schema: {} }
        }
      }
      actions: {
        Queue_alert_id: {
          type: 'Http'
          runAfter: {}
          inputs: {
            method: 'POST'
            uri: '${storage.properties.primaryEndpoints.queue}${queueName}/messages'
            headers: { 'x-ms-version': '2021-12-02', 'Content-Type': 'application/xml' }
            body: '<QueueMessage><MessageText>@{base64(triggerBody()?[\'data\']?[\'essentials\']?[\'alertId\'])}</MessageText></QueueMessage>'
            authentication: { type: 'ManagedServiceIdentity', audience: 'https://storage.azure.com/' }
          }
        }
      }
    }
  }
}

resource senderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: queue
  name: guid(queue.id, logicApp.id, roles.queueSender)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.queueSender)
    principalId: logicApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'avd-swarm-alerts-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'avdswarm'
    enabled: true
    logicAppReceivers: [
      {
        name: 'alert-path'
        resourceId: logicApp.id
        callbackUrl: listCallbackUrl(resourceId('Microsoft.Logic/workflows/triggers', logicApp.name, 'manual'), '2019-05-01').value
        useCommonAlertSchema: true
      }
    ]
  }
}

resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'avd-swarm-alert-job-${suffix}'
  location: location
  tags: tags
}

resource processorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: queue
  name: guid(queue.id, jobIdentity.id, roles.queueProcessor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.queueProcessor)
    principalId: jobIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource queueReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: queue
  name: guid(queue.id, jobIdentity.id, roles.queueReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.queueReader)
    principalId: jobIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Alerts are read at their subscription-scoped ID (/subscriptions/<id>/providers/Microsoft.AlertsManagement/alerts/<id>),
// so Monitoring Reader is granted on the subscription: read-only monitoring data, no estate writes.
module alertReader 'subscription-role.bicep' = {
  name: 'avd-swarm-alert-job-monitoring-${suffix}'
  scope: subscription()
  params: {
    principalId: jobIdentity.properties.principalId
    roleId: '43d0d8ad-25c7-4714-9337-8ba259a9fe05' // Monitoring Reader
  }
}

module readers 'readers.bicep' = {
  name: 'avd-swarm-alert-job-readers-${suffix}'
  params: {
    principalId: jobIdentity.properties.principalId
    swarmResourceGroup: swarmResourceGroup
  }
}

// The job: wakes when the queue holds a message, runs `python -m avd_runner alert-job` from the pinned commit.
resource job 'Microsoft.App/jobs@2025-01-01' = {
  name: 'avd-swarm-alert-job-${suffix}'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${jobIdentity.id}': {} } }
  dependsOn: [processorRole, queueReaderRole, readers, alertReader]
  properties: {
    environmentId: managedEnvironmentId
    configuration: {
      triggerType: 'Event'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: 1
          pollingInterval: 60
          rules: [
            {
              name: 'alert-queue'
              type: 'azure-queue'
              metadata: { accountName: storage.name, queueName: queueName, queueLength: '1' }
              identity: jobIdentity.id
            }
          ]
        }
      }
    }
    template: {
      containers: [
        {
          name: 'alert-job'
          image: 'docker.io/library/python:3.13-slim'
          command: ['sh', '-c']
          // No image build: install the one pinned dependency, fetch the pinned commit, run the job.
          args: [
            'pip install --no-cache-dir -q pyyaml==6.0.2 && python -c "import io, tarfile, urllib.request as u; tarfile.open(fileobj=io.BytesIO(u.urlopen(\'https://codeload.github.com/${repo}/tar.gz/${repoCommit}\').read())).extractall(\'/tmp/src\', filter=\'data\')" && cd /tmp/src/* && PYTHONPATH=runner exec python -m avd_runner alert-job'
          ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
            { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
            { name: 'AVD_SWARM_RESOURCE_GROUP', value: swarmResourceGroup }
            { name: 'ALERT_QUEUE_ACCOUNT', value: storage.name }
            { name: 'ALERT_QUEUE_NAME', value: queueName }
            { name: 'AGENT_API_URL', value: agentApiUrl }
            { name: 'AGENT_API_RESOURCE', value: agentApiResource }
          ]
        }
      ]
    }
  }
}

output actionGroupId string = actionGroup.id
output jobPrincipalId string = jobIdentity.properties.principalId
