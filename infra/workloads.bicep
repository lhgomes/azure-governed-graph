// Complete workloads only, consuming public foundation outputs.
targetScope = 'subscription'

@description('Short environment name (azd sets this).')
param environmentName string
@description('EU region. Confirm Foundry models, Voice Live and Fabric availability (A-2, A-3).')
param location string
@description('EU Search region; may differ only when the primary region lacks capacity (A-2).')
param searchLocation string = location
param embeddingModelName string = 'text-embedding-3-large'
@description('Immutable image references supplied by the explicit domain composition.')
param orchestratorImage string
param strategyServiceImage string
param memberValuesImage string
param memberTokenIssuer string
param memberTokenAudience string = 'gg-apim'
@description('Public RSA JWK only; never supply private key material.')
param memberTokenPublicJwk object
@description('Explicit opt-in for the synthetic development issuer; never enable in production.')
param deployDevIssuer bool = false
@description('Opt-in assigned presenter path. Effective only in gg-dev; defaults off everywhere.')
param deployVoiceDemo bool = false
param deployVoicePersona bool = false
@description('True once delivery has published the persona agent.')
param personaAgentPublished bool = false
@description('True once delivery has published the gap-review agent; false means no AI drafts: no style ideas are offered and content ideas have no draft.')
param gapReviewAgentPublished bool = false
param voiceDemoClientId string = ''
param voiceDemoOperatorId string = ''
@description('Immutable P2 image; native learning and fairness schedules are configured at the approved cadence.')
param p2ContainerImage string
@description('Entra v2 application ID of the Strategy mutation API; roles are assigned through Microsoft Graph.')
param strategyApiClientId string = ''
@description('Approved human dev recovery operator; empty disables recovery, never enables strategy reinstatement.')
param strategyRecoveryOperatorId string = ''
param counsellorAgentName string = 'gg-counsellor'
@description('True once delivery has published the counsellor and compact voice agents; false keeps generated answers closed.')
param agentsPublished bool = false
@description('Explicit synthetic conversation visibility; only effective in the opted-in gg-dev presenter.')
param traceSyntheticConversations bool = false
@allowed(['gpt-counsellor', 'gpt-compact'])
param voiceDraftModel string = 'gpt-counsellor'
param deploySyntheticContactCentre bool = false
param contactCentreImage string
param contactCentreApiClientId string = ''
param contactCentreAudience string = ''
param contactCentreUrl string = ''
@description('Deploy the administration console: its own identity, an internal app behind the gateway at /admin, and container-scoped evidence access.')
param deployAdminConsole bool = false
param adminImage string
@description('Optional Entra group object ID allowed to view the console. Blank leaves read access control off.')
param adminReaderGroupId string = ''
@description('Optional Entra group object ID allowed to apply protective actions. Blank disables actions once any group is set.')
param adminOperatorGroupId string = ''
@description('Entra group for platform owners. The deployment enrolls the signed-in deploying user.')
param adminOwnerGroupId string = ''
@description('Client ID of the Entra single-page app used for console sign-in; required when a group ID is set.')
param adminClientId string = ''
@description('Allow protective actions while access control is off. Off by default on Azure; the console is publicly reachable.')
param adminAllowOpenActions bool = false
@description('Let the console read captured synthetic conversation spans (Log Analytics Reader on the workspace). Review before enabling.')
param adminTraceLookup bool = false
@description('Dev on-call destination supplied through environment configuration; never a code literal.')
param onCallEmail string
param promptShieldsFailureThreshold int = 3
@description('Resource group to create. Empty uses rg-<environmentName>.')
@maxLength(90)
param resourceGroupName string = ''
@description('Persisted random seed for fresh deployments. Empty preserves existing legacy resource names.')
@maxLength(36)
param namingSeed string = ''
@description('Explicit deterministic values adapter application module from the extension composition.')
@minLength(1)
@maxLength(64)
param memberValuesModule string
param domainEnv object
param foundation object

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: empty(resourceGroupName) ? 'rg-${environmentName}' : resourceGroupName
}

var token = empty(namingSeed)
  ? toLower(uniqueString(subscription().id, rg.id, environmentName))
  : toLower(uniqueString(subscription().id, rg.id, environmentName, namingSeed))
var tags = { 'azd-env-name': environmentName, workload: 'gg-journey-graph', data: 'synthetic' }

var monitoring = { outputs: foundation.monitoring }
var security = { outputs: foundation.security }
var data = { outputs: foundation.data }
var ai = { outputs: foundation.ai }
module p2Access 'modules/p2-access.bicep' = {
  name: 'p2-access'
  scope: rg
  params: {
    storageName: data.outputs.contentStorageName
    aggregateStorageName: data.outputs.storageName
    registryName: foundation.compute.registryName
    pipelinePrincipalId: security.outputs.pipelinePrincipalId
    fairnessPrincipalId: security.outputs.fairnessPrincipalId
  }
}
module fairnessConsumer 'modules/fairness-agent-consumer.bicep' = {
  name: 'fairness-agent-consumer'
  scope: rg
  params: {
    fairnessPrincipalId: security.outputs.fairnessPrincipalId
    aiServicesName: ai.outputs.aiServicesName
    projectName: last(split(ai.outputs.projectEndpoint, '/'))
  }
}
// The persona agent must already exist (created before this deployment); the grant is scoped to it alone.
module personaConsumer 'modules/persona-agent-consumer.bicep' = if (deployVoicePersona && environmentName == 'gg-dev' && personaAgentPublished) {
  name: 'persona-agent-consumer'
  scope: rg
  params: {
    personaPrincipalId: security.outputs.personaPrincipalId
    aiServicesName: ai.outputs.aiServicesName
    projectName: last(split(ai.outputs.projectEndpoint, '/'))
  }
}
module alerts 'modules/alerts.bicep' = {
  name: 'operational-alerts'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    monitorGapReview: deployAdminConsole
    monitorPublication: deployAdminConsole
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    serviceBusId: data.outputs.serviceBusId
    onCallEmail: onCallEmail
    promptShieldsFailureThreshold: promptShieldsFailureThreshold
  }
}

module compute 'modules/compute.bicep' = {
  name: 'compute'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    runtimeIdentityId: security.outputs.runtimeIdentityId
    runtimeIdentityClientId: security.outputs.runtimeIdentityClientId
    personaIdentityId: deployVoicePersona && personaAgentPublished ? security.outputs.personaIdentityId : ''
    personaClientId: deployVoicePersona && personaAgentPublished ? security.outputs.personaClientId : ''
    runtimePrincipalId: security.outputs.runtimePrincipalId
    memberValuesIdentityId: security.outputs.memberValuesIdentityId
    memberValuesClientId: security.outputs.memberValuesClientId
    memberValuesPrincipalId: security.outputs.memberValuesPrincipalId
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
    deployAdminConsole: deployAdminConsole
    adminImage: adminImage
    adminIdentityId: security.outputs.adminIdentityId
    adminClientId: security.outputs.adminClientId
    adminPrincipalId: security.outputs.adminPrincipalId
    adminEnv: {
      AZURE_ENV_NAME: environmentName
      GOVERNEDGRAPH_ADMIN_ENABLED: 'true'
      GOVERNEDGRAPH_ADMIN_PUBLIC_ORIGIN: 'https://apim-gg-${token}.azure-api.net'
      GOVERNEDGRAPH_ADMIN_BASE_PATH: '/admin/'
      GOVERNEDGRAPH_ADMIN_READER_GROUP_ID: adminReaderGroupId
      GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID: adminOperatorGroupId
      GOVERNEDGRAPH_ADMIN_OWNER_GROUP_ID: adminOwnerGroupId
      GOVERNEDGRAPH_ADMIN_CLIENT_ID: adminClientId
      GOVERNEDGRAPH_ADMIN_TENANT_ID: subscription().tenantId
      GOVERNEDGRAPH_ADMIN_ALLOW_OPEN_ACTIONS: adminAllowOpenActions ? 'true' : 'false'
      STRATEGY_ADMIN_URL: 'https://apim-gg-${token}.azure-api.net/strategy-admin'
      STRATEGY_API_CLIENT_ID: strategyApiClientId
      STRATEGY_TENANT_ID: subscription().tenantId
      EXPLAINABILITY_STORAGE_URL: data.outputs.blobEndpoint
      EXPLAINABILITY_CONTAINER: 'explainability-records'
      RANKING_STORAGE_URL: data.outputs.contentBlobEndpoint
      GOVERNEDGRAPH_ADMIN_TRACE_WORKSPACE: adminTraceLookup ? monitoring.outputs.logAnalyticsCustomerId : ''
      GOVERNEDGRAPH_ADMIN_TRACE_PROJECT: adminTraceLookup ? ai.outputs.projectResourceId : ''
      GOVERNEDGRAPH_ADMIN_TRACE_ENVIRONMENT: environmentName
    }
    strategyEnv: {
      AZURE_ENV_NAME: environmentName
      GOVERNEDGRAPH_ADMIN_OWNER_GROUP_ID: adminOwnerGroupId
      GOVERNEDGRAPH_ADMIN_CLIENT_ID: adminClientId
      STRATEGY_ADMIN_PRINCIPAL_ID: security.outputs.adminPrincipalId
      STRATEGY_RECOVERY_OPERATOR_ID: environmentName == 'gg-dev' ? strategyRecoveryOperatorId : ''
      P2_FAIRNESS_JOB_ID: empty(p2ContainerImage) ? '' : '${rg.id}/providers/Microsoft.App/jobs/job-gg-p2-fairness'
      GOVERNEDGRAPH_P2_IMAGE: p2ContainerImage
      APPLICATIONINSIGHTS_CONNECTION_STRING: monitoring.outputs.appInsightsRouting
      GOVERNEDGRAPH_SERVICE_NAME: 'gg-strategy-service'
    }
    orchestratorEnv: {
      AZURE_ENV_NAME: environmentName
      STRATEGY_ADMIN_URL: 'https://apim-gg-${token}.azure-api.net/strategy-admin'
      GOVERNEDGRAPH_ADMIN_TESTING: deployAdminConsole && environmentName == 'gg-dev' ? 'true' : 'false'
      GOVERNEDGRAPH_ADMIN_READER_GROUP_ID: adminReaderGroupId
      GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID: adminOperatorGroupId
      GOVERNEDGRAPH_ADMIN_OWNER_GROUP_ID: adminOwnerGroupId
      GOVERNEDGRAPH_ADMIN_CLIENT_ID: adminClientId
      AZURE_VOICE_LIVE_ENDPOINT: ai.outputs.aiServicesEndpoint
      GOVERNEDGRAPH_ENABLE_VOICE_PROOF: environmentName == 'gg-dev' ? 'true' : 'false'
      // Host-authored feedback wording. Synthetic dev only; production requires B8 approval.
      GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK: environmentName == 'gg-dev' ? 'true' : 'false'
      GOVERNEDGRAPH_DEPLOY_VOICE_DEMO: deployVoiceDemo && environmentName == 'gg-dev' ? 'true' : 'false'
      VOICE_DEMO_CLIENT_ID: voiceDemoClientId
      VOICE_DEMO_OPERATOR_ID: voiceDemoOperatorId
      VOICE_DEMO_ORIGIN: 'https://apim-gg-${token}.azure-api.net'
      APPLICATIONINSIGHTS_CONNECTION_STRING: monitoring.outputs.appInsightsRouting
      AZURE_AI_AGENT_NAME: counsellorAgentName
      AZURE_AI_GATEWAY_URL: 'https://apim-gg-${token}.azure-api.net/foundry/openai/v1'
      GOVERNEDGRAPH_SERVICE_NAME: 'gg-orchestrator'
      AZURE_AI_DECISION_DEPLOYMENT: ai.outputs.decisionDeployment
      GOVERNEDGRAPH_CAPTURE_DECLINED_QUESTIONS: deployAdminConsole ? 'true' : 'false'
      GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS: traceSyntheticConversations && environmentName == 'gg-dev' && deployVoiceDemo ? 'true' : 'false'
      GOVERNEDGRAPH_LOG_LLM_CALLS: environmentName == 'gg-dev' ? 'true' : 'false'
      GOVERNEDGRAPH_VOICE_DRAFT_MODEL: voiceDraftModel
      AZURE_AI_COMPACT_DEPLOYMENT: 'gpt-compact'
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: 'false'
      AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED: 'false'
    }

    orchestratorImage: orchestratorImage
    strategyServiceImage: strategyServiceImage
    memberValuesImage: memberValuesImage
    memberValuesModule: memberValuesModule
    env: union(domainEnv, {
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
    })
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
    token: token
    memberValuesBackendUrl: compute.outputs.memberValuesUrl
    strategyServiceBackendUrl: compute.outputs.strategyServiceUrl
    orchestratorBackendUrl: compute.outputs.orchestratorUrl
    appInsightsName: last(split(monitoring.outputs.appInsightsId, '/'))
    memberTokenIssuer: memberTokenIssuer
    memberTokenAudience: memberTokenAudience
    memberTokenPublicJwk: memberTokenPublicJwk
    deployAdminConsole: deployAdminConsole
    adminBackendUrl: compute.outputs.adminUrl
  }
}

module modelGateway 'modules/model-gateway.bicep' = {
  name: 'model-gateway'
  scope: rg
  params: {
    apimName: 'apim-gg-${token}'
    projectEndpoint: ai.outputs.projectEndpoint
    runtimeClientId: security.outputs.runtimeIdentityClientId
    agentsReady: agentsPublished
    // gg-dev only: a live persona answers in seconds, so a rewrite round can use ~16k tokens in under a minute.
    // The daily token quota below stays unchanged.
    conversationTokensPerMinute: environmentName == 'gg-dev' ? 30000 : 12000
  }
  dependsOn: [ gateway ]
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
    scheduleEnabled: true
    env: union(domainEnv, {
      AZURE_ENV_NAME: environmentName
      RANKING_STORAGE_URL: data.outputs.contentBlobEndpoint
      EXPLAINABILITY_STORAGE_URL: data.outputs.blobEndpoint
      STRATEGY_ADMIN_URL: '${gateway.outputs.gatewayUrl}/strategy-admin'
      STRATEGY_API_CLIENT_ID: strategyApiClientId
      P2_ARTIFACT_STORAGE_URL: data.outputs.contentBlobEndpoint
      P2_FAIRNESS_JOB_ID: '${rg.id}/providers/Microsoft.App/jobs/job-gg-p2-fairness'
    })
    pipelineEnv: {
      AZURE_KEY_VAULT_URL: security.outputs.keyVaultUri
      SNAPSHOT_SIGNING_KEY_NAME: security.outputs.signingKeyName
      GOVERNEDGRAPH_P2_PRINCIPAL_ID: security.outputs.pipelinePrincipalId
    }
  }
  dependsOn: [p2Access]
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

// The drafting agent must already exist (created before this deployment); the grant is scoped to it alone.
module gapReviewConsumer 'modules/gap-review-agent-consumer.bicep' = if (deployAdminConsole && gapReviewAgentPublished) {
  name: 'gap-review-agent-consumer'
  scope: rg
  params: {
    gapReviewPrincipalId: security.outputs.gapReviewPrincipalId
    aiServicesName: ai.outputs.aiServicesName
    projectName: last(split(ai.outputs.projectEndpoint, '/'))
  }
}

// Daily batch that reviews declined interactions and proposes missing content and styles to the administration console.
module gapReview 'modules/gap-review-job.bicep' = if (deployAdminConsole) {
  name: 'gap-review-job'
  scope: rg
  params: {
    location: location
    token: token
    image: adminImage
    identityId: security.outputs.gapReviewIdentityId
    identityClientId: security.outputs.gapReviewClientId
    env: union(domainEnv, {
      AZURE_ENV_NAME: environmentName
      EXPLAINABILITY_STORAGE_URL: data.outputs.blobEndpoint
      EXPLAINABILITY_CONTAINER: 'explainability-records'
      GOVERNEDGRAPH_IMPROVEMENT_CONTAINER: 'improvement-proposals'
      GOVERNEDGRAPH_APPROVALS_CONTAINER: 'content-approvals'
      GOVERNEDGRAPH_DECLINED_QUESTIONS_CONTAINER: 'declined-questions'
      GOVERNEDGRAPH_GAP_REVIEW_SYNTHETIC_FEEDBACK: environmentName == 'gg-dev' ? 'true' : 'false'
      AZURE_AI_PROJECT_ENDPOINT: ai.outputs.projectEndpoint
      AZURE_AI_GAP_REVIEW_AGENT: gapReviewAgentPublished ? 'gg-gap-review' : ''
      JOURNEY_STORAGE_URL: data.outputs.contentBlobEndpoint
      STRATEGY_ADMIN_URL: '${gateway.outputs.gatewayUrl}/strategy-admin'
      STRATEGY_API_CLIENT_ID: strategyApiClientId
    })
  }
}

module adminTraceRbac 'modules/admin-trace-rbac.bicep' = if (deployAdminConsole && adminTraceLookup) {
  name: 'admin-trace-access'
  scope: rg
  params: {
    workspaceName: monitoring.outputs.logAnalyticsName
    adminPrincipalId: security.outputs.adminPrincipalId
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output AZURE_SEARCH_LOCATION string = searchLocation
output AZURE_AI_PROJECT_ENDPOINT string = ai.outputs.projectEndpoint
output AZURE_AI_PROJECT_RESOURCE_ID string = ai.outputs.projectResourceId
output AZURE_AI_DECISION_DEPLOYMENT string = ai.outputs.decisionDeployment
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
output CONTENT_PUBLISH_JOB_NAME string = 'job-gg-content-publisher'
output CONTENT_APPROVALS_RECONCILER_JOB_NAME string = deployAdminConsole ? 'job-gg-approved-reconciler' : ''
output GAP_REVIEW_JOB_NAME string = deployAdminConsole ? 'job-gg-gap-review' : ''
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
output P2_ARTIFACT_STORAGE_URL string = data.outputs.contentBlobEndpoint
output RANKING_STORAGE_URL string = data.outputs.contentBlobEndpoint
output STRATEGY_TENANT_ID string = subscription().tenantId
output STRATEGY_API_CLIENT_ID string = strategyApiClientId
output STRATEGY_ADMIN_URL string = '${gateway.outputs.gatewayUrl}/strategy-admin'
// Workspace customer GUID (an identifier, not a credential), so local tools can query traces without manual input.
output LOG_ANALYTICS_WORKSPACE_ID string = monitoring.outputs.logAnalyticsCustomerId
output ADMIN_CONSOLE_URL string = deployAdminConsole ? '${gateway.outputs.gatewayUrl}/admin/' : ''
output STRATEGY_ADMIN_PRINCIPAL_ID string = security.outputs.adminPrincipalId
output ADMIN_IDENTITY_ID string = security.outputs.adminIdentityId
output ADMIN_CLIENT_ID string = security.outputs.adminClientId
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
