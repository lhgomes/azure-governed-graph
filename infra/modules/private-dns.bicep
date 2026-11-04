param zoneName string
param staticIp string
param virtualNetworkId string

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
  properties: { ttl: 300, aRecords: [ { ipv4Address: staticIp } ] }
}]
