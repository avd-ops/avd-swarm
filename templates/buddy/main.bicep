// avd-buddy scale-to-zero Container App with built-in Entra auth and GHCR image (docs/decisions/0010-avd-buddy-container-app-hosting.md, docs/buddy-app.md).
// Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
//
// Deploys into the adopter's swarm resource group and Container Apps Consumption environment:
// - Scales to zero (minReplicas: 0) inside the monthly Container Apps free grant ($0/month idle).
// - Uses Container Apps built-in authentication ("Easy Auth") with a custom single-tenant Entra ID app
//   registration, trusting a user-assigned managed identity via OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID
//   so no client secret exists and no token enters browser storage (HTTP-only session cookie only).
// - Calls agent_api over internal Container Apps environment ingress using its managed identity.

targetScope = 'resourceGroup'

@description('Azure region for the avd-buddy Container App and its user-assigned managed identity.')
param location string = resourceGroup().location

@description('Resource ID of the existing Container Apps Consumption managed environment (cae-<prefix>-<env>-swarm).')
param managedEnvironmentId string

@description('Internal HTTPS URL of agent_api inside the Container Apps environment.')
param agentApiInternalUrl string

@description('Digest-pinned public container image on GHCR (for example ghcr.io/avd-ops/avd-buddy@sha256:...). Empty uses python:3.13-slim with the pinned swarmCommit tarball.')
param buddyImage string = ''

@description('Repository (owner/name) to pull the avd-buddy source tarball from when buddyImage is empty.')
param repo string = 'avd-ops/avd-swarm'

@description('Pushed commit SHA (or ref) of avd-ops/avd-swarm to unpack when buddyImage is empty.')
param swarmCommit string = 'main'

@description('Entra ID tenant ID (<tenant-id>) passed from the adopter\'s repository variable; never committed.')
param tenantId string = tenant().tenantId

@description('Client ID of the custom single-tenant Entra ID sign-in app registration (assignmentRequired = true).')
param entraSignInClientId string

@description('Client ID of the MCP / agent_api Entra app registration (audience api://<mcpAppId> for managed identity tokens).')
param mcpAppId string = ''

@description('Client ID of the Entra Agent ID identity passed to agent_api /ask.')
param agentIdentityAppId string = ''

@description('Default model deployment name passed to agent_api /ask.')
param agentModel string = 'claude-sonnet-5-5'

@description('Daily metered token spend cap in USD shown on the Status screen.')
param tokenSpendCapUsd string = '15.00'

@description('Whether to start in deterministic simulation mode when agent_api is not yet connected.')
@allowed(['true', 'false'])
param simMode string = 'false'

@description('Short unique suffix for resource names.')
param suffix string = take(uniqueString(resourceGroup().id), 6)

var openIdIssuer = '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
var useTarballRuntime = empty(buddyImage)

// User-assigned managed identity for avd-buddy (docs/identities.md):
// 1. Federated credential on the Entra sign-in app (OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID) so Easy Auth has no secret.
// 2. Caller identity accepted by agent_api over internal ingress (red team V1-16).
// Holds no Azure RBAC role and no Microsoft Graph permission.
resource buddyIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-avdswarm-buddy-${suffix}'
  location: location
}

resource buddyApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-avdswarm-buddy-${suffix}'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${buddyIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        allowInsecure: false
        transport: 'auto'
      }
      secrets: [
        {
          name: 'override-use-mi-fic-assertion-clientid'
          value: buddyIdentity.properties.clientId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'avd-buddy'
          image: useTarballRuntime ? 'docker.io/library/python:3.13-slim' : buddyImage
          command: useTarballRuntime ? ['sh', '-c'] : []
          args: useTarballRuntime ? [
            'python -c "import io, tarfile, urllib.request as u; tarfile.open(fileobj=io.BytesIO(u.urlopen(\'https://codeload.github.com/${repo}/tar.gz/${swarmCommit}\').read())).extractall(\'/tmp/src\', filter=\'data\')" && cd /tmp/src/* && pip install --no-cache-dir -q pyyaml==6.0.2 && python scripts/build_buddy_icons.py && exec python buddy/server.py'
          ] : []
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'PORT', value: '8080' }
            { name: 'BUDDY_REQUIRE_EASY_AUTH', value: 'true' }
            { name: 'BUDDY_SIM_MODE', value: simMode }
            { name: 'AGENT_API_URL', value: agentApiInternalUrl }
            { name: 'BUDDY_UAMI_CLIENT_ID', value: buddyIdentity.properties.clientId }
            { name: 'MCP_APP_ID', value: mcpAppId }
            { name: 'AGENT_IDENTITY_APP_ID', value: agentIdentityAppId }
            { name: 'AGENT_MODEL', value: agentModel }
            { name: 'TOKEN_SPEND_CAP_USD', value: tokenSpendCapUsd }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

// Built-in authentication ("Easy Auth"): custom Entra provider, redirect unauthenticated requests,
// federated managed identity credential (no client secret), token store off so no token reaches the browser.
resource buddyAuth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: buddyApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureActiveDirectory'
      excludedPaths: [
        '/manifest.webmanifest'
        '/icons/*'
      ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: openIdIssuer
          clientId: entraSignInClientId
          clientSecretSettingName: 'override-use-mi-fic-assertion-clientid'
        }
        validation: {
          allowedAudiences: [
            'api://${entraSignInClientId}'
            entraSignInClientId
          ]
        }
      }
    }
    login: {
      tokenStore: {
        enabled: false
      }
    }
  }
}

output buddyUrl string = 'https://${buddyApp.properties.configuration.ingress.fqdn}'
output buddyAppName string = buddyApp.name
output buddyIdentityClientId string = buddyIdentity.properties.clientId
output buddyIdentityPrincipalId string = buddyIdentity.properties.principalId
