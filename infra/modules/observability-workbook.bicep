param location string
param tags object
param logAnalyticsId string

var panels = [
  { title: 'SLOs, escalation, numeric blocks, suspensions and governed uplift', query: loadTextContent('../queries/journey-slos.kql') }
  { title: 'Observed token cost per mature resolved query (14 days)', query: loadTextContent('../queries/resolved-cost.kql') }
]
var queryItems = [for panel in panels: {
  type: 3
  content: {
    version: 'KqlItem/1.0'
    title: panel.title
    query: panel.query
    queryType: 0
    resourceType: 'microsoft.operationalinsights/workspaces'
    crossComponentResources: [ logAnalyticsId ]
    visualization: 'table'
    size: 0
  }
}]
resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(logAnalyticsId, 'gg-governed-observability')
  location: location
  kind: 'shared'
  tags: tags
  properties: {
    displayName: 'GovernedGraph governed journeys - ILLUSTRATIVE synthetic data'
    category: 'workbook'
    sourceId: logAnalyticsId
    serializedData: string({
      version: 'Notebook/1.0'
      items: concat([
        {
          type: 1
          content: {
            json: '# ILLUSTRATIVE - synthetic data\nContent-free evidence only. Voice dispatch is not browser audible-playback measurement. n < 20 is not SLO certification. Cost is observed Responses token cost, not full-stack billing; an immature or empty denominator is not zero cost.'
          }
        }
      ], queryItems)
    })
  }
}
output workbookId string = workbook.id
