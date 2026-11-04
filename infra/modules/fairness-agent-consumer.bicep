param fairnessPrincipalId string
param aiServicesName string
param projectName string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiServicesName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}
// This documented data-plane RBAC scope has no ARM resource schema; only its ID is used.
#disable-next-line BCP081
resource agent 'Microsoft.CognitiveServices/accounts/projects/agents@2025-04-01-preview' existing = {
  parent: project
  name: 'gg-fairness-explainability'
}
resource fairnessConsumer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: agent
  name: guid(agent.id, fairnessPrincipalId, 'eed3b665-ab3a-47b6-8f48-c9382fb1dad6')
  properties: {
    principalId: fairnessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'eed3b665-ab3a-47b6-8f48-c9382fb1dad6')
    description: 'IMP-33 approved protective fairness agent endpoint only; no agent mutations.'
  }
}
