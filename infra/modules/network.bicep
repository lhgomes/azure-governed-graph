param location string
param tags object
param token string

resource mlOutboundIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-gg-ml-outbound-${token}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}
resource mlNat 'Microsoft.Network/natGateways@2024-05-01' = {
  name: 'nat-gg-ml-${token}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    idleTimeoutInMinutes: 4
    publicIpAddresses: [ { id: mlOutboundIp.id } ]
  }
}

resource apimNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-gg-apim-${token}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowKeyVault'
        properties: {
          priority: 100
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'AzureKeyVault'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-gg-${token}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '10.33.0.0/16' ] }
    subnets: [
      {
        name: 'container-apps'
        properties: {
          addressPrefix: '10.33.0.0/23'
          delegations: [
            { name: 'container-apps', properties: { serviceName: 'Microsoft.App/environments' } }
          ]
        }
      }
      {
        name: 'apim'
        properties: {
          addressPrefix: '10.33.2.0/24'
          networkSecurityGroup: { id: apimNsg.id }
          delegations: [
            { name: 'apim', properties: { serviceName: 'Microsoft.Web/serverFarms' } }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.33.3.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'ml-compute'
        properties: {
          addressPrefix: '10.33.4.0/24'
          defaultOutboundAccess: false
          natGateway: { id: mlNat.id }
        }
      }
    ]
  }
}

output virtualNetworkId string = vnet.id
output infrastructureSubnetId string = vnet.properties.subnets[0].id
output apimSubnetId string = vnet.properties.subnets[1].id
output privateEndpointSubnetId string = vnet.properties.subnets[2].id
output mlComputeSubnetId string = vnet.properties.subnets[3].id
