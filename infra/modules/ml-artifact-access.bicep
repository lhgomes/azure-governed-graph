// ML creates datastore containers itself. Reconcile only the verified dedicated containers.
param storageName string
param containers array
param pipelinePrincipalId string
param fairnessPrincipalId string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageName }
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource scheduler 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'azureml'
  properties: { publicAccess: 'None' }
}
resource artifacts 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = [for container in union(containers, ['azureml']): {
  parent: blobs
  name: container
}]
resource pipelineAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (container, i) in union(containers, ['azureml']): {
  scope: artifacts[i]
  name: guid(artifacts[i].id, pipelinePrincipalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  properties: {
    principalId: pipelinePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
  dependsOn: [scheduler]
}]
resource fairnessAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (container, i) in union(containers, ['azureml']): {
  scope: artifacts[i]
  name: guid(artifacts[i].id, fairnessPrincipalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  properties: {
    principalId: fairnessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
  dependsOn: [scheduler]
}]
