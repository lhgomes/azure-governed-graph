// Optional, off by default: lets the administration console read captured synthetic conversation spans.
// Scope is the single Log Analytics workspace; enable only after a human reviews that access.
param workspaceName string
param adminPrincipalId string

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = { name: workspaceName }

var logAnalyticsReader = '73c42c96-874c-492b-b04d-ab87d138a893'
resource adminTraceRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: workspace
  name: guid(workspace.id, adminPrincipalId, logAnalyticsReader)
  properties: {
    principalId: adminPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsReader)
    description: 'Administration console read-only query of captured synthetic conversation traces.'
  }
}
