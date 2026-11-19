param apimName string
param projectEndpoint string
@minLength(1)
param runtimeClientId string
param mainAgentVersion string
param compactAgentVersion string
param understandingAgentVersion string
@maxLength(3000)
param writerApprovalJson string = ''
@minValue(1000)
param conversationTokensPerMinute int = 12000

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: apimName }
var writerApproval = empty(writerApprovalJson) ? {} : json(writerApprovalJson)
var configuration = {
  'model-project-endpoint': projectEndpoint
  'model-writer-approval': base64(string(writerApproval))
  'model-runtime-client-id': runtimeClientId
  'model-main-agent-version': mainAgentVersion
  'model-compact-agent-version': compactAgentVersion
  'model-understanding-agent-version': understandingAgentVersion
}
var agentsReady = !empty(mainAgentVersion) && !empty(compactAgentVersion) && !empty(understandingAgentVersion)

resource values 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for item in items(configuration): if (!empty(item.value)) {
  parent: apim
  name: item.key
  properties: { displayName: item.key, value: item.value, secret: false }
}]
resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'governed-model-responses'
  properties: {
    displayName: 'Governed Foundry Responses (pinned agent front door)'
    path: 'foundry/openai/v1'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: '${projectEndpoint}/openai/v1'
  }
}
resource operation 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'create-response'
  properties: { displayName: 'Create governed response', method: 'POST', urlTemplate: '/responses' }
  dependsOn: [ policy ]
}
var pinnedAgentPolicy = {
  format: 'xml', value: replace(loadTextContent('../policies/model-responses.xml'), '__CONVERSATION_TOKENS_PER_MINUTE__', string(conversationTokensPerMinute))
}
var unavailableAgentPolicy = {
  format: 'xml', value: loadTextContent('../policies/model-unavailable.xml')
}
resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: agentsReady ? pinnedAgentPolicy : unavailableAgentPolicy
  dependsOn: [ values ]
}
var tracedApis = [ 'governed-model-responses', 'strategy-service', 'journeys' ]
resource diagnostic 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = [for name in tracedApis: {
  name: '${apim.name}/${name}/applicationinsights'
  properties: {
    loggerId: resourceId('Microsoft.ApiManagement/service/loggers', apim.name, 'member-values-insights')
    alwaysLog: 'allErrors'
    sampling: { samplingType: 'fixed', percentage: 100 }
    httpCorrelationProtocol: 'W3C'
    verbosity: 'information'
    logClientIp: false
    frontend: {
      request: { headers: [ 'X-Journey-ID', 'traceparent' ], body: { bytes: 0 } }
      response: { headers: [], body: { bytes: 0 } }
    }
    backend: {
      request: { headers: [ 'X-Journey-ID', 'traceparent' ], body: { bytes: 0 } }
      response: { headers: [], body: { bytes: 0 } }
    }
  }
  dependsOn: [ api ]
}]
