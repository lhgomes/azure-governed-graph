// Microsoft Foundry models/Voice Live and a separate French Prompt Shields resource.
// The Foundry project hosts Agent Service agents. Verify API versions with the
// Learn MCP server before first deploy (prompt 01) - Foundry resource APIs evolve quickly.
param location string
param tags object
param token string
param counsellorModelName string
param counsellorModelVersion string
param compactModelName string
param compactModelVersion string
param embeddingModelName string
param embeddingModelVersion string
@minLength(1)
@maxLength(64)
param decisionDeployment string
param appInsightsId string
@secure()
param appInsightsRouting string

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'cs-gg-${token}'
  location: 'francecentral'
  tags: tags
  kind: 'ContentSafety'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'cs-gg-${token}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource aiServices 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: 'ais-gg-${token}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'ais-gg-${token}'
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled' // BYO VNet + private endpoints for production
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: aiServices
  name: 'gg-counsellor'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'GovernedGraph Counsellor', description: 'Synthetic sample fund - GovernedGraph reference agent' }
  dependsOn: [ decision ]
}

resource insightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'gg-application-insights'
  properties: {
    category: 'AppInsights'
    // AppInsights requires this discriminator; the published connection schema omits it.
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    target: appInsightsId
    useWorkspaceManagedIdentity: true
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsId
      ApplicationInsightsConnectionString: appInsightsRouting
    }
  }
}

resource guardrailPolicy 'Microsoft.CognitiveServices/accounts/raiPolicies@2024-10-01' = {
  parent: aiServices
  name: 'gg-blocking-guardrails'
  properties: {
    basePolicyName: 'Microsoft.Default'
    mode: 'Blocking'
    contentFilters: [
      { name: 'Jailbreak', source: 'Prompt', enabled: true, blocking: true }
      { name: 'Indirect Attack', source: 'Prompt', enabled: true, blocking: true }
      { name: 'Hate', source: 'Prompt', enabled: true, blocking: true, severityThreshold: 'Medium' }
      { name: 'Sexual', source: 'Prompt', enabled: true, blocking: true, severityThreshold: 'Medium' }
      { name: 'Violence', source: 'Prompt', enabled: true, blocking: true, severityThreshold: 'Medium' }
      { name: 'Selfharm', source: 'Prompt', enabled: true, blocking: true, severityThreshold: 'Medium' }
      { name: 'Hate', source: 'Completion', enabled: true, blocking: true, severityThreshold: 'Medium' }
      { name: 'Sexual', source: 'Completion', enabled: true, blocking: true, severityThreshold: 'Medium' }
      { name: 'Violence', source: 'Completion', enabled: true, blocking: true, severityThreshold: 'Medium' }
      { name: 'Selfharm', source: 'Completion', enabled: true, blocking: true, severityThreshold: 'Medium' }
    ]
  }
}

resource counsellor 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: 'gpt-counsellor'
  sku: { name: 'DataZoneStandard', capacity: 50 } // EU data zone (A-2)
  properties: {
    model: { format: 'OpenAI', name: counsellorModelName, version: counsellorModelVersion }
    raiPolicyName: guardrailPolicy.name
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource compact 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: 'gpt-compact'
  sku: { name: 'DataZoneStandard', capacity: 50 }
  properties: {
    model: { format: 'OpenAI', name: compactModelName, version: compactModelVersion }
    raiPolicyName: guardrailPolicy.name
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [ counsellor ]
}

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: 'embedding'
  sku: { name: 'Standard', capacity: 50 }
  properties: {
    model: { format: 'OpenAI', name: embeddingModelName, version: embeddingModelVersion }
    raiPolicyName: 'Microsoft.DefaultV2'
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [ compact ]
}

resource decision 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: aiServices
  name: decisionDeployment
  sku: { name: 'DataZoneStandard', capacity: 10 }
  properties: {
    model: { format: 'Microsoft', name: 'Microsoft-Decision-1.1', version: '1' }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [ embedding ]
}

output aiServicesName string = aiServices.name
output contentSafetyName string = contentSafety.name
output contentSafetyEndpoint string = 'https://${contentSafety.properties.customSubDomainName}.cognitiveservices.azure.com'
output aiServicesEndpoint string = aiServices.properties.endpoint
output embeddingEndpoint string = 'https://${aiServices.properties.customSubDomainName}.openai.azure.com'
output projectEndpoint string = 'https://${aiServices.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'
output projectResourceId string = project.id
output decisionDeployment string = decision.name
output appInsightsLinked string = appInsightsId
