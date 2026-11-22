param apimName string
param projectEndpoint string
@minLength(1)
param runtimeClientId string
@description('True once delivery has published the counsellor agents; false keeps the front door closed.')
param agentsReady bool
@minValue(1000)
param conversationTokensPerMinute int = 12000

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: apimName }
var configuration = {
  'model-project-endpoint': projectEndpoint
  'model-runtime-client-id': runtimeClientId
}

resource values 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for item in items(configuration): if (!empty(item.value)) {
  parent: apim
  name: item.key
  properties: { displayName: item.key, value: item.value, secret: false }
}]
resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'governed-model-responses'
  properties: {
    displayName: 'Governed Foundry Responses (approved agent front door)'
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
var approvedAgentPolicy = {
  format: 'xml', value: replace(loadTextContent('../policies/model-responses.xml'), '__CONVERSATION_TOKENS_PER_MINUTE__', string(conversationTokensPerMinute))
}
var unavailableAgentPolicy = {
  format: 'xml', value: loadTextContent('../policies/model-unavailable.xml')
}
resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: agentsReady ? approvedAgentPolicy : unavailableAgentPolicy
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
