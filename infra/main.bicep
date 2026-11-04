// GovernedGraph governed Learning Member Journey Graph - full-stack reference infrastructure.
// azd up  ->  provisions everything below in one EU region (A-2: confirm model + Voice Live availability).
// Every resource uses Entra ID / managed identity; local auth is disabled where the service allows it.
targetScope = 'subscription'

@description('Short environment name (azd sets this).')
param environmentName string
@description('EU region. Confirm Foundry models, Voice Live and Fabric availability (A-2, A-3).')
param location string
@description('EU Search region; may differ only when the primary region lacks capacity (A-2).')
param searchLocation string = location
@description('Object ID of the deploying user, for data-plane access during development.')
param principalId string = ''
@description('Larger GPT-family deployment. Verify name/version in the regional catalogue.')
param counsellorModelName string = 'gpt-4.1'
param counsellorModelVersion string = '2025-04-14'
@description('Smaller GPT-family deployment.')
param compactModelName string = 'gpt-4.1-mini'
param compactModelVersion string = '2025-04-14'
param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'
@description('Deploy a Microsoft Fabric capacity for the journey aggregates + segregated cohort zone.')
param deployFabric bool = false
param fabricAdminUpn string = ''
@description('Container image tags are set by azd deploy; placeholders allow first provision.')
param orchestratorImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param strategyServiceImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param memberValuesImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param contentPublisherImage string = ''
param devIssuerImage string = ''
param publisherEmail string = 'noreply@contoso.example'
param memberTokenIssuer string
param memberTokenAudience string = 'gg-apim'
@description('Public RSA JWK only; never supply private key material.')
param memberTokenPublicJwk object
@description('Explicit opt-in for the synthetic development issuer; never enable in production.')
param deployDevIssuer bool = false
@description('Opt-in assigned presenter path. Effective only in gg-dev; defaults off everywhere.')
param deployVoiceDemo bool = false
param voiceDemoClientId string = ''
param voiceDemoOperatorId string = ''
@description('Explicit gg-dev-only opt-in for labelled learning fixtures; production uses governed upstream feeds.')
param deployDevFixtures bool = false
param devFixturesImage string = ''
@description('Immutable temporary P2 Container Apps image; empty keeps this optional hosting adapter undeployed.')
param p2ContainerImage string = ''
param p2ScheduleEnabled bool = false
param p2ScheduleProof bool = false
@description('Entra v2 application ID of the Strategy mutation API; roles are assigned through Microsoft Graph.')
param strategyApiClientId string = ''
@description('Approved human dev recovery operator; empty disables recovery, never enables strategy reinstatement.')
param strategyRecoveryOperatorId string = ''
@description('Verified Azure ML blob datastore container names; no account-wide P2 data role.')
param p2MlArtifactContainers array = []
param counsellorAgentName string = 'gg-counsellor'
@description('Exact version returned by scripts/create_agents.py --apply; never use latest.')
param counsellorAgentVersion string = ''
@description('Exact immutable compact voice candidate version (IMP-35); empty fails closed.')
param voiceCompactAgentVersion string = ''
@description('Exact immutable contextual decision agents; empty fails closed.')
param intentAgentVersion string = ''
param understandingAgentVersion string = ''
@description('Exact immutable source-grounded answer judge version; empty fails closed.')
param reviewAgentVersion string = ''
@description('Verified human Tier-2 writer approval object. Null or empty keeps generated answers suspended.')
param writerApproval object?
@description('Explicit synthetic conversation visibility; only effective in the opted-in gg-dev presenter.')
param traceSyntheticConversations bool = false
@allowed(['gpt-counsellor', 'gpt-compact'])
param voiceDraftModel string = 'gpt-counsellor'
param deploySyntheticContactCentre bool = false
param contactCentreImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param contactCentreApiClientId string = ''
param contactCentreAudience string = ''
param contactCentreUrl string = ''
@description('Dev on-call destination supplied through environment configuration; never a code literal.')
param onCallEmail string
param promptShieldsFailureThreshold int = 3
@description('Resource group to create. Empty uses rg-<environmentName>.')
@maxLength(90)
param resourceGroupName string = ''
@description('Persisted random seed for fresh deployments. Empty preserves existing legacy resource names.')
@maxLength(32)
param namingSeed string = ''

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: empty(resourceGroupName) ? 'rg-${environmentName}' : resourceGroupName
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
module alerts 'modules/alerts.bicep' = {
  name: 'operational-alerts'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    serviceBusId: data.outputs.serviceBusId
    onCallEmail: onCallEmail
    promptShieldsFailureThreshold: promptShieldsFailureThreshold
  }
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

module compute 'modules/compute.bicep' = {
  name: 'compute'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    infrastructureSubnetId: network.outputs.infrastructureSubnetId
    virtualNetworkId: network.outputs.virtualNetworkId
    runtimeIdentityId: security.outputs.runtimeIdentityId
    runtimeIdentityClientId: security.outputs.runtimeIdentityClientId
    runtimePrincipalId: security.outputs.runtimePrincipalId
    strategyIdentityId: security.outputs.strategyIdentityId
    strategyClientId: security.outputs.strategyClientId
    strategyPrincipalId: security.outputs.strategyPrincipalId
    deploySyntheticContactCentre: deploySyntheticContactCentre
    contactCentreImage: contactCentreImage
    contactCentreIdentityId: security.outputs.contactCentreIdentityId
    contactCentreIdentityClientId: security.outputs.contactCentreClientId
    contactCentrePrincipalId: security.outputs.contactCentrePrincipalId
    contactCentreApiClientId: contactCentreApiClientId
    contactCentreAudience: empty(contactCentreAudience) ? 'api://${contactCentreApiClientId}' : contactCentreAudience
    contactCentreUrl: contactCentreUrl
    contactCentreStorageUrl: data.outputs.contentBlobEndpoint
    strategyEnv: {
      AZURE_ENV_NAME: environmentName
      STRATEGY_RECOVERY_OPERATOR_ID: environmentName == 'gg-dev' ? strategyRecoveryOperatorId : ''
      P2_FAIRNESS_JOB_ID: empty(p2ContainerImage) ? '' : '${rg.id}/providers/Microsoft.App/jobs/job-gg-p2-fairness'
      GOVERNEDGRAPH_P2_IMAGE: p2ContainerImage
      APPLICATIONINSIGHTS_CONNECTION_STRING: monitoring.outputs.appInsightsRouting
      GOVERNEDGRAPH_SERVICE_NAME: 'gg-strategy-service'
    }
    orchestratorEnv: {
      AZURE_ENV_NAME: environmentName
      AZURE_VOICE_LIVE_ENDPOINT: ai.outputs.aiServicesEndpoint
      GOVERNEDGRAPH_ENABLE_VOICE_PROOF: environmentName == 'gg-dev' ? 'true' : 'false'
      GOVERNEDGRAPH_DEPLOY_VOICE_DEMO: deployVoiceDemo && environmentName == 'gg-dev' ? 'true' : 'false'
      VOICE_DEMO_CLIENT_ID: voiceDemoClientId
      VOICE_DEMO_OPERATOR_ID: voiceDemoOperatorId
      VOICE_DEMO_ORIGIN: 'https://apim-gg-${token}.azure-api.net'
      APPLICATIONINSIGHTS_CONNECTION_STRING: monitoring.outputs.appInsightsRouting
      AZURE_AI_AGENT_NAME: counsellorAgentName
      AZURE_AI_GATEWAY_URL: 'https://apim-gg-${token}.azure-api.net/foundry/openai/v1'
      GOVERNEDGRAPH_SERVICE_NAME: 'gg-orchestrator'
      AZURE_AI_AGENT_VERSION: counsellorAgentVersion
      AZURE_AI_VOICE_COMPACT_VERSION: voiceCompactAgentVersion
      AZURE_AI_INTENT_AGENT_VERSION: intentAgentVersion
      AZURE_AI_UNDERSTANDING_AGENT_VERSION: understandingAgentVersion
      AZURE_AI_REVIEW_AGENT_VERSION: reviewAgentVersion
      GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS: traceSyntheticConversations && environmentName == 'gg-dev' && deployVoiceDemo ? 'true' : 'false'
      GOVERNEDGRAPH_VOICE_DRAFT_MODEL: voiceDraftModel
      AZURE_AI_COMPACT_DEPLOYMENT: 'gpt-compact'
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: 'false'
      AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED: 'false'
    }

    orchestratorImage: orchestratorImage
    strategyServiceImage: strategyServiceImage
    memberValuesImage: memberValuesImage
    env: {
      GOVERNEDGRAPH_MODE: 'azure'
      MEMBER_TOKEN_ISSUER: memberTokenIssuer
      MEMBER_TOKEN_AUDIENCE: memberTokenAudience
      MEMBER_TOKEN_PUBLIC_JWK: string(memberTokenPublicJwk)
      AZURE_AI_PROJECT_ENDPOINT: ai.outputs.projectEndpoint
      AZURE_AI_PROJECT_RESOURCE_ID: ai.outputs.projectResourceId
      AZURE_AI_MODEL_DEPLOYMENT: 'gpt-counsellor'
      AZURE_SEARCH_ENDPOINT: data.outputs.searchEndpoint
      AZURE_SEARCH_INDEX: 'approved-content'
      EXPLAINABILITY_STORAGE_URL: data.outputs.blobEndpoint
      EXPLAINABILITY_CONTAINER: 'explainability-records'
      AZURE_KEY_VAULT_URL: security.outputs.keyVaultUri
      SNAPSHOT_SIGNING_KEY_NAME: security.outputs.signingKeyName
      RANKING_STORAGE_URL: data.outputs.contentBlobEndpoint
      STRATEGY_TENANT_ID: subscription().tenantId
      STRATEGY_API_CLIENT_ID: strategyApiClientId
      STRATEGY_PUBLISHER_PRINCIPAL_ID: security.outputs.pipelinePrincipalId
      STRATEGY_FAIRNESS_PRINCIPAL_ID: security.outputs.fairnessPrincipalId
      STRATEGY_RUNTIME_PRINCIPAL_ID: security.outputs.runtimePrincipalId
      SERVICE_BUS_NAMESPACE: data.outputs.serviceBusFqdn
      JOURNEY_STORAGE_URL: data.outputs.contentBlobEndpoint
      CONTENT_SAFETY_ENDPOINT: ai.outputs.contentSafetyEndpoint
    }
  }
}

module voiceDemo 'modules/voice-demo.bicep' = if (environmentName == 'gg-dev' && deployDevIssuer && !empty(voiceDemoClientId)) {
  name: 'voice-demo'
  scope: rg
  params: {
    location: location
    token: token
    enabled: deployVoiceDemo
    image: orchestratorImage
    issuerIdentityId: security.outputs.devIssuerIdentityId
    issuerClientId: security.outputs.devIssuerClientId
    runtimePrincipalId: security.outputs.runtimePrincipalId
    runtimeClientId: security.outputs.runtimeIdentityClientId
    clientId: voiceDemoClientId
    operatorId: voiceDemoOperatorId
    origin: 'https://apim-gg-${token}.azure-api.net'
    memberTokenIssuer: memberTokenIssuer
    orchestratorUrl: compute.outputs.orchestratorUrl
  }
}

module gateway 'modules/gateway.bicep' = {
  name: 'gateway'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    publisherEmail: publisherEmail
    memberValuesBackendUrl: compute.outputs.memberValuesUrl
    strategyServiceBackendUrl: compute.outputs.strategyServiceUrl
    orchestratorBackendUrl: compute.outputs.orchestratorUrl
    apimSubnetId: network.outputs.apimSubnetId
    appInsightsName: last(split(monitoring.outputs.appInsightsId, '/'))
    memberTokenIssuer: memberTokenIssuer
    memberTokenAudience: memberTokenAudience
    memberTokenPublicJwk: memberTokenPublicJwk
  }
}

module modelGateway 'modules/model-gateway.bicep' = {
  name: 'model-gateway'
  scope: rg
  params: {
    apimName: 'apim-gg-${token}'
    projectEndpoint: ai.outputs.projectEndpoint
    runtimeClientId: security.outputs.runtimeIdentityClientId
    mainAgentVersion: counsellorAgentVersion
    compactAgentVersion: voiceCompactAgentVersion
    intentAgentVersion: intentAgentVersion
    understandingAgentVersion: understandingAgentVersion
    reviewAgentVersion: reviewAgentVersion
    writerApprovalJson: empty(writerApproval) ? '' : string(writerApproval)
  }
  dependsOn: [ gateway ]
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
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    virtualNetworkId: network.outputs.virtualNetworkId
    deployFabric: deployFabric
    fabricAdminUpn: fabricAdminUpn
    mlArtifactContainers: p2MlArtifactContainers
    fairnessPrincipalId: security.outputs.fairnessPrincipalId
  }
}

module p2JobMonitorRoles 'modules/p2-job-monitor-rbac.bicep' = {
  name: 'p2-job-monitor-roles'
  scope: rg
  params: {
    workspaceName: learning.outputs.mlWorkspaceName
    pipelinePrincipalId: security.outputs.pipelinePrincipalId
  }
}

module p2Compute 'modules/p2-compute.bicep' = {
  name: 'p2-compute'
  scope: rg
  params: {
    location: location
    tags: tags
    workspaceName: learning.outputs.mlWorkspaceName
    subnetId: network.outputs.mlComputeSubnetId
    pipelineIdentityId: security.outputs.pipelineIdentityId
    fairnessIdentityId: security.outputs.fairnessIdentityId
  }

  dependsOn: [ p2JobMonitorRoles ]
}

module mlTrackingRoles 'modules/ml-tracking-rbac.bicep' = {
  name: 'ml-tracking-roles'
  scope: rg
  params: {
    workspaceName: learning.outputs.mlWorkspaceName
    pipelinePrincipalId: security.outputs.pipelinePrincipalId
    fairnessPrincipalId: security.outputs.fairnessPrincipalId
  }
}

module devLearningFixtures 'modules/dev-learning-fixtures.bicep' = if (deployDevFixtures && environmentName == 'gg-dev') {
  name: 'dev-learning-fixtures'
  scope: rg
  params: {
    location: location
    token: token
    environmentName: environmentName
    deployDevFixtures: deployDevFixtures
    image: devFixturesImage
    recordsStorageName: data.outputs.storageName
    rankingStorageName: data.outputs.contentStorageName
  }
}

module p2ContainerJobs 'modules/p2-jobs.bicep' = if (!empty(p2ContainerImage)) {
  name: 'p2-container-jobs'
  scope: rg
  params: {
    location: location
    token: token
    image: p2ContainerImage
    pipelineIdentityId: security.outputs.pipelineIdentityId
    pipelineClientId: security.outputs.pipelineClientId
    fairnessIdentityId: security.outputs.fairnessIdentityId
    fairnessClientId: security.outputs.fairnessClientId
    scheduleEnabled: p2ScheduleEnabled
    scheduleProof: environmentName == 'gg-dev' && p2ScheduleProof
    env: {
      AZURE_ENV_NAME: environmentName
      RANKING_STORAGE_URL: data.outputs.contentBlobEndpoint
      EXPLAINABILITY_STORAGE_URL: data.outputs.blobEndpoint
      STRATEGY_ADMIN_URL: '${gateway.outputs.gatewayUrl}/strategy-admin'
      STRATEGY_API_CLIENT_ID: strategyApiClientId
      AZURE_ML_ARTIFACT_URL: 'https://${last(split(learning.outputs.mlStorageId, '/'))}.blob.${environment().suffixes.storage}'
      P2_FAIRNESS_JOB_ID: '${rg.id}/providers/Microsoft.App/jobs/job-gg-p2-fairness'
    }
    pipelineEnv: {
      AZURE_KEY_VAULT_URL: security.outputs.keyVaultUri
      SNAPSHOT_SIGNING_KEY_NAME: security.outputs.signingKeyName
      GOVERNEDGRAPH_P2_PRINCIPAL_ID: security.outputs.pipelinePrincipalId
    }
  }
}

module p2ContainerMonitor 'modules/p2-container-monitor-rbac.bicep' = if (!empty(p2ContainerImage)) {
  name: 'p2-container-monitor'
  scope: rg
  params: {
    pipelinePrincipalId: security.outputs.pipelinePrincipalId
    recoveryReaderPrincipalId: environmentName == 'gg-dev' && !empty(strategyRecoveryOperatorId) ? security.outputs.strategyPrincipalId : ''
  }
  dependsOn: [p2ContainerJobs]
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
  }
}

module contentPublisher 'modules/content-publisher.bicep' = {
  name: 'content-publisher'
  scope: rg
  params: {
    location: location
    token: token
    image: empty(contentPublisherImage) ? orchestratorImage : contentPublisherImage
    identityId: security.outputs.contentPublisherIdentityId
    identityClientId: security.outputs.contentPublisherClientId
    registryServer: compute.outputs.registryLoginServer
    env: {
      GOVERNEDGRAPH_MODE: 'azure'
      AZURE_SEARCH_ENDPOINT: data.outputs.searchEndpoint
      AZURE_SEARCH_INDEX: 'approved-content'
      CONTENT_STORAGE_URL: data.outputs.contentBlobEndpoint
      CONTENT_STORAGE_RESOURCE_ID: data.outputs.contentStorageId
      CONTENT_CONTAINER: 'approved-content'
      AZURE_OPENAI_EMBEDDING_ENDPOINT: ai.outputs.embeddingEndpoint
      AZURE_OPENAI_EMBEDDING_DEPLOYMENT: 'embedding'
      AZURE_OPENAI_EMBEDDING_MODEL: embeddingModelName
    }
  }
  dependsOn: [ rbac ]
}

module devMemberIssuer 'modules/dev-member-issuer.bicep' = if (deployDevIssuer) {
  name: 'dev-member-issuer'
  scope: rg
  params: {
    location: location
    token: token
    image: empty(devIssuerImage) ? orchestratorImage : devIssuerImage
    issuerIdentityId: security.outputs.devIssuerIdentityId
    issuerClientId: security.outputs.devIssuerClientId
    memberTokenIssuer: memberTokenIssuer
  }
  dependsOn: [ compute, rbac ]
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output AZURE_SEARCH_LOCATION string = searchLocation
output AZURE_AI_PROJECT_ENDPOINT string = ai.outputs.projectEndpoint
output AZURE_AI_PROJECT_RESOURCE_ID string = ai.outputs.projectResourceId
output CONTENT_SAFETY_ENDPOINT string = ai.outputs.contentSafetyEndpoint
output AZURE_AI_MODEL_DEPLOYMENT string = 'gpt-counsellor'
output AZURE_SEARCH_ENDPOINT string = data.outputs.searchEndpoint
output CONTACT_CENTRE_URL string = compute.outputs.contactCentreEndpoint
output CONTACT_CENTRE_AUDIENCE string = empty(contactCentreAudience) ? 'api://${contactCentreApiClientId}' : contactCentreAudience
output CONTACT_CENTRE_ACTION_GROUP_ID string = alerts.outputs.actionGroupId
output AZURE_OPENAI_EMBEDDING_ENDPOINT string = ai.outputs.embeddingEndpoint
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = 'embedding'
output AZURE_OPENAI_EMBEDDING_MODEL string = embeddingModelName
output CONTENT_STORAGE_URL string = data.outputs.contentBlobEndpoint
output CONTENT_STORAGE_RESOURCE_ID string = data.outputs.contentStorageId
output CONTENT_CONTAINER string = 'approved-content'
output CONTENT_PUBLISH_JOB_NAME string = contentPublisher.outputs.name
output CONTENT_SEARCH_SHARED_LINK_ID string = data.outputs.contentSharedLinkId
output AZURE_KEY_VAULT_URL string = security.outputs.keyVaultUri
output EXPLAINABILITY_STORAGE_URL string = data.outputs.blobEndpoint
output MEMBER_VALUES_API_URL string = gateway.outputs.memberValuesApiUrl
output STRATEGY_SERVICE_URL string = gateway.outputs.strategyServiceUrl
output ORCHESTRATOR_URL string = gateway.outputs.orchestratorUrl
output APIM_GATEWAY_URL string = gateway.outputs.gatewayUrl
output AZURE_SEARCH_INDEX string = 'approved-content'
output EXPLAINABILITY_CONTAINER string = 'explainability-records'
output SNAPSHOT_SIGNING_KEY_NAME string = security.outputs.signingKeyName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = compute.outputs.registryLoginServer
output AZURE_ML_WORKSPACE string = learning.outputs.mlWorkspaceName
output AZURE_ML_STORAGE_ID string = learning.outputs.mlStorageId
output AZURE_ML_REGISTRY_ID string = learning.outputs.mlRegistryId
output AZURE_ML_KEY_VAULT_ID string = learning.outputs.mlVaultId
output AZURE_ML_APP_INSIGHTS_ID string = learning.outputs.mlAppInsightsId
output AZURE_ML_WORKSPACE_PRINCIPAL_ID string = learning.outputs.workspacePrincipalId
output RANKING_STORAGE_URL string = data.outputs.contentBlobEndpoint
output STRATEGY_TENANT_ID string = subscription().tenantId
output STRATEGY_API_CLIENT_ID string = strategyApiClientId
output STRATEGY_ADMIN_URL string = '${gateway.outputs.gatewayUrl}/strategy-admin'
output VOICE_DEMO_ORIGIN string = 'https://apim-gg-${token}.azure-api.net'
output JOURNEY_STORAGE_URL string = data.outputs.contentBlobEndpoint
output STRATEGY_PUBLISHER_PRINCIPAL_ID string = security.outputs.pipelinePrincipalId
output STRATEGY_FAIRNESS_PRINCIPAL_ID string = security.outputs.fairnessPrincipalId
output STRATEGY_RUNTIME_PRINCIPAL_ID string = security.outputs.runtimePrincipalId
output STRATEGY_IDENTITY_ID string = security.outputs.strategyIdentityId
output STRATEGY_PRINCIPAL_ID string = security.outputs.strategyPrincipalId
output STRATEGY_CLIENT_ID string = security.outputs.strategyClientId
output FAIRNESS_IDENTITY_ID string = security.outputs.fairnessIdentityId
output FAIRNESS_CLIENT_ID string = security.outputs.fairnessClientId
output PIPELINE_IDENTITY_ID string = security.outputs.pipelineIdentityId
output PIPELINE_CLIENT_ID string = security.outputs.pipelineClientId
output DEV_ISSUER_IDENTITY_ID string = security.outputs.devIssuerIdentityId
output DEV_ISSUER_PRINCIPAL_ID string = security.outputs.devIssuerPrincipalId
output DEV_ISSUER_CLIENT_ID string = security.outputs.devIssuerClientId
