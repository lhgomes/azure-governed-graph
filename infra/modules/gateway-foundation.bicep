param location string
param tags object
param token string
param publisherEmail string
param apimSubnetId string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: 'apim-gg-${token}'
  location: location
  tags: tags
  sku: { name: 'StandardV2', capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: 'GovernedGraph synthetic fund'
    developerPortalStatus: 'Disabled'
    legacyPortalStatus: 'Disabled'
    natGatewayState: 'Enabled'
    publicNetworkAccess: 'Enabled'
    virtualNetworkType: 'External'
    virtualNetworkConfiguration: { subnetResourceId: apimSubnetId }
  }
}
output gatewayUrl string = 'https://${apim.name}.azure-api.net'
