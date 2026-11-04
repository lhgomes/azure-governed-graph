param fairnessPrincipalId string
param aiServicesName string
param projectName string
param storageName string

module agentConsumer 'fairness-agent-consumer.bicep' = {
  name: 'fairness-single-agent-consumer'
  params: {
    fairnessPrincipalId: fairnessPrincipalId
    aiServicesName: aiServicesName
    projectName: projectName
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiServicesName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource records 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobs
  name: 'explainability-records'
}
resource invocationRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(project.id, 'gg-fairness-invoker')
  properties: {
    roleName: 'GovernedGraph Fairness Invoker ${uniqueString(project.id)}'
    description: 'Read pinned agent definitions and endpoint selectors only; no response or agent mutations.'
    type: 'CustomRole'
    assignableScopes: [ resourceGroup().id ]
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.CognitiveServices/accounts/AIServices/agents/read'
        ]
        notDataActions: []
      }
    ]
  }
}
resource fairnessInvocation 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: project
  name: guid(project.id, fairnessPrincipalId, invocationRole.id)
  properties: {
    principalId: fairnessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: invocationRole.id
    description: 'IMP-33/IMP-34 approved fairness-only pinned-agent invocation.'
  }
}
resource fairnessRecordsRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: records
  name: guid(records.id, fairnessPrincipalId, '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  properties: {
    principalId: fairnessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
    description: 'IMP-33/IMP-34 approved host-only explainability report reads.'
  }
}
