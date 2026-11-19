param location string
param token string
param environmentName string
param deployDevFixtures bool = false
param image string
param recordsStorageName string
param rankingStorageName string
param domainEnv object
var domainEnvList = [for item in items(domainEnv): { name: item.key, value: string(item.value) }]

var enabled = deployDevFixtures && environmentName == 'gg-dev'
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-gg-${token}'
}
resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: 'crgg${take(token, 16)}'
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (enabled) {
  name: 'id-gg-dev-fixtures-${token}'
  location: location
  tags: { data: 'synthetic', purpose: 'dev-only-learning-fixture-publisher' }
}
resource records 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: recordsStorageName
}
resource recordsBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: records
  name: 'default'
}
resource aggregates 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: recordsBlobs
  name: 'learning-aggregates'
}
resource ranking 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: rankingStorageName
}
resource rankingBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: ranking
  name: 'default'
}
resource cohorts 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: rankingBlobs
  name: 'cohort-zone'
}
var blobContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
resource aggregateWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled) {
  scope: aggregates
  name: guid(aggregates.id, identity!.id, blobContributor)
  properties: { principalId: identity!.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobContributor }
}
resource cohortWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled) {
  scope: cohorts
  name: guid(cohorts.id, identity!.id, blobContributor)
  properties: { principalId: identity!.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobContributor }
}
resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled) {
  scope: registry
  name: guid(registry.id, identity!.id, 'AcrPull')
  properties: {
    principalId: identity!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource publisher 'Microsoft.App/jobs@2024-03-01' = if (enabled) {
  name: 'job-gg-dev-learning-fixtures'
  location: location
  tags: { data: 'synthetic', purpose: 'dev-only-learning-fixture-publisher' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity!.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registry.properties.loginServer, identity: identity!.id }]
    }
    template: {
      containers: [{
        name: 'publisher'
        image: image
        command: ['python', '-m', 'governedgraph.dev.publish_learning_fixtures']
        args: ['--initialize-only']
        env: concat(domainEnvList, [
          { name: 'AZURE_CLIENT_ID', value: identity!.properties.clientId }
          { name: 'AZURE_ENV_NAME', value: environmentName }
          { name: 'DEPLOY_DEV_FIXTURES', value: 'true' }
          { name: 'EXPLAINABILITY_STORAGE_URL', value: records.properties.primaryEndpoints.blob }
          { name: 'RANKING_STORAGE_URL', value: ranking.properties.primaryEndpoints.blob }
        ])
        resources: { cpu: json('0.5'), memory: '1Gi' }
      }]
    }
  }
  dependsOn: [aggregateWrite, cohortWrite, pull]
}
