// Explicit illustrative pension extension. No sample material is a framework default.
targetScope = 'subscription'
param configuration object

module graph '../../../infra/workloads.bicep' = {
  name: 'pension-wtp-workloads'
  params: {
    environmentName: configuration.environmentName
    location: configuration.location
    searchLocation: configuration.searchLocation
    resourceGroupName: configuration.resourceGroupName
    namingSeed: configuration.namingSeed
    foundation: configuration.foundation
    memberValuesModule: 'pension_wtp.member_values_api.app:app'
    domainEnv: {
      GOVERNEDGRAPH_DATA_DIR: '/app/examples/pension_wtp/data'
    }
    orchestratorImage: configuration.orchestratorImage
    strategyServiceImage: configuration.strategyServiceImage
    memberValuesImage: configuration.memberValuesImage
    contactCentreImage: configuration.contactCentreImage
    adminImage: configuration.adminImage
    p2ContainerImage: configuration.p2ContainerImage
    counsellorAgentVersion: configuration.counsellorAgentVersion
    voiceCompactAgentVersion: configuration.voiceCompactAgentVersion
    writerApproval: configuration.writerApproval
    memberTokenIssuer: configuration.memberTokenIssuer
    memberTokenPublicJwk: configuration.memberTokenPublicJwk
    onCallEmail: configuration.onCallEmail
    deployDevIssuer: true
    deploySyntheticContactCentre: true
    deployAdminConsole: true
    strategyApiClientId: configuration.strategyApiClientId
    strategyRecoveryOperatorId: configuration.strategyRecoveryOperatorId
    contactCentreApiClientId: configuration.contactCentreApiClientId
    contactCentreAudience: configuration.contactCentreAudience
    adminClientId: configuration.adminClientId
    adminReaderGroupId: configuration.adminReaderGroupId
    adminOperatorGroupId: configuration.adminOperatorGroupId
    adminOwnerGroupId: configuration.adminOwnerGroupId
    voiceDemoClientId: configuration.voiceDemoClientId
    voiceDemoOperatorId: configuration.voiceDemoOperatorId
    deployVoiceDemo: configuration.deployVoiceDemo
    deployVoicePersona: configuration.deployVoicePersona
    personaAgentVersion: configuration.personaAgentVersion
    traceSyntheticConversations: configuration.traceSyntheticConversations
    adminTraceLookup: configuration.adminTraceLookup
  }
}
output deployment object = {
  AZURE_RESOURCE_GROUP: graph.outputs.AZURE_RESOURCE_GROUP
  AZURE_LOCATION: graph.outputs.AZURE_LOCATION
  AZURE_SEARCH_LOCATION: graph.outputs.AZURE_SEARCH_LOCATION
  AZURE_AI_PROJECT_ENDPOINT: graph.outputs.AZURE_AI_PROJECT_ENDPOINT
  AZURE_AI_PROJECT_RESOURCE_ID: graph.outputs.AZURE_AI_PROJECT_RESOURCE_ID
  AZURE_AI_MODEL_DEPLOYMENT: graph.outputs.AZURE_AI_MODEL_DEPLOYMENT
  CONTENT_SAFETY_ENDPOINT: graph.outputs.CONTENT_SAFETY_ENDPOINT
  AZURE_SEARCH_ENDPOINT: graph.outputs.AZURE_SEARCH_ENDPOINT
  AZURE_SEARCH_INDEX: graph.outputs.AZURE_SEARCH_INDEX
  CONTACT_CENTRE_URL: graph.outputs.CONTACT_CENTRE_URL
  CONTACT_CENTRE_AUDIENCE: graph.outputs.CONTACT_CENTRE_AUDIENCE
  CONTACT_CENTRE_ACTION_GROUP_ID: graph.outputs.CONTACT_CENTRE_ACTION_GROUP_ID
  AZURE_OPENAI_EMBEDDING_ENDPOINT: graph.outputs.AZURE_OPENAI_EMBEDDING_ENDPOINT
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT: graph.outputs.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
  AZURE_OPENAI_EMBEDDING_MODEL: graph.outputs.AZURE_OPENAI_EMBEDDING_MODEL
  CONTENT_STORAGE_URL: graph.outputs.CONTENT_STORAGE_URL
  CONTENT_STORAGE_RESOURCE_ID: graph.outputs.CONTENT_STORAGE_RESOURCE_ID
  CONTENT_CONTAINER: graph.outputs.CONTENT_CONTAINER
  CONTENT_PUBLISH_JOB_NAME: graph.outputs.CONTENT_PUBLISH_JOB_NAME
  CONTENT_SEARCH_SHARED_LINK_ID: graph.outputs.CONTENT_SEARCH_SHARED_LINK_ID
  AZURE_KEY_VAULT_URL: graph.outputs.AZURE_KEY_VAULT_URL
  EXPLAINABILITY_STORAGE_URL: graph.outputs.EXPLAINABILITY_STORAGE_URL
  EXPLAINABILITY_CONTAINER: graph.outputs.EXPLAINABILITY_CONTAINER
  MEMBER_VALUES_API_URL: graph.outputs.MEMBER_VALUES_API_URL
  STRATEGY_SERVICE_URL: graph.outputs.STRATEGY_SERVICE_URL
  ORCHESTRATOR_URL: graph.outputs.ORCHESTRATOR_URL
  APIM_GATEWAY_URL: graph.outputs.APIM_GATEWAY_URL
  SNAPSHOT_SIGNING_KEY_NAME: graph.outputs.SNAPSHOT_SIGNING_KEY_NAME
  AZURE_CONTAINER_REGISTRY_ENDPOINT: graph.outputs.AZURE_CONTAINER_REGISTRY_ENDPOINT
  P2_ARTIFACT_STORAGE_URL: graph.outputs.P2_ARTIFACT_STORAGE_URL
  RANKING_STORAGE_URL: graph.outputs.RANKING_STORAGE_URL
  STRATEGY_TENANT_ID: graph.outputs.STRATEGY_TENANT_ID
  STRATEGY_API_CLIENT_ID: graph.outputs.STRATEGY_API_CLIENT_ID
  STRATEGY_ADMIN_URL: graph.outputs.STRATEGY_ADMIN_URL
  LOG_ANALYTICS_WORKSPACE_ID: graph.outputs.LOG_ANALYTICS_WORKSPACE_ID
  ADMIN_CONSOLE_URL: graph.outputs.ADMIN_CONSOLE_URL
  STRATEGY_ADMIN_PRINCIPAL_ID: graph.outputs.STRATEGY_ADMIN_PRINCIPAL_ID
  ADMIN_IDENTITY_ID: graph.outputs.ADMIN_IDENTITY_ID
  ADMIN_CLIENT_ID: graph.outputs.ADMIN_CLIENT_ID
  VOICE_DEMO_ORIGIN: graph.outputs.VOICE_DEMO_ORIGIN
  JOURNEY_STORAGE_URL: graph.outputs.JOURNEY_STORAGE_URL
  STRATEGY_PUBLISHER_PRINCIPAL_ID: graph.outputs.STRATEGY_PUBLISHER_PRINCIPAL_ID
  STRATEGY_FAIRNESS_PRINCIPAL_ID: graph.outputs.STRATEGY_FAIRNESS_PRINCIPAL_ID
  STRATEGY_RUNTIME_PRINCIPAL_ID: graph.outputs.STRATEGY_RUNTIME_PRINCIPAL_ID
  STRATEGY_IDENTITY_ID: graph.outputs.STRATEGY_IDENTITY_ID
  STRATEGY_PRINCIPAL_ID: graph.outputs.STRATEGY_PRINCIPAL_ID
  STRATEGY_CLIENT_ID: graph.outputs.STRATEGY_CLIENT_ID
  FAIRNESS_IDENTITY_ID: graph.outputs.FAIRNESS_IDENTITY_ID
  FAIRNESS_CLIENT_ID: graph.outputs.FAIRNESS_CLIENT_ID
  PIPELINE_IDENTITY_ID: graph.outputs.PIPELINE_IDENTITY_ID
  PIPELINE_CLIENT_ID: graph.outputs.PIPELINE_CLIENT_ID
  DEV_ISSUER_IDENTITY_ID: graph.outputs.DEV_ISSUER_IDENTITY_ID
  DEV_ISSUER_PRINCIPAL_ID: graph.outputs.DEV_ISSUER_PRINCIPAL_ID
  DEV_ISSUER_CLIENT_ID: graph.outputs.DEV_ISSUER_CLIENT_ID
}
