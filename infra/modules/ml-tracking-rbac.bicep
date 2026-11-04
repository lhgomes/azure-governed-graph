param workspaceName string
param pipelinePrincipalId string
param fairnessPrincipalId string

resource workspace 'Microsoft.MachineLearningServices/workspaces@2024-10-01-preview' existing = {
  name: workspaceName
}
resource trackingRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(workspace.id, 'gg-ml-tracking-writer')
  properties: {
    roleName: 'GovernedGraph ML Tracking Writer ${uniqueString(workspace.id)}'
    description: 'Experiment/run tracking only; no job submission, compute management, deletion or data access.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: [
          'Microsoft.MachineLearningServices/workspaces/read'
          'Microsoft.MachineLearningServices/workspaces/experiments/read'
          'Microsoft.MachineLearningServices/workspaces/experiments/write'
          'Microsoft.MachineLearningServices/workspaces/experiments/runs/read'
          'Microsoft.MachineLearningServices/workspaces/experiments/runs/write'
          'Microsoft.MachineLearningServices/workspaces/metrics/resource/write'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}
resource assignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in [pipelinePrincipalId, fairnessPrincipalId]: {
  name: guid(workspace.id, principal, trackingRole.id)
  scope: workspace
  properties: {
    principalId: principal
    principalType: 'ServicePrincipal'
    roleDefinitionId: trackingRole.id
  }
}]
