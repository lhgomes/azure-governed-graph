param location string
param tags object
param token string
param workspaceId string
param subnetId string
param virtualNetworkId string

var zoneNames = [ 'privatelink.api.azureml.ms', 'privatelink.notebooks.azure.net' ]
resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for zone in zoneNames: {
  name: zone
  location: 'global'
}]
resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zone, i) in zoneNames: {
  parent: zones[i]
  name: 'governedgraph'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: virtualNetworkId } }
}]
resource endpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-gg-ml-workspace-${token}'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [
      {
        name: 'ml-workspace'
        properties: { privateLinkServiceId: workspaceId, groupIds: [ 'amlworkspace' ] }
      }
    ]
  }
}
resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: endpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for (zone, i) in zoneNames: {
      name: 'ml-${i}'
      properties: { privateDnsZoneId: zones[i].id }
    }]
  }
}
