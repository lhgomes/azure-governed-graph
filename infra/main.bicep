// Foundation only: no container workloads, agent versions or member-facing routes.
targetScope = 'subscription'
param environmentName string
param location string
param searchLocation string = location
param resourceGroupName string
param namingSeed string
param principalId string
param publisherEmail string
param deployDevIssuer bool = false
param deploySyntheticContactCentre bool = false
param deployAdminConsole bool = false
param counsellorModelName string = 'gpt-4.1'
param counsellorModelVersion string = '2025-04-14'
param compactModelName string = 'gpt-4.1-mini'
param compactModelVersion string = '2025-04-14'
param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: { 'azd-env-name': environmentName, workload: 'gg-journey-graph', data: 'synthetic' }
}
var token = empty(namingSeed)
  ? toLower(uniqueString(subscription().id, rg.id, environmentName))
  : toLower(uniqueString(subscription().id, rg.id, environmentName, namingSeed))
var tags = { 'azd-env-name': environmentName, workload: 'gg-journey-graph', data: 'synthetic' }
module network 'modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: { location: location, tags: tags, token: token }
}
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: { location: location, tags: tags, token: token }
}
module security 'modules/security.bicep' = {
  name: 'security'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    deployDevIssuer: deployDevIssuer
    deploySyntheticContactCentre: deploySyntheticContactCentre
    deployAdminConsole: deployAdminConsole
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    virtualNetworkId: network.outputs.virtualNetworkId
  }
}
module data 'modules/data.bicep' = {
  name: 'data'
  scope: rg
  params: {
    location: location
    searchLocation: searchLocation
    tags: tags
    token: token
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    virtualNetworkId: network.outputs.virtualNetworkId
    deploySyntheticContactCentre: deploySyntheticContactCentre
    deployAdminConsole: deployAdminConsole
  }
}
module ai 'modules/ai.bicep' = {
  name: 'ai'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    counsellorModelName: counsellorModelName
    counsellorModelVersion: counsellorModelVersion
    compactModelName: compactModelName
    compactModelVersion: compactModelVersion
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
    appInsightsId: monitoring.outputs.appInsightsId
    appInsightsRouting: monitoring.outputs.appInsightsRouting
  }
}
module compute 'modules/compute-foundation.bicep' = {
  name: 'compute-foundation'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    infrastructureSubnetId: network.outputs.infrastructureSubnetId
    virtualNetworkId: network.outputs.virtualNetworkId
  }
}
module gateway 'modules/gateway-foundation.bicep' = {
  name: 'gateway-foundation'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    publisherEmail: publisherEmail
    apimSubnetId: network.outputs.apimSubnetId
  }
}
module learning 'modules/learning.bicep' = {
  name: 'learning'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    aggregateStorageId: data.outputs.storageId
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    pipelinePrincipalId: security.outputs.pipelinePrincipalId
    fairnessPrincipalId: security.outputs.fairnessPrincipalId
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    virtualNetworkId: network.outputs.virtualNetworkId
    deployFabric: false
    fabricAdminUpn: ''
    mlArtifactContainers: []
  }
}
module rbac 'modules/rbac.bicep' = {
  name: 'rbac'
  scope: rg
  params: {
    runtimePrincipalId: security.outputs.runtimePrincipalId
    pipelinePrincipalId: security.outputs.pipelinePrincipalId
    fairnessPrincipalId: security.outputs.fairnessPrincipalId
    developerPrincipalId: principalId
    keyVaultName: security.outputs.keyVaultName
    storageName: data.outputs.storageName
    contentStorageName: data.outputs.contentStorageName
    searchName: data.outputs.searchName
    searchPrincipalId: data.outputs.searchPrincipalId
    contentPublisherPrincipalId: security.outputs.contentPublisherPrincipalId
    registryName: compute.outputs.registryName
    aiServicesName: ai.outputs.aiServicesName
    foundryProjectName: last(split(ai.outputs.projectEndpoint, '/'))
    contentSafetyName: ai.outputs.contentSafetyName
    serviceBusName: data.outputs.serviceBusName
    strategyPrincipalId: security.outputs.strategyPrincipalId
    appInsightsName: last(split(monitoring.outputs.appInsightsId, '/'))
    deployDevIssuer: deployDevIssuer
    devIssuerPrincipalId: security.outputs.devIssuerPrincipalId
    deploySyntheticContactCentre: deploySyntheticContactCentre
    contactCentrePrincipalId: security.outputs.contactCentrePrincipalId
    deployAdminConsole: deployAdminConsole
    adminPrincipalId: security.outputs.adminPrincipalId
  }
}
output foundation object = {
  token: token
  network: {
    virtualNetworkId: network.outputs.virtualNetworkId
    infrastructureSubnetId: network.outputs.infrastructureSubnetId
    apimSubnetId: network.outputs.apimSubnetId
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    mlComputeSubnetId: network.outputs.mlComputeSubnetId
  }
  monitoring: {
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    logAnalyticsName: monitoring.outputs.logAnalyticsName
    logAnalyticsCustomerId: monitoring.outputs.logAnalyticsCustomerId
    appInsightsId: monitoring.outputs.appInsightsId
    appInsightsRouting: monitoring.outputs.appInsightsRouting
  }
  security: {
    runtimeIdentityId: security.outputs.runtimeIdentityId
    runtimeIdentityClientId: security.outputs.runtimeIdentityClientId
    runtimePrincipalId: security.outputs.runtimePrincipalId
    memberValuesIdentityId: security.outputs.memberValuesIdentityId
    memberValuesClientId: security.outputs.memberValuesClientId
    memberValuesPrincipalId: security.outputs.memberValuesPrincipalId
    pipelineIdentityId: security.outputs.pipelineIdentityId
    pipelinePrincipalId: security.outputs.pipelinePrincipalId
    pipelineClientId: security.outputs.pipelineClientId
    fairnessIdentityId: security.outputs.fairnessIdentityId
    fairnessPrincipalId: security.outputs.fairnessPrincipalId
    fairnessClientId: security.outputs.fairnessClientId
    strategyIdentityId: security.outputs.strategyIdentityId
    strategyClientId: security.outputs.strategyClientId
    strategyPrincipalId: security.outputs.strategyPrincipalId
    contentPublisherIdentityId: security.outputs.contentPublisherIdentityId
    contentPublisherClientId: security.outputs.contentPublisherClientId
    contentPublisherPrincipalId: security.outputs.contentPublisherPrincipalId
    keyVaultName: security.outputs.keyVaultName
    keyVaultUri: security.outputs.keyVaultUri
    signingKeyName: security.outputs.signingKeyName
    devIssuerIdentityId: security.outputs.devIssuerIdentityId
    devIssuerPrincipalId: security.outputs.devIssuerPrincipalId
    devIssuerClientId: security.outputs.devIssuerClientId
    contactCentreIdentityId: security.outputs.contactCentreIdentityId
    contactCentreClientId: security.outputs.contactCentreClientId
    contactCentrePrincipalId: security.outputs.contactCentrePrincipalId
    adminIdentityId: security.outputs.adminIdentityId
    adminClientId: security.outputs.adminClientId
    adminPrincipalId: security.outputs.adminPrincipalId
  }
  data: {
    storageId: data.outputs.storageId
    storageName: data.outputs.storageName
    blobEndpoint: data.outputs.blobEndpoint
    contentStorageId: data.outputs.contentStorageId
    contentStorageName: data.outputs.contentStorageName
    contentBlobEndpoint: data.outputs.contentBlobEndpoint
    searchName: data.outputs.searchName
    searchEndpoint: data.outputs.searchEndpoint
    contentSharedLinkId: data.outputs.contentSharedLinkId
    serviceBusId: data.outputs.serviceBusId
    serviceBusFqdn: data.outputs.serviceBusFqdn
  }
  ai: {
    aiServicesName: ai.outputs.aiServicesName
    aiServicesEndpoint: ai.outputs.aiServicesEndpoint
    contentSafetyEndpoint: ai.outputs.contentSafetyEndpoint
    embeddingEndpoint: ai.outputs.embeddingEndpoint
    projectEndpoint: ai.outputs.projectEndpoint
    projectResourceId: ai.outputs.projectResourceId
  }
  learning: {
    mlWorkspaceName: learning.outputs.mlWorkspaceName
    mlStorageId: learning.outputs.mlStorageId
    mlRegistryId: learning.outputs.mlRegistryId
    mlVaultId: learning.outputs.mlVaultId
    mlAppInsightsId: learning.outputs.mlAppInsightsId
    workspacePrincipalId: learning.outputs.workspacePrincipalId
  }
  compute: {
    registryName: compute.outputs.registryName
    registryLoginServer: compute.outputs.registryLoginServer
    environmentId: compute.outputs.environmentId
  }
  gateway: { gatewayUrl: gateway.outputs.gatewayUrl }
}
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = compute.outputs.registryLoginServer
output AZURE_AI_PROJECT_ENDPOINT string = ai.outputs.projectEndpoint
output APIM_GATEWAY_URL string = gateway.outputs.gatewayUrl
