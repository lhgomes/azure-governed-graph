param location string
param tags object
param token string

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-gg-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90
    features: { disableLocalAuth: true }
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-gg-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: law.id, DisableLocalAuth: true }
}

output logAnalyticsId string = law.id
output logAnalyticsName string = law.name
output logAnalyticsCustomerId string = law.properties.customerId
module workbook 'observability-workbook.bicep' = {
  name: 'observability-workbook'
  params: { location: location, tags: tags, logAnalyticsId: law.id }
}
output appInsightsId string = appi.id
// Service-generated routing metadata, not an authentication secret; never a root output.
output appInsightsRouting string = appi.properties.ConnectionString
