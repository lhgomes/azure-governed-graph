// Plane P2 (offline bounded learning): Azure ML workspace for ranking + Fairlearn audit pipelines;
// optional Fabric capacity for OneLake journey aggregates and the network-isolated cohort zone.
param location string
param tags object
param token string
param aggregateStorageId string
param logAnalyticsId string
param pipelinePrincipalId string
param fairnessPrincipalId string
param privateEndpointSubnetId string
param virtualNetworkId string
param deployFabric bool
param fabricAdminUpn string
param mlArtifactContainers array

resource aggregateStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: last(split(aggregateStorageId, '/'))
}
resource mlInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-ml-gg-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsId
    DisableLocalAuth: true
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'mlgg${token}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Disabled'
  }
}
resource mlVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-ml-gg-${token}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    publicNetworkAccess: 'Disabled'
  }
}
resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' = {
  name: 'crmlgg${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false, anonymousPullEnabled: false }
}
resource vaultDns 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: 'privatelink.vaultcore.azure.net'
}
resource blobDns 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
}
resource fileDns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.file.${environment().suffixes.storage}'
  location: 'global'
}
resource fileDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: fileDns
  name: 'governedgraph'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: virtualNetworkId } }
}
var privateTargets = [
  { name: 'ml-vault', resourceId: mlVault.id, group: 'vault', dnsId: vaultDns.id }
  { name: 'ml-blob', resourceId: storage.id, group: 'blob', dnsId: blobDns.id }
  { name: 'ml-file', resourceId: storage.id, group: 'file', dnsId: fileDns.id }
]
resource endpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [for target in privateTargets: {
  name: 'pe-gg-${target.name}-${token}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      { name: target.name, properties: { privateLinkServiceId: target.resourceId, groupIds: [ target.group ] } }
    ]
  }
}]
resource dnsGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [for (target, i) in privateTargets: {
  parent: endpoints[i]
  name: 'default'
  properties: { privateDnsZoneConfigs: [ { name: target.group, properties: { privateDnsZoneId: target.dnsId } } ] }
}]
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource artifactContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = [for container in mlArtifactContainers: {
  parent: blobService
  name: container
}]
resource artifactAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (container, i) in mlArtifactContainers: {
  scope: artifactContainers[i]
  name: guid(artifactContainers[i].id, pipelinePrincipalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  properties: {
    principalId: pipelinePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}]
resource fairnessArtifactAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (container, i) in mlArtifactContainers: {
  scope: artifactContainers[i]
  name: guid(artifactContainers[i].id, fairnessPrincipalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  properties: {
    principalId: fairnessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}]
resource computePull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in [pipelinePrincipalId, fairnessPrincipalId]: {
  scope: registry
  name: guid(registry.id, principal, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  properties: {
    principalId: principal
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}]
resource aggregates 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  name: '${aggregateStorage.name}/default/learning-aggregates'
}
resource aggregateAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: aggregates
  name: guid(aggregates.id, pipelinePrincipalId, '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  properties: {
    principalId: pipelinePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  }
}

// This verified API retains the opt-out that newer workspace schemas omit.
resource mlw 'Microsoft.MachineLearningServices/workspaces@2024-10-01-preview' = {
  name: 'mlw-gg-${token}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    friendlyName: 'GovernedGraph P2 bounded ranking'
    storageAccount: storage.id
    keyVault: mlVault.id
    containerRegistry: registry.id
    applicationInsights: mlInsights.id
    systemDatastoresAuthMode: 'Identity'
    allowRoleAssignmentOnRG: false
    publicNetworkAccess: 'Enabled'
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'gg-p2-diagnostics'
  scope: mlw
  properties: {
    workspaceId: logAnalyticsId
    logs: [
      { category: 'AmlComputeJobEvent', enabled: true }
      { category: 'AmlComputeClusterEvent', enabled: true }
      { category: 'AmlRunStatusChangedEvent', enabled: true }
    ]
  }
}

module workspaceNetwork 'ml-workspace-network.bicep' = {
  name: 'ml-workspace-network'
  params: {
    location: location
    tags: tags
    token: token
    workspaceId: mlw.id
    subnetId: privateEndpointSubnetId
    virtualNetworkId: virtualNetworkId
  }
}
// Pass the new principal into a nested deployment so role names are known at its start.
module workspaceRoles 'ml-workspace-rbac.bicep' = {
  name: 'ml-workspace-roles'
  params: {
    workspaceName: mlw.name
    workspacePrincipalId: mlw.identity.principalId
    storageName: storage.name
    vaultName: mlVault.name
    registryName: registry.name
    appInsightsName: mlInsights.name
  }
}
resource fabric 'Microsoft.Fabric/capacities@2023-11-01' = if (deployFabric) {
  name: 'fabgg${take(token, 12)}'
  location: location
  tags: tags
  sku: { name: 'F2', tier: 'Fabric' }
  properties: { administration: { members: [ fabricAdminUpn ] } }
}

output mlWorkspaceName string = mlw.name
output mlStorageId string = storage.id
output mlRegistryId string = registry.id
output mlVaultId string = mlVault.id
output mlAppInsightsId string = mlInsights.id
output workspacePrincipalId string = mlw.identity.principalId
