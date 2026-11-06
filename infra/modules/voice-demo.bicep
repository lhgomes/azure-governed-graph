param location string
param token string
param enabled bool = false
param image string
param issuerIdentityId string
param issuerClientId string
param runtimePrincipalId string
param runtimeClientId string
param clientId string
param operatorId string
param origin string
param memberTokenIssuer string
param orchestratorUrl string

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = { name: 'cae-gg-${token}' }
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: 'kv-gg-${take(token, 13)}' }
resource issuer 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-gg-voice-issuer'
  location: location
  tags: { data: 'synthetic', purpose: 'dev-only-voice-issuer' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${issuerIdentityId}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 8000, transport: 'http', allowInsecure: false }
      registries: [ { server: 'crgg${take(token, 16)}.azurecr.io', identity: issuerIdentityId } ]
    }
    template: {
      containers: [ {
        name: 'issuer'
        image: image
        env: [
          { name: 'APP_MODULE', value: 'governedgraph.dev.member_issuer_api:app' }
          { name: 'AZURE_ENV_NAME', value: 'gg-dev' }
          { name: 'GOVERNEDGRAPH_DEPLOY_VOICE_DEMO', value: enabled ? 'true' : 'false' }
          { name: 'GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER', value: 'true' }
          { name: 'AZURE_CLIENT_ID', value: issuerClientId }
          { name: 'AZURE_KEY_VAULT_URL', value: vault.properties.vaultUri }
          { name: 'MEMBER_TOKEN_ISSUER', value: memberTokenIssuer }
          { name: 'STRATEGY_TENANT_ID', value: subscription().tenantId }
          { name: 'VOICE_DEMO_CLIENT_ID', value: clientId }
          { name: 'VOICE_BRIDGE_PRINCIPAL_ID', value: runtimePrincipalId }
          { name: 'VOICE_BRIDGE_CLIENT_ID', value: runtimeClientId }
        ]
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
      } ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: 'apim-gg-${token}' }
resource page 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'voice-demo'
  properties: {
    displayName: 'Dev-only assigned presenter channel'
    path: 'demo'
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: '${orchestratorUrl}/demo'
  }
}
var operations = [
  { name: 'page', method: 'GET', path: '/' }
  { name: 'config', method: 'GET', path: '/config' }
  { name: 'authorize', method: 'POST', path: '/authorize' }
]
resource pageOperations 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for operation in operations: {
  parent: page
  name: operation.name
  properties: { displayName: operation.name, method: operation.method, urlTemplate: operation.path }
}]
resource stream 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'voice-demo-stream'
  properties: {
    displayName: 'Dev-only authenticated voice media'
    apiType: 'websocket'
    type: 'websocket'
    path: 'demo-stream'
    protocols: [ 'wss' ]
    subscriptionRequired: false
    serviceUrl: '${replace(orchestratorUrl, 'https://', 'wss://')}/demo-stream'
  }
}
var pageXml = replace(replace(replace(replace(replace(replace(loadTextContent('../policies/voice-demo.xml'), '__TENANT__', subscription().tenantId), '__CLIENT__', clientId), '__OPERATOR__', operatorId), '__ORIGIN__', origin), '__DISABLED__', enabled ? 'false' : 'true'), '\r', '')
var streamXml = replace(replace(replace(replace(replace(replace(loadTextContent('../policies/voice-demo-stream.xml'), '__TENANT__', subscription().tenantId), '__CLIENT__', clientId), '__OPERATOR__', operatorId), '__ORIGIN__', origin), '__DISABLED__', enabled ? 'false' : 'true'), '\r', '')
resource pagePolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: page
  name: 'policy'
  properties: { format: 'rawxml', value: pageXml }
  dependsOn: [ pageOperations ]
}
resource handshake 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' existing = {
  parent: stream
  name: 'onHandshake'
}
resource streamPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = {
  parent: handshake
  name: 'policy'
  properties: { format: 'rawxml', value: streamXml }
}
