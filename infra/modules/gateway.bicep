// APIM facade = anti-corruption layer over the administrator + AI gateway.
// Policies: validate JWT, derive the authenticated member from the token (member scoping),
// reject unknown fields (B7), rate limit per consumer, retry + circuit breaker, wiretap to App Insights.
param token string
param memberValuesBackendUrl string
param strategyServiceBackendUrl string
param orchestratorBackendUrl string
param appInsightsName string
param memberTokenIssuer string
param memberTokenAudience string
param memberTokenPublicJwk object
param deployAdminConsole bool = false
param adminBackendUrl string = ''

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: 'apim-gg-${token}'
}

resource tenantNv 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'tenant-id'
  properties: { displayName: 'tenant-id', value: subscription().tenantId }
}

var memberTokenConfig = {
  'member-token-issuer': memberTokenIssuer
  'member-token-audience': memberTokenAudience
  'member-token-modulus': memberTokenPublicJwk.n
  'member-token-exponent': memberTokenPublicJwk.e
}
resource memberTokenNamedValues 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for item in items(memberTokenConfig): {
  parent: apim
  name: item.key
  properties: { displayName: item.key, value: item.value, secret: false }
}]

resource insights 'Microsoft.Insights/components@2020-02-02' existing = { name: appInsightsName }
resource metricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, apim.id, 'metrics-publisher')
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
  }
}
resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'member-values-insights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: insights.id
    credentials: {
      connectionString: insights.properties.ConnectionString
      identityClientId: 'SystemAssigned'
    }
  }
  dependsOn: [ metricsPublisher ]
}

resource valuesBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'administrator-values'
  properties: {
    url: '${memberValuesBackendUrl}/members'
    protocol: 'http'
    circuitBreaker: {
      rules: [ {
        name: 'administrator-breaker'
        failureCondition: { count: 5, interval: 'PT1M', statusCodeRanges: [ { min: 500, max: 599 } ] }
        tripDuration: 'PT1M'
        acceptRetryAfter: true
      } ]
    }
  }
}

resource valuesApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'member-values'
  properties: {
    displayName: 'Member Values (read-only facade)'
    path: 'members'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: '${memberValuesBackendUrl}/members'
  }
}

resource getValues 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: valuesApi
  name: 'get-member-values'
  properties: {
    displayName: 'Get member values'
    method: 'GET'
    urlTemplate: '/{memberRef}/values'
    templateParameters: [ { name: 'memberRef', type: 'string', required: true } ]
  }
}

resource rejectValuesPost 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: valuesApi
  name: 'reject-unsupported-methods'
  properties: { displayName: 'Read-only facade (POST)', method: 'POST', urlTemplate: '/*' }
}

var blockedValuesMethods = [ 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'TRACE' ]
resource rejectValuesWrites 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for method in blockedValuesMethods: {
  parent: valuesApi
  name: 'reject-${toLower(method)}'
  properties: { displayName: 'Read-only facade (${method})', method: method, urlTemplate: '/*' }
  dependsOn: [ rejectValuesPost ]
}]

resource valuesPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: valuesApi
  name: 'policy'
  properties: { format: 'rawxml', value: loadTextContent('../policies/member-values.xml') }
  dependsOn: [ tenantNv, valuesBackend, memberTokenNamedValues ]
}

resource valuesDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: valuesApi
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
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
}

resource strategyApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'strategy-service'
  properties: {
    displayName: 'Strategy Service'
    path: 'strategies'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: '${strategyServiceBackendUrl}/strategies'
  }
}

resource rankedOp 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: strategyApi
  name: 'get-ranked'
  properties: { displayName: 'Get ranked strategies', method: 'POST', urlTemplate: '/ranked' }
}

resource strategyPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: strategyApi
  name: 'policy'
  properties: { format: 'rawxml', value: loadTextContent('../policies/strategy-request.xml') }
}

resource strategyAdminApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'strategy-administration'
  properties: {
    displayName: 'Strategy administration (Entra application roles)'
    path: 'strategy-admin'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: strategyServiceBackendUrl
  }
}
var strategyAdminOperations = [
  { name: 'read-catalogue', method: 'GET', path: '/catalogue' }
  { name: 'catalogue-decision', method: 'POST', path: '/catalogue-decisions' }
  { name: 'catalogue-publication', method: 'POST', path: '/catalogue-publications' }
  { name: 'read-snapshots', method: 'GET', path: '/snapshots' }
  { name: 'publish-snapshot', method: 'POST', path: '/snapshots' }
  { name: 'suspend', method: 'POST', path: '/suspensions' }
  { name: 'freeze', method: 'POST', path: '/freeze' }
  { name: 'revert-to-static', method: 'POST', path: '/revert-to-static' }
  { name: 'human-reinstatement', method: 'POST', path: '/reinstatements' }
  { name: 'owner-decision', method: 'POST', path: '/owner-overrides' }
  { name: 'owner-order-publication', method: 'POST', path: '/owner-order-publications' }
  { name: 'human-recovery-readiness', method: 'GET', path: '/freeze-recovery' }
  { name: 'human-freeze-recovery', method: 'POST', path: '/freeze-recovery' }
]
resource strategyAdminOps 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for op in strategyAdminOperations: {
  parent: strategyAdminApi
  name: op.name
  properties: { displayName: op.name, method: op.method, urlTemplate: op.path }
}]

// Administration console: every route is listed explicitly, so anything else returns 404 at the gateway.
// Authentication is enforced by the app (Entra group membership, when configured) and the Strategy API's own roles.
resource adminApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = if (deployAdminConsole) {
  parent: apim
  name: 'administration-console'
  properties: {
    displayName: 'Administration console'
    path: 'admin'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: adminBackendUrl
  }
}
var adminOperations = [
  { name: 'page', method: 'GET', path: '/', parameters: [] }
  { name: 'asset', method: 'GET', path: '/assets/{name}', parameters: [ 'name' ] }
  { name: 'access', method: 'GET', path: '/api/access', parameters: [] }
  { name: 'overview', method: 'GET', path: '/api/overview', parameters: [] }
  { name: 'journeys', method: 'GET', path: '/api/journeys', parameters: [] }
  { name: 'journey', method: 'GET', path: '/api/journeys/{journeyId}', parameters: [ 'journeyId' ] }
  { name: 'trace', method: 'GET', path: '/api/traces/{journeyId}', parameters: [ 'journeyId' ] }
  { name: 'action', method: 'POST', path: '/api/actions', parameters: [] }
  { name: 'owner-action', method: 'POST', path: '/api/owner-actions', parameters: [] }
  { name: 'catalogue-action', method: 'POST', path: '/api/catalogue', parameters: [] }
  { name: 'proposals', method: 'GET', path: '/api/proposals', parameters: [] }
  { name: 'proposal', method: 'GET', path: '/api/proposals/{proposalId}', parameters: [ 'proposalId' ] }
  { name: 'proposal-decision', method: 'POST', path: '/api/proposals/decision', parameters: [] }
]
resource adminOps 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for op in adminOperations: if (deployAdminConsole) {
  parent: adminApi
  name: op.name
  properties: {
    displayName: op.name
    method: op.method
    urlTemplate: op.path
    templateParameters: [for parameter in op.parameters: { name: parameter, type: 'string', required: true }]
  }
}]

resource journeysApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'journeys'
  properties: {
    displayName: 'Member journeys'
    path: 'journey'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: orchestratorBackendUrl
  }
}

resource startJourney 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: journeysApi
  name: 'start'
  properties: { displayName: 'Start journey', method: 'POST', urlTemplate: '/journeys' }
}

resource startAdminTestJourney 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = if (deployAdminConsole) {
  parent: journeysApi
  name: 'start-admin-test'
  properties: { displayName: 'Start authenticated synthetic style test', method: 'POST', urlTemplate: '/admin-test/journeys' }
}

resource adminTesting 'Microsoft.ApiManagement/service/apis@2024-05-01' = if (deployAdminConsole) {
  parent: apim
  name: 'admin-testing'
  properties: {
    displayName: 'Authenticated synthetic admin testing'
    path: 'admin-testing'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: '${orchestratorBackendUrl}/admin-testing'
  }
}
var adminTestingOperations = [
  { name: 'config', method: 'GET', path: '/config' }
  { name: 'authorize', method: 'POST', path: '/authorize' }
]
resource adminTestingRoutes 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for operation in adminTestingOperations: if (deployAdminConsole) {
  parent: adminTesting
  name: operation.name
  properties: { displayName: operation.name, method: operation.method, urlTemplate: operation.path }
}]
resource adminTestStream 'Microsoft.ApiManagement/service/apis@2024-05-01' = if (deployAdminConsole) {
  parent: apim
  name: 'admin-test-stream'
  properties: {
    displayName: 'Authenticated synthetic admin conversation stream'
    apiType: 'websocket'
    type: 'websocket'
    path: 'admin-test-stream'
    protocols: [ 'wss' ]
    subscriptionRequired: false
    serviceUrl: '${replace(orchestratorBackendUrl, 'https://', 'wss://')}/admin-test-stream'
  }
}

resource advisorQueue 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: journeysApi
  name: 'advisor-queue'
  properties: {
    displayName: 'Read synthetic advisor queue for authenticated journey'
    method: 'GET'
    urlTemplate: '/journeys/{journeyId}/advisor-queue'
    templateParameters: [ { name: 'journeyId', type: 'string', required: true } ]
  }
}

resource memberExplainabilityReport 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: journeysApi
  name: 'member-explainability-report'
  properties: {
    displayName: 'Read step-up verified member explainability report'
    method: 'GET'
    urlTemplate: '/members/{memberRef}/explainability-report'
    templateParameters: [ { name: 'memberRef', type: 'string', required: true } ]
  }
}

resource journeysPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: journeysApi
  name: 'policy'
  properties: { format: 'rawxml', value: loadTextContent('../policies/member-journey.xml') }
  dependsOn: [ memberTokenNamedValues ]
}
resource journeyTurn 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: journeysApi
  name: 'turn'
  properties: {
    displayName: 'Journey turn'
    method: 'POST'
    urlTemplate: '/journeys/{journeyId}/turns'
    templateParameters: [ { name: 'journeyId', type: 'string', required: true } ]
  }
}

var healthBackends = concat([
  { name: 'orchestrator', url: orchestratorBackendUrl }
  { name: 'strategy-service', url: strategyServiceBackendUrl }
  { name: 'member-values', url: memberValuesBackendUrl }
], deployAdminConsole ? [ { name: 'admin', url: adminBackendUrl } ] : [])

resource healthApis 'Microsoft.ApiManagement/service/apis@2024-05-01' = [for backend in healthBackends: {
  parent: apim
  name: 'health-${backend.name}'
  properties: {
    displayName: '${backend.name} health'
    path: 'health/${backend.name}'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: backend.url
  }
}]

resource healthOperations 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for (backend, i) in healthBackends: {
  name: 'healthz'
  parent: healthApis[i]
  properties: { displayName: '${backend.name} health', method: 'GET', urlTemplate: '/healthz' }
}]

output memberValuesApiUrl string = apim.properties.gatewayUrl
output strategyServiceUrl string = apim.properties.gatewayUrl
output orchestratorUrl string = '${apim.properties.gatewayUrl}/journey'
output gatewayUrl string = apim.properties.gatewayUrl
output apimName string = apim.name
