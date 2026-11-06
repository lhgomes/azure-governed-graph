// Least-privilege role assignments. The runtime identity can VERIFY but not SIGN snapshots.
param runtimePrincipalId string
param pipelinePrincipalId string
param fairnessPrincipalId string
param developerPrincipalId string
param keyVaultName string
param storageName string
param contentStorageName string
param searchName string
param searchPrincipalId string
param contentPublisherPrincipalId string
param registryName string
param aiServicesName string
param foundryProjectName string
param contentSafetyName string
param serviceBusName string
param strategyPrincipalId string
param appInsightsName string
param deployDevIssuer bool = false
param devIssuerPrincipalId string = ''
param deploySyntheticContactCentre bool = false
param contactCentrePrincipalId string = ''
param deployAdminConsole bool = false
param adminPrincipalId string = ''

var roles = {
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  blobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  searchIndexReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  searchIndexContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  azureAiUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  sbSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  sbReceiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
}

module fairnessFoundry 'fairness-foundry-rbac.bicep' = {
  name: 'fairness-foundry-access'
  params: {
    fairnessPrincipalId: fairnessPrincipalId
    aiServicesName: aiServicesName
    projectName: foundryProjectName
    storageName: storageName
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: keyVaultName }
resource snapshotKey 'Microsoft.KeyVault/vaults/keys@2023-07-01' existing = {
  parent: kv
  name: 'ranking-snapshot-signing'
}
resource devMemberKey 'Microsoft.KeyVault/vaults/keys@2023-07-01' existing = {
  parent: kv
  name: 'dev-member-token-signing'
}
resource keySignerRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(kv.id, 'gg-key-signer')
  properties: {
    roleName: 'GovernedGraph Key Signer ${uniqueString(kv.id)}'
    description: 'Read the assigned public key and sign only; no verify, encryption or key mutation.'
    type: 'CustomRole'
    assignableScopes: [ resourceGroup().id ]
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [ 'Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/sign/action' ]
        notDataActions: []
      }
    ]
  }
}
resource devIssuerSign 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployDevIssuer) {
  scope: devMemberKey
  name: guid(devMemberKey.id, devIssuerPrincipalId, keySignerRole.id)
  properties: {
    principalId: devIssuerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keySignerRole.id
    description: 'IMP-13 dev-only issuer; member-token key only.'
  }
}
resource st 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageName }
resource search 'Microsoft.Search/searchServices@2023-11-01' existing = { name: searchName }
resource ais 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = { name: aiServicesName }
resource safety 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: contentSafetyName }
resource runtimeSafety 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: safety
  name: guid(safety.id, runtimePrincipalId, roles.cognitiveServicesUser)
  properties: {
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}
resource sb 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = { name: serviceBusName }
resource contentStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: contentStorageName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
resource devIssuerPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployDevIssuer) {
  scope: registry
  name: guid(registry.id, devIssuerPrincipalId, 'dev-issuer-pull')
  properties: {
    principalId: devIssuerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    description: 'IMP-13 dev-only issuer image pull.'
  }
}
resource contentBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: contentStorage
  name: 'default'
}
resource contentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: contentBlobService
  name: 'approved-content'
}
resource rankingContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: contentBlobService
  name: 'ranking-snapshots'
}
var journeyContainerNames = [ 'journey-state', 'journey-inbox' ]
resource journeyContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = [for name in journeyContainerNames: {
  parent: contentBlobService
  name: name
}]
resource runtimeJourneyWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in journeyContainerNames: {
  scope: journeyContainers[i]
  name: guid(journeyContainers[i].id, runtimePrincipalId, roles.blobDataContributor)
  properties: {
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}]
resource handoverContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: contentBlobService
  name: 'handover-records'
}
resource contactCentreHandoverWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deploySyntheticContactCentre) {
  scope: handoverContainer
  name: guid(handoverContainer.id, contactCentrePrincipalId, roles.blobDataContributor)
  properties: {
    principalId: contactCentrePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}
var consumedQueueNames = [ 'journey-events', 'escalations' ]
resource consumedQueues 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' existing = [for name in consumedQueueNames: {
  parent: sb
  name: name
}]
resource runtimeJourneyReceive 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in consumedQueueNames: {
  scope: consumedQueues[i]
  name: guid(consumedQueues[i].id, runtimePrincipalId, roles.sbReceiver)
  properties: {
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.sbReceiver)
  }
}]
resource cohortContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: contentBlobService
  name: 'cohort-zone'
}
resource auditContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: contentBlobService
  name: 'learning-audits'
}
resource recordsBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: st
  name: 'default'
}
resource recordsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: recordsBlobService
  name: 'explainability-records'
}
resource outcomeContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: recordsBlobService
  name: 'learning-aggregates'
}
resource runtimeOutcomeWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: outcomeContainer
  name: guid(outcomeContainer.id, runtimePrincipalId, roles.blobDataContributor)
  properties: {
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
    description: 'IMP-44 structured outcomes to immutable aggregates; no signing or cohort access.'
  }
}
resource fairnessOutcomeRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: outcomeContainer
  name: guid(outcomeContainer.id, fairnessPrincipalId, roles.blobDataReader)
  properties: {
    principalId: fairnessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataReader)
  }
}
resource strategyVerifyRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(kv.id, 'gg-strategy-verifier')
  properties: {
    roleName: 'GovernedGraph Strategy Verifier ${uniqueString(kv.id)}'
    description: 'Read public RSA key metadata and verify only; never sign snapshots.'
    type: 'CustomRole'
    assignableScopes: [ resourceGroup().id ]
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [ 'Microsoft.KeyVault/vaults/keys/read', 'Microsoft.KeyVault/vaults/keys/verify/action' ]
        notDataActions: []
      }
    ]
  }
}
resource strategyVerify 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: snapshotKey
  name: guid(snapshotKey.id, strategyPrincipalId, strategyVerifyRole.id)
  properties: { principalId: strategyPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: strategyVerifyRole.id }
}
resource strategyStateWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: rankingContainer
  name: guid(rankingContainer.id, strategyPrincipalId, roles.blobDataContributor)
  properties: {
    principalId: strategyPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}
resource fairnessCohort 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: cohortContainer
  name: guid(cohortContainer.id, fairnessPrincipalId, roles.blobDataContributor)
  properties: {
    principalId: fairnessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}
resource fairnessAudit 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: auditContainer
  name: guid(auditContainer.id, fairnessPrincipalId, roles.blobDataContributor)
  properties: {
    principalId: fairnessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}
resource pipelineAuditRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: auditContainer
  name: guid(auditContainer.id, pipelinePrincipalId, roles.blobDataReader)
  properties: {
    principalId: pipelinePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataReader)
  }
}
resource strategyInsights 'Microsoft.Insights/components@2020-02-02' existing = { name: appInsightsName }
resource strategyMetrics 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: strategyInsights
  name: guid(strategyInsights.id, strategyPrincipalId, 'metrics-publisher')
  properties: {
    principalId: strategyPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
  }
}
resource runtimeMetrics 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: strategyInsights
  name: guid(strategyInsights.id, runtimePrincipalId, 'metrics-publisher')
  properties: {
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
  }
}
resource searchContentRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: contentContainer
  name: guid(contentContainer.id, searchPrincipalId, roles.blobDataReader)
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataReader)
  }
}
resource searchEmbedding 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: ais
  name: guid(ais.id, searchPrincipalId, roles.openAiUser)
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
  }
}
resource devContentPublish 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  scope: contentContainer
  name: guid(contentContainer.id, developerPrincipalId, roles.blobDataContributor)
  properties: {
    principalId: developerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}
resource p3ContentPublish 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: contentContainer
  name: guid(contentContainer.id, contentPublisherPrincipalId, roles.blobDataContributor)
  properties: {
    principalId: contentPublisherPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}
resource p3SearchPublish 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in [
  roles.searchServiceContributor
  roles.searchIndexContributor
]: {
  scope: search
  name: guid(search.id, contentPublisherPrincipalId, role)
  properties: {
    principalId: contentPublisherPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]
resource p3RegistryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, contentPublisherPrincipalId, 'content-publisher-pull')
  properties: {
    principalId: contentPublisherPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

// --- runtime (member-facing) ---
resource verifyRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(kv.id, 'gg-snapshot-verifier')
  properties: {
    roleName: 'GovernedGraph Snapshot Verifier ${uniqueString(kv.id)}'
    description: 'Verify ranking snapshots without permission to sign or modify keys.'
    type: 'CustomRole'
    assignableScopes: [ resourceGroup().id ]
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [ 'Microsoft.KeyVault/vaults/keys/verify/action' ]
        notDataActions: []
      }
    ]
  }
}

resource rtKvVerify 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: snapshotKey
  name: guid(snapshotKey.id, runtimePrincipalId, verifyRole.id)
  properties: { principalId: runtimePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: verifyRole.id }
}
resource rtBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: recordsContainer
  name: guid(recordsContainer.id, runtimePrincipalId, roles.blobDataContributor)
  properties: { principalId: runtimePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor) }
}
resource rtSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, runtimePrincipalId, roles.searchIndexReader)
  properties: { principalId: runtimePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexReader) }
}
resource rtAi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: ais
  name: guid(ais.id, runtimePrincipalId, roles.azureAiUser)
  properties: { principalId: runtimePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAiUser) }
}
resource rtCs 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: ais
  name: guid(ais.id, runtimePrincipalId, roles.cognitiveServicesUser)
  properties: { principalId: runtimePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser) }
}
resource rtSb 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: sb
  name: guid(sb.id, runtimePrincipalId, roles.sbSender)
  properties: { principalId: runtimePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.sbSender) }
}
// --- P2 pipeline (the only snapshot signer) ---
resource p2Kv 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: snapshotKey
  name: guid(snapshotKey.id, pipelinePrincipalId, keySignerRole.id)
  properties: { principalId: pipelinePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: keySignerRole.id }
}
resource p2KvVerify 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: snapshotKey
  name: guid(snapshotKey.id, pipelinePrincipalId, verifyRole.id)
  properties: { principalId: pipelinePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: verifyRole.id }
}
resource p2Sb 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: sb
  name: guid(sb.id, pipelinePrincipalId, roles.sbReceiver)
  properties: { principalId: pipelinePrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.sbReceiver) }
}

resource learningEvidence 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: contentBlobService
  name: 'learning-evidence'
}
resource p2EvidenceWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: learningEvidence
  name: guid(learningEvidence.id, pipelinePrincipalId, roles.blobDataContributor)
  properties: {
    principalId: pipelinePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}

// --- administration console (container-scoped; no cohort zone, no signing, no content or member state) ---
resource adminRecordsRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAdminConsole) {
  scope: recordsContainer
  name: guid(recordsContainer.id, adminPrincipalId, roles.blobDataReader)
  properties: {
    principalId: adminPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataReader)
    description: 'Administration console read-only immutable response audit records.'
  }
}
resource adminOutcomeRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAdminConsole) {
  scope: outcomeContainer
  name: guid(outcomeContainer.id, adminPrincipalId, roles.blobDataReader)
  properties: {
    principalId: adminPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataReader)
    description: 'Administration console read-only learning aggregates.'
  }
}
resource adminEvidenceRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAdminConsole) {
  scope: learningEvidence
  name: guid(learningEvidence.id, adminPrincipalId, roles.blobDataReader)
  properties: {
    principalId: adminPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataReader)
    description: 'Administration console read-only committee and uplift evidence.'
  }
}
resource adminActionsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = if (deployAdminConsole) {
  parent: recordsBlobService
  name: 'admin-actions'
}
resource adminJournalWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAdminConsole) {
  scope: adminActionsContainer
  name: guid(adminActionsContainer.id, adminPrincipalId, roles.blobDataContributor)
  properties: {
    principalId: adminPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
    description: 'Administration console journal only; the container is immutable once written.'
  }
}

// --- developer (optional, for local runs against Azure) ---
resource devAi 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  scope: ais
  name: guid(ais.id, developerPrincipalId, roles.azureAiUser)
  properties: { principalId: developerPrincipalId, principalType: 'User', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAiUser) }
}
resource devSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  scope: search
  name: guid(search.id, developerPrincipalId, roles.searchIndexContributor)
  properties: { principalId: developerPrincipalId, principalType: 'User', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexContributor) }
}
resource devSearchSvc 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  scope: search
  name: guid(search.id, developerPrincipalId, roles.searchServiceContributor)
  properties: { principalId: developerPrincipalId, principalType: 'User', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchServiceContributor) }
}
resource devBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  scope: st
  name: guid(st.id, developerPrincipalId, roles.blobDataContributor)
  properties: { principalId: developerPrincipalId, principalType: 'User', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor) }
}
