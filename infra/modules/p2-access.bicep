// Container-scoped checkpoint/lease access; independent audit and signing permissions remain unchanged.
param storageName string
param aggregateStorageName string
param registryName string
param pipelinePrincipalId string
param fairnessPrincipalId string
resource artifacts 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  name: '${storageName}/default/p2-artifacts'
}
resource aggregates 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  name: '${aggregateStorageName}/default/learning-aggregates'
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
var contributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var reader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var pull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource artifactAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in [pipelinePrincipalId, fairnessPrincipalId]: {
  scope: artifacts
  name: guid(artifacts.id, principal, contributor)
  properties: {
    principalId: principal
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributor)
  }
}]
resource aggregateRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: aggregates
  name: guid(aggregates.id, pipelinePrincipalId, reader)
  properties: {
    principalId: pipelinePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', reader)
  }
}
resource imagePull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in [pipelinePrincipalId, fairnessPrincipalId]: {
  scope: registry
  name: guid(registry.id, principal, pull)
  properties: {
    principalId: principal
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', pull)
  }
}]
