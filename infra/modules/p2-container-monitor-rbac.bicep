param pipelinePrincipalId string
param recoveryReaderPrincipalId string = ''
resource pipeline 'Microsoft.App/jobs@2024-03-01' existing = {
  name: 'job-gg-p2-pipeline'
}
resource fairness 'Microsoft.App/jobs@2024-03-01' existing = {
  name: 'job-gg-p2-fairness'
}
resource reader 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(pipeline.id, 'gg-p2-execution-status-reader')
  properties: {
    roleName: 'GovernedGraph P2 Execution Status Reader ${uniqueString(pipeline.id)}'
    description: 'Read execution metadata on dedicated P2 jobs only; no start, override, log, secret or data access.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: ['Microsoft.App/jobs/executions/read']
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}
resource pipelineRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: pipeline
  name: guid(pipeline.id, pipelinePrincipalId, reader.id)
  properties: { principalId: pipelinePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: reader.id }
}
resource fairnessRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: fairness
  name: guid(fairness.id, pipelinePrincipalId, reader.id)
  properties: { principalId: pipelinePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: reader.id }
}
resource humanRecoveryRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(recoveryReaderPrincipalId)) {
  scope: fairness
  name: guid(fairness.id, recoveryReaderPrincipalId, reader.id)
  properties: { principalId: recoveryReaderPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: reader.id }
}
