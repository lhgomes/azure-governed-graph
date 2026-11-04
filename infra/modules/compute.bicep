param location string
param tags object
@minLength(1)
param token string
param logAnalyticsId string
param infrastructureSubnetId string
param virtualNetworkId string
param runtimeIdentityId string
param runtimeIdentityClientId string
param runtimePrincipalId string
param strategyIdentityId string
param strategyClientId string
param strategyPrincipalId string
param strategyEnv object
param orchestratorEnv object
param orchestratorImage string
param strategyServiceImage string
param memberValuesImage string
param env object
param deploySyntheticContactCentre bool = false
param contactCentreImage string
param contactCentreIdentityId string = ''
param contactCentreIdentityClientId string = ''
param contactCentrePrincipalId string = ''
param contactCentreApiClientId string = ''
param contactCentreAudience string = ''
param contactCentreUrl string = ''
param contactCentreStorageUrl string

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'crgg${take(token, 16)}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

var acrPullRole = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource runtimeAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, runtimePrincipalId, acrPullRole)
  properties: {
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
  }
}
resource strategyAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, strategyPrincipalId, acrPullRole)
  properties: {
    principalId: strategyPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
  }
}
resource contactCentreAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deploySyntheticContactCentre) {
  scope: acr
  name: guid(acr.id, contactCentrePrincipalId, acrPullRole)
  properties: {
    principalId: contactCentrePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
  }
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-gg-${token}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { internal: true, infrastructureSubnetId: infrastructureSubnetId }
    workloadProfiles: [ { name: 'Consumption', workloadProfileType: 'Consumption' } ]
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'container-apps-logs'
  scope: cae
  properties: {
    workspaceId: logAnalyticsId
    logs: [ { categoryGroup: 'allLogs', enabled: true } ]
  }
}

module privateDns 'private-dns.bicep' = {
  name: 'container-apps-dns'
  params: {
    zoneName: cae.properties.defaultDomain
    staticIp: cae.properties.staticIp
    virtualNetworkId: virtualNetworkId
  }
}

var serviceEnv = [for k in items(env): { name: k.key, value: k.value }]
var strategyEnvList = [for k in items(strategyEnv): { name: k.key, value: k.value }]
var orchestratorEnvEntries = [for k in items(orchestratorEnv): { name: k.key, value: k.value }]
var orchestratorEnvList = concat(orchestratorEnvEntries, [
  { name: 'VOICE_DEMO_ISSUER_URL', value: 'https://ca-gg-voice-issuer.internal.${cae.properties.defaultDomain}' }
])

var apps = [
  { name: 'orchestrator', image: orchestratorImage, module: 'governedgraph.orchestrator.api:app', external: true, minReplicas: 1 }
  { name: 'strategy-service', image: strategyServiceImage, module: 'governedgraph.strategy_service.api:app', external: true, minReplicas: 2 }
  { name: 'member-values', image: memberValuesImage, module: 'pension_wtp.member_values_api.app:app', external: true, minReplicas: 1 }
]

resource ca 'Microsoft.App/containerApps@2024-03-01' = [for a in apps: {
  name: 'ca-gg-${a.name}-${take(token, 6)}'
  location: location
  tags: union(tags, { 'azd-service-name': a.name, 'gg-agent-role': a.name })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${a.name == 'strategy-service' ? strategyIdentityId : runtimeIdentityId}': {} }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: { external: a.external, targetPort: 8000, transport: 'auto' }
      registries: [ { server: acr.properties.loginServer, identity: a.name == 'strategy-service' ? strategyIdentityId : runtimeIdentityId } ]
    }
    template: {
      containers: [ {
        name: a.name
        image: a.image
        env: concat(serviceEnv, a.name == 'strategy-service' ? strategyEnvList : [], a.name == 'orchestrator' ? orchestratorEnvList : [], [
          { name: 'AZURE_CLIENT_ID', value: a.name == 'strategy-service' ? strategyClientId : runtimeIdentityClientId }
          { name: 'APP_MODULE', value: a.module }
          { name: 'STRATEGY_SERVICE_URL', value: 'https://apim-gg-${token}.azure-api.net' }
          { name: 'MEMBER_VALUES_API_URL', value: 'https://apim-gg-${token}.azure-api.net' }
        ], a.name == 'orchestrator' ? [
          { name: 'GOVERNEDGRAPH_ORCHESTRATOR_URL', value: 'https://ca-gg-orchestrator-${take(token, 6)}.${cae.properties.defaultDomain}' }
          { name: 'CONTACT_CENTRE_URL', value: deploySyntheticContactCentre ? 'https://${contact!.properties.configuration.ingress.fqdn}' : contactCentreUrl }
          { name: 'CONTACT_CENTRE_AUDIENCE', value: contactCentreAudience }
        ] : [])
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [ { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 } } ]
      } ]
      scale: { minReplicas: a.minReplicas, maxReplicas: 10 }
    }
  }
  dependsOn: [ runtimeAcrPull, strategyAcrPull ]
}]

resource contact 'Microsoft.App/containerApps@2024-03-01' = if (deploySyntheticContactCentre) {
  name: 'ca-gg-contact-centre-${take(token, 6)}'
  location: location
  tags: union(tags, { 'azd-service-name': 'contact-centre', purpose: 'dev-only-synthetic-contact-centre' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${contactCentreIdentityId}': {} }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: { external: false, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [ { server: acr.properties.loginServer, identity: contactCentreIdentityId } ]
    }
    template: {
      containers: [
        {
          name: 'contact-centre'
          image: contactCentreImage
          env: [
            { name: 'APP_MODULE', value: 'governedgraph.contact_centre.api:app' }
            { name: 'GOVERNEDGRAPH_MODE', value: 'azure' }
            { name: 'AZURE_CLIENT_ID', value: contactCentreIdentityClientId }
            { name: 'CONTACT_CENTRE_STORAGE_URL', value: contactCentreStorageUrl }
            { name: 'CONTACT_CENTRE_CLIENT_ID', value: contactCentreApiClientId }
            { name: 'STRATEGY_TENANT_ID', value: subscription().tenantId }
            { name: 'STRATEGY_RUNTIME_PRINCIPAL_ID', value: runtimePrincipalId }
          ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: [ { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 } } ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
  dependsOn: [ contactCentreAcrPull ]
}

output registryName string = acr.name
output registryLoginServer string = acr.properties.loginServer
output orchestratorUrl string = 'https://${ca[0].properties.configuration.ingress.fqdn}'
output strategyServiceUrl string = 'https://${ca[1].properties.configuration.ingress.fqdn}'
output memberValuesUrl string = 'https://${ca[2].properties.configuration.ingress.fqdn}'
output contactCentreEndpoint string = deploySyntheticContactCentre ? 'https://${contact!.properties.configuration.ingress.fqdn}' : contactCentreUrl
