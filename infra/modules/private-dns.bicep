param zoneName string
param privateEndpointNicName string
param virtualNetworkId string

resource endpointNic 'Microsoft.Network/networkInterfaces@2024-05-01' existing = {
  name: privateEndpointNicName
}

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: zoneName
  location: 'global'
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'container-apps'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: virtualNetworkId } }
}

resource records 'Microsoft.Network/privateDnsZones/A@2024-06-01' = [for record in [ '@', '*' ]: {
  parent: zone
  name: record
  properties: { ttl: 300, aRecords: [ { ipv4Address: endpointNic.properties.ipConfigurations[0].properties.privateIPAddress } ] }
}]

output privateIp string = endpointNic.properties.ipConfigurations[0].properties.privateIPAddress
