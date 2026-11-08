param location string
param tags object
@minLength(1)
param token string
param logAnalyticsId string
param infrastructureSubnetId string
param virtualNetworkId string

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'crgg${take(token, 16)}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-gg-${token}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { internal: true, infrastructureSubnetId: infrastructureSubnetId }
    workloadProfiles: [ { name: 'Consumption', workloadProfileType: 'Consumption' } ]
    appLogsConfiguration: { destination: 'azure-monitor' }
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
output registryName string = acr.name
output registryLoginServer string = acr.properties.loginServer
output environmentId string = cae.id
