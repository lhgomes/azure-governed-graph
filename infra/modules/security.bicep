// Separate identities per plane (least privilege):
//  runtime  - member-facing agents + services (read values, read snapshot, write explainability, VERIFY snapshots)
//  pipeline - P2 ranking pipeline (the ONLY identity that can SIGN snapshots)
//  fairness - Fairness & Explainability Agent (cohort zone read, suspend, open case)
param location string
param tags object
param token string
param deployDevIssuer bool = false
param deploySyntheticContactCentre bool = false
param privateEndpointSubnetId string
param virtualNetworkId string

var signingKeyRotationPolicy = {
  lifetimeActions: [
    {
      action: { type: 'notify' }
      trigger: { timeBeforeExpiry: 'P30D' }
    }
  ]
}

resource runtimeId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-gg-runtime-${token}'
  location: location
  tags: tags
}
resource pipelineId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-gg-p2-pipeline-${token}'
  location: location
  tags: tags
}
resource fairnessId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-gg-fairness-${token}'
  location: location
  tags: tags
}
resource contentPublisherId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-gg-p3-content-${token}'
  location: location
  tags: tags
}
resource strategyId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-gg-strategy-${token}'
  location: location
  tags: tags
}
resource devIssuerId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (deployDevIssuer) {
  name: 'id-gg-dev-issuer-${token}'
  location: location
  tags: union(tags, { purpose: 'dev-only-member-issuer' })
}
resource contactCentreId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (deploySyntheticContactCentre) {
  name: 'id-gg-contact-centre-${token}'
  location: location
  tags: union(tags, { purpose: 'dev-only-synthetic-contact-centre' })
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-gg-${take(token, 13)}'
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

resource vaultDns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
}

resource vaultDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: vaultDns
  name: 'governedgraph'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: virtualNetworkId } }
}

resource vaultEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-gg-vault-${token}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'vault'
        properties: { privateLinkServiceId: kv.id, groupIds: [ 'vault' ] }
      }
    ]
  }
}

resource vaultDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: vaultEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [ { name: 'vault', properties: { privateDnsZoneId: vaultDns.id } } ]
  }
}

resource signingKey 'Microsoft.KeyVault/vaults/keys@2023-07-01' = {
  parent: kv
  name: 'ranking-snapshot-signing'
  properties: {
    kty: 'RSA'
    keySize: 3072
    keyOps: [ 'sign', 'verify' ]
    rotationPolicy: signingKeyRotationPolicy
  }
}
resource devMemberKey 'Microsoft.KeyVault/vaults/keys@2023-07-01' = if (deployDevIssuer) {
  parent: kv
  name: 'dev-member-token-signing'
  properties: {
    kty: 'RSA'
    keySize: 3072
    keyOps: [ 'sign', 'verify' ]
    rotationPolicy: signingKeyRotationPolicy
  }
}

output runtimeIdentityId string = runtimeId.id
output runtimeIdentityClientId string = runtimeId.properties.clientId
output runtimePrincipalId string = runtimeId.properties.principalId
output pipelineIdentityId string = pipelineId.id
output pipelinePrincipalId string = pipelineId.properties.principalId
output fairnessPrincipalId string = fairnessId.properties.principalId
output fairnessIdentityId string = fairnessId.id
output fairnessClientId string = fairnessId.properties.clientId
output strategyIdentityId string = strategyId.id
output strategyClientId string = strategyId.properties.clientId
output strategyPrincipalId string = strategyId.properties.principalId
output pipelineClientId string = pipelineId.properties.clientId
output contentPublisherIdentityId string = contentPublisherId.id
output contentPublisherClientId string = contentPublisherId.properties.clientId
output contentPublisherPrincipalId string = contentPublisherId.properties.principalId
output keyVaultId string = kv.id
output keyVaultName string = kv.name
output keyVaultUri string = kv.properties.vaultUri
output signingKeyName string = signingKey.name
output devIssuerIdentityId string = deployDevIssuer ? devIssuerId!.id : ''
output devIssuerPrincipalId string = deployDevIssuer ? devIssuerId!.properties.principalId : ''
output devIssuerClientId string = deployDevIssuer ? devIssuerId!.properties.clientId : ''
output contactCentreIdentityId string = deploySyntheticContactCentre ? contactCentreId!.id : ''
output contactCentreClientId string = deploySyntheticContactCentre ? contactCentreId!.properties.clientId : ''
output contactCentrePrincipalId string = deploySyntheticContactCentre ? contactCentreId!.properties.principalId : ''
