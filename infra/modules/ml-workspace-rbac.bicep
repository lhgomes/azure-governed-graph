param workspaceName string
param workspacePrincipalId string
param storageName string
param vaultName string
param registryName string
param appInsightsName string

resource workspace 'Microsoft.MachineLearningServices/workspaces@2024-10-01-preview' existing = {
  name: workspaceName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}
resource mlVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: vaultName
}
resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: registryName
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

var contributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
var reader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
var storageContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '17d1049b-9a84-46fb-8f53-869881c3d3ab')
var vaultContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f25e0fa2-a7c8-4377-a976-54943a77a395')

// Azure ML owns its automatic Blob/File data and operational-vault grants.
// Duplicating those assignments under deterministic names causes RoleAssignmentExists.

resource workspaceSelf 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: workspace
  name: guid(workspace.id, workspacePrincipalId, contributor)
  properties: { principalId: workspacePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: contributor }
}
resource workspaceStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, workspacePrincipalId, storageContributor)
  properties: { principalId: workspacePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: storageContributor }
}
resource workspaceVaultMetadata 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: mlVault
  name: guid(mlVault.id, workspacePrincipalId, vaultContributor)
  properties: { principalId: workspacePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: vaultContributor }
}
resource workspaceRegistry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, workspacePrincipalId, contributor)
  properties: { principalId: workspacePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: contributor }
}
resource workspaceInsights 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, workspacePrincipalId, reader)
  properties: { principalId: workspacePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: reader }
}
