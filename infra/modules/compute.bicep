param location string
param tags object
@minLength(1)
param token string
param runtimeIdentityId string
param runtimeIdentityClientId string
param runtimePrincipalId string
param memberValuesIdentityId string
param memberValuesClientId string
param memberValuesPrincipalId string
param strategyIdentityId string
param strategyClientId string
param strategyPrincipalId string
param strategyEnv object
param orchestratorEnv object
param orchestratorImage string
param strategyServiceImage string
param memberValuesImage string
param memberValuesModule string
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
param deployAdminConsole bool = false
param adminImage string
param adminIdentityId string = ''
param adminClientId string = ''
param adminPrincipalId string = ''
param adminEnv object = {}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: 'crgg${take(token, 16)}'
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
resource memberValuesAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, memberValuesPrincipalId, acrPullRole)
  properties: {
    principalId: memberValuesPrincipalId
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

resource adminAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAdminConsole) {
  scope: acr
  name: guid(acr.id, adminPrincipalId, acrPullRole)
  properties: {
    principalId: adminPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
  }
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-gg-${token}'
}

var serviceEnv = [for k in items(env): { name: k.key, value: k.value }]
var strategyEnvList = [for k in items(strategyEnv): { name: k.key, value: k.value }]
var orchestratorEnvEntries = [for k in items(orchestratorEnv): { name: k.key, value: k.value }]
var orchestratorEnvList = concat(orchestratorEnvEntries, [
  { name: 'VOICE_DEMO_ISSUER_URL', value: 'https://ca-gg-voice-issuer.internal.${cae.properties.defaultDomain}' }
])

var apps = [
  { name: 'orchestrator', image: orchestratorImage, module: 'governedgraph.orchestrator.api:app', external: true, minReplicas: 1, identityId: runtimeIdentityId, clientId: runtimeIdentityClientId }
  { name: 'strategy-service', image: strategyServiceImage, module: 'governedgraph.strategy_service.api:app', external: true, minReplicas: 2, identityId: strategyIdentityId, clientId: strategyClientId }
  { name: 'member-values', image: memberValuesImage, module: memberValuesModule, external: true, minReplicas: 1, identityId: memberValuesIdentityId, clientId: memberValuesClientId }
]

resource ca 'Microsoft.App/containerApps@2024-03-01' = [for a in apps: {
  name: 'ca-gg-${a.name}-${take(token, 6)}'
  location: location
  tags: union(tags, { 'azd-service-name': a.name, 'gg-agent-role': a.name })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${a.identityId}': {} }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: { external: a.external, targetPort: 8000, transport: 'auto' }
      registries: [ { server: acr.properties.loginServer, identity: a.identityId } ]
    }
    template: {
      containers: [ {
        name: a.name
        image: a.image
        env: concat(serviceEnv, a.name == 'strategy-service' ? strategyEnvList : [], a.name == 'orchestrator' ? orchestratorEnvList : [], [
          { name: 'AZURE_CLIENT_ID', value: a.clientId }
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
  dependsOn: [ runtimeAcrPull, strategyAcrPull, memberValuesAcrPull ]
}]

var adminEnvList = [for k in items(adminEnv): { name: k.key, value: k.value }]

// Administration console: its own identity and a minimal environment; a single replica because the
// per-process session capability must match the replica that served the page.
resource admin 'Microsoft.App/containerApps@2024-03-01' = if (deployAdminConsole) {
  name: 'ca-gg-admin-${take(token, 6)}'
  location: location
  tags: union(tags, { 'azd-service-name': 'admin', 'gg-agent-role': 'admin' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${adminIdentityId}': {} }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [ { server: acr.properties.loginServer, identity: adminIdentityId } ]
    }
    template: {
      containers: [ {
        name: 'admin'
        image: adminImage
        env: concat([
          { name: 'GOVERNEDGRAPH_MODE', value: 'azure' }
          { name: 'APP_MODULE', value: 'governedgraph.admin.api:app' }
          { name: 'AZURE_CLIENT_ID', value: adminClientId }
          { name: 'GOVERNEDGRAPH_SERVICE_NAME', value: 'gg-admin' }
        ], adminEnvList)
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [ { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 } } ]
      } ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [ adminAcrPull ]
}

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
output adminUrl string = deployAdminConsole ? 'https://${admin!.properties.configuration.ingress.fqdn}' : ''
output contactCentreEndpoint string = deploySyntheticContactCentre ? 'https://${contact!.properties.configuration.ingress.fqdn}' : contactCentreUrl
