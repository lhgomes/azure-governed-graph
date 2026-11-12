// Bootstrap separately with an Azure operator. Never grant CI identities access
// to production data, the synthetic member-token issuer or existing runtime ACR.
targetScope = 'resourceGroup'

@allowed(['gg-dev'])
param environmentName string = 'gg-dev'
param location string = resourceGroup().location
@description('Use the exact namingSeed applied by infra/main.bicep. Empty preserves the original names.')
@maxLength(36)
param namingSeed string = ''
@description('Trusted repository; workflow environment must be protected with required reviewers.')
param githubRepository string = 'lhgomes/azure-governed-graph'

var token = empty(namingSeed)
  ? toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
  : toLower(uniqueString(subscription().id, resourceGroup().id, environmentName, namingSeed))
var tags = {
  'azd-env-name': environmentName
  purpose: 'azure-ci-isolated'
  data: 'synthetic'
}
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var acrTasksContributor = 'fb382eab-e894-4461-af04-94435c366c3f'

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-gg-${token}'
}

// A dedicated registry means PR-authored Docker builds cannot read or overwrite
// the existing application and P2 image repositories.
resource ciRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'crggci${take(token, 16)}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

// Only this identity is federated to GitHub, and only for a protected environment.
resource githubIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-gg-ci-github'
  location: location
  tags: tags
}
resource githubFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: githubIdentity
  name: 'github-gg-ci-environment'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:environment:gg-ci'
    audiences: [ 'api://AzureADTokenExchange' ]
  }
}

// The CI job has NO member-token signing key, runtime identity, secrets,
// storage access, Foundry permissions, or service-bus access.
resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-gg-ci-validation'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 3600
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [
        {
          server: ciRegistry.properties.loginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'validation'
          image: 'mcr.microsoft.com/azure-cli:2.77.0'
          // Accidental runs without the override MUST fail instead of succeeding.
          command: [ '/bin/sh' ]
          args: [ '-c', 'echo missing immutable CI execution image >&2; exit 64' ]
          env: [
            { name: 'AZURE_ENV_NAME', value: 'gg-dev' }
            { name: 'GOVERNEDGRAPH_CI_AZURE_JOB', value: 'true' }
          ]
          resources: { cpu: json('2.0'), memory: '4Gi' }
        }
      ]
    }
  }
}

// Container code can only pull CI-only artifacts, not app/P2 images.
resource jobPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ciRegistry.id, job.identity.principalId, acrPull)
  scope: ciRegistry
  properties: {
    principalId: job.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
  }
}

// Restrict ACR Tasks build authority to the disposable CI registry only.
resource githubTasks 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ciRegistry.id, githubIdentity.properties.principalId, acrTasksContributor)
  scope: ciRegistry
  properties: {
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrTasksContributor)
  }
}
resource githubRegistryRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ciRegistry.id, githubIdentity.properties.principalId, acrPull)
  scope: ciRegistry
  properties: {
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
  }
}

// A dedicated narrow custom role, not Container Apps Contributor / subscription
// Contributor. Microsoft warns that jobs/start may override the container, so
// this role is scoped to the secret-free CI job only.
resource ciJobOperatorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'gg-ci-start-observe-only')
  properties: {
    roleName: 'GG CI Job Start and Observe'
    description: 'Run and read the isolated secret-free Azure CI Container Apps job only.'
    type: 'CustomRole'
    assignableScopes: [ resourceGroup().id ]
    permissions: [
      {
        actions: [
          'Microsoft.App/jobs/read'
          'Microsoft.App/jobs/start/action'
          'Microsoft.App/jobs/executions/read'
          'Microsoft.App/jobs/execution/read'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}
resource githubJobOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(job.id, githubIdentity.properties.principalId, ciJobOperatorRole.id)
  scope: job
  properties: {
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: ciJobOperatorRole.id
  }
}

output GG_CI_AZURE_CLIENT_ID string = githubIdentity.properties.clientId
output GG_CI_AZURE_TENANT_ID string = subscription().tenantId
output GG_CI_AZURE_SUBSCRIPTION_ID string = subscription().subscriptionId
output GG_CI_RESOURCE_GROUP string = resourceGroup().name
output GG_CI_ACR_NAME string = ciRegistry.name
output GG_CI_JOB_NAME string = job.name
