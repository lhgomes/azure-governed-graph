param location string
param tags object
param token string
// Subnets created outside this template that must survive redeployment: the vnet declares its subnets inline, so
// ARM deletes any subnet that is not listed. Empty for a standard or net-new deployment.
param retainedSubnets array = []
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
    subnets: concat([
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
    ], map(retainedSubnets, subnet => {
      name: subnet.name
      properties: {
        addressPrefix: subnet.addressPrefix
        networkSecurityGroup: empty(subnet.networkSecurityGroupId) ? null : { id: subnet.networkSecurityGroupId }
        privateEndpointNetworkPolicies: 'Disabled'
      }
    }))
  }
}

output virtualNetworkId string = vnet.id
output infrastructureSubnetId string = vnet.properties.subnets[0].id
output apimSubnetId string = vnet.properties.subnets[1].id
output privateEndpointSubnetId string = vnet.properties.subnets[2].id
