param workspaceName string
param pipelinePrincipalId string

resource workspace 'Microsoft.MachineLearningServices/workspaces@2024-10-01-preview' existing = {
  name: workspaceName
}

resource jobStatusReader 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(workspace.id, 'gg-p2-job-status-reader')
  properties: {
    roleName: 'GovernedGraph P2 Job Status Reader ${uniqueString(workspace.id)}'
    description: 'Read workspace and job status metadata for the protective P2 failure watchdog; no writes or data-plane access.'
    type: 'CustomRole'
    assignableScopes: [ resourceGroup().id ]
    permissions: [
      {
        actions: [
          'Microsoft.MachineLearningServices/workspaces/read'
          'Microsoft.MachineLearningServices/workspaces/jobs/read'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

resource monitorAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: workspace
  name: guid(workspace.id, pipelinePrincipalId, jobStatusReader.id)
  properties: {
    principalId: pipelinePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: jobStatusReader.id
  }
}
