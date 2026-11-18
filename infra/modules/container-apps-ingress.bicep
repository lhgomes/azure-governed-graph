param location string
param tags object
param token string
param privateEndpointSubnetId string
param virtualNetworkId string

resource environment 'Microsoft.App/managedEnvironments@2025-07-01' existing = {
  name: 'cae-gg-${token}'
}

resource endpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-gg-container-apps-${token}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'container-apps'
        properties: {
          privateLinkServiceId: environment.id
          groupIds: [ 'managedEnvironments' ]
        }
      }
    ]
  }
}

module privateDns 'private-dns.bicep' = {
  name: 'container-apps-dns'
  params: {
    zoneName: environment.properties.defaultDomain
    privateEndpointNicName: last(split(endpoint.properties.networkInterfaces[0].id, '/'))
    virtualNetworkId: virtualNetworkId
  }
}

output privateIp string = privateDns.outputs.privateIp
