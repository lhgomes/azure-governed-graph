param location string
param searchLocation string
param tags object
param token string
param privateEndpointSubnetId string
param virtualNetworkId string
param deploySyntheticContactCentre bool = false
param deployAdminConsole bool = false

resource st 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stgg${take(token, 16)}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: st
  name: 'default'
  properties: { isVersioningEnabled: true }
}

// Create-only per-response records and legacy append blobs share WORM retention (DPO decision);
// the policy starts UNLOCKED so it can be tuned, and is locked as a pre-launch readiness decision.
resource records 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'explainability-records'
}
resource recordsWorm 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = {
  parent: records
  name: 'default'
  properties: { immutabilityPeriodSinceCreationInDays: 30, allowProtectedAppendWrites: true }
}
resource learningAggregates 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'learning-aggregates'
  properties: { publicAccess: 'None' }
}
// Administration journal: one create-only blob per action step, immutable like the response audit records.
resource adminActions 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (deployAdminConsole) {
  parent: blob
  name: 'admin-actions'
  properties: { publicAccess: 'None' }
}
// Gap-review proposals: written once by the gap-review job, read by the administration console.
resource improvementProposals 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (deployAdminConsole) {
  parent: blob
  name: 'improvement-proposals'
  properties: { publicAccess: 'None' }
}
// Member questions that ended without an explanation, kept for the gap review. Not immutable, so an erasure request
// can be honoured; written by the runtime, read only by the gap review, expired by the lifecycle rule below.
resource declinedQuestions 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (deployAdminConsole) {
  parent: blob
  name: 'declined-questions'
  properties: { publicAccess: 'None' }
}
resource improvementLifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = if (deployAdminConsole) {
  parent: st
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-declined-questions'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: [ 'blockBlob' ], prefixMatch: [ 'declined-questions/' ] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 30 } } }
          }
        }
        {
          name: 'expire-improvement-proposals'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: [ 'blockBlob' ], prefixMatch: [ 'improvement-proposals/' ] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 90 } } }
          }
        }
      ]
    }
  }
}
// Owner-approved passages awaiting the content publisher, and its publication markers. Create-only, enforced by retention.
resource contentApprovals 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (deployAdminConsole) {
  parent: blob
  name: 'content-approvals'
  properties: { publicAccess: 'None' }
}
resource contentApprovalsWorm 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = if (deployAdminConsole) {
  parent: contentApprovals
  name: 'default'
  properties: { immutabilityPeriodSinceCreationInDays: 30, allowProtectedAppendWrites: false }
}
resource adminActionsWorm 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = if (deployAdminConsole) {
  parent: adminActions
  name: 'default'
  properties: { immutabilityPeriodSinceCreationInDays: 30, allowProtectedAppendWrites: false }
}
resource contentStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'ctgg${take(token, 16)}'
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
resource contentDns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
}
resource contentDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: contentDns
  name: 'governedgraph'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: virtualNetworkId } }
}
resource contentEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-gg-content-${token}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      { name: 'content', properties: { privateLinkServiceId: contentStorage.id, groupIds: [ 'blob' ] } }
    ]
  }
}
resource contentDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: contentEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [ { name: 'blob', properties: { privateDnsZoneId: contentDns.id } } ]
  }
}
resource learningEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-gg-learning-${token}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      { name: 'learning', properties: { privateLinkServiceId: st.id, groupIds: [ 'blob' ] } }
    ]
  }
}
resource learningDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: learningEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [ { name: 'blob', properties: { privateDnsZoneId: contentDns.id } } ]
  }
}
resource contentBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: contentStorage
  name: 'default'
  properties: { isVersioningEnabled: true }
}
resource approvedContent 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: contentBlobs
  name: 'approved-content'
  properties: { publicAccess: 'None' }
}
// Learning outputs are not P3 board-approved content; isolate permissions per container.
resource rankingState 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: contentBlobs
  name: 'ranking-snapshots'
  properties: { publicAccess: 'None' }
}
resource journeyState 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: contentBlobs
  name: 'journey-state'
  properties: { publicAccess: 'None' }
}
resource journeyInbox 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: contentBlobs
  name: 'journey-inbox'
  properties: { publicAccess: 'None' }
}
resource handovers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (deploySyntheticContactCentre) {
  parent: contentBlobs
  name: 'handover-records'
  properties: { publicAccess: 'None' }
}
resource cohortZone 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: contentBlobs
  name: 'cohort-zone'
  properties: { publicAccess: 'None' }
}
resource learningAudits 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: contentBlobs
  name: 'learning-audits'
  properties: { publicAccess: 'None' }
}
resource p2Artifacts 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: contentBlobs
  name: 'p2-artifacts'
  properties: { publicAccess: 'None' }
}
resource learningEvidence 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: contentBlobs
  name: 'learning-evidence'
  properties: { publicAccess: 'None' }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: 'srch-gg-${token}'
  location: searchLocation
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    semanticSearch: 'standard'
    disableLocalAuth: true
    publicNetworkAccess: 'enabled'
  }
}
resource contentSharedLink 'Microsoft.Search/searchServices/sharedPrivateLinkResources@2023-11-01' = {
  parent: search
  name: 'approved-content-blob'
  properties: {
    groupId: 'blob'
    privateLinkResourceId: contentStorage.id
    requestMessage: 'GovernedGraph approved-content indexer - owner approval required'
  }
}

resource sb 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: 'sb-gg-${token}'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: { disableLocalAuth: true, minimumTlsVersion: '1.2' }
}
resource qJourney 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: sb
  name: 'journey-events'
  properties: { requiresSession: true, requiresDuplicateDetection: false, deadLetteringOnMessageExpiration: true, maxDeliveryCount: 10 }
}
resource qEsc 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: sb
  name: 'escalations'
  properties: { requiresDuplicateDetection: false, deadLetteringOnMessageExpiration: true }
}

output storageId string = st.id
output storageName string = st.name
output blobEndpoint string = st.properties.primaryEndpoints.blob
output contentStorageId string = contentStorage.id
output contentStorageName string = contentStorage.name
output contentBlobEndpoint string = contentStorage.properties.primaryEndpoints.blob
output searchName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchPrincipalId string = search.identity.principalId
output contentSharedLinkId string = contentSharedLink.id
output serviceBusName string = sb.name
output serviceBusId string = sb.id
output serviceBusFqdn string = '${sb.name}.servicebus.windows.net'
