targetScope = 'subscription'
param resourceGroupName string
param location string
param foundation object
param runtimeImage string
param probeImage string
param fixturesImage string
param domainEnv object
param memberTokenIssuer string
param deployDevIssuer bool = false
param deployAdminConsole bool = false

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: resourceGroupName
}
var publisherEnv = union(domainEnv, {
  GOVERNEDGRAPH_MODE: 'azure'
  AZURE_SEARCH_ENDPOINT: foundation.data.searchEndpoint
  AZURE_SEARCH_INDEX: 'approved-content'
  CONTENT_STORAGE_URL: foundation.data.contentBlobEndpoint
  CONTENT_STORAGE_RESOURCE_ID: foundation.data.contentStorageId
  CONTENT_CONTAINER: 'approved-content'
  EXPLAINABILITY_STORAGE_URL: foundation.data.blobEndpoint
  GOVERNEDGRAPH_APPROVALS_CONTAINER: 'content-approvals'
  AZURE_OPENAI_EMBEDDING_ENDPOINT: foundation.ai.embeddingEndpoint
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT: 'embedding'
  AZURE_OPENAI_EMBEDDING_MODEL: 'text-embedding-3-large'
})

module publisher 'modules/content-publisher.bicep' = {
  name: 'content-publisher'
  scope: rg
  params: {
    location: location
    token: foundation.token
    image: runtimeImage
    identityId: foundation.security.contentPublisherIdentityId
    identityClientId: foundation.security.contentPublisherClientId
    registryServer: foundation.compute.registryLoginServer
    env: publisherEnv
  }
}
// Reuse the restricted P3 identity. Journal validation and base-version checks remain in the publisher.
module approvedReconciler 'modules/approved-content-reconciler.bicep' = if (deployAdminConsole) {
  name: 'approved-content-reconciler'
  scope: rg
  params: {
    location: location
    token: foundation.token
    image: runtimeImage
    identityId: foundation.security.contentPublisherIdentityId
    identityClientId: foundation.security.contentPublisherClientId
    registryServer: foundation.compute.registryLoginServer
    env: publisherEnv
  }
}
module issuer 'modules/dev-member-issuer.bicep' = if (deployDevIssuer) {
  name: 'dev-member-issuer'
  scope: rg
  params: {
    location: location
    token: foundation.token
    image: probeImage
    issuerIdentityId: foundation.security.devIssuerIdentityId
    issuerClientId: foundation.security.devIssuerClientId
    memberTokenIssuer: memberTokenIssuer
    domainEnv: domainEnv
  }
}
module fixtures 'modules/dev-learning-fixtures.bicep' = {
  name: 'dev-learning-fixtures'
  scope: rg
  params: {
    location: location
    token: foundation.token
    environmentName: 'gg-dev'
    deployDevFixtures: true
    image: fixturesImage
    recordsStorageName: foundation.data.storageName
    rankingStorageName: foundation.data.contentStorageName
    domainEnv: domainEnv
  }
}
output contentJob string = publisher.outputs.name
output approvalsReconcilerJob string = deployAdminConsole ? 'job-gg-approved-reconciler' : ''
output issuerJob string = 'job-gg-dev-member-issuer'
output fixturesJob string = 'job-gg-dev-learning-fixtures'
