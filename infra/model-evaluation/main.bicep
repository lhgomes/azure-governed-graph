targetScope = 'subscription'

@allowed(['gg-dev'])
param environmentName string
@allowed(['swedencentral'])
param location string

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: 'rg-${environmentName}'
}

var token = toLower(uniqueString(subscription().id, rg.id, environmentName))

module model 'model.bicep' = {
  name: 'gg-evaluation-model'
  scope: rg
  params: {
    accountName: 'ais-gg-${token}'
  }
}

output AZURE_AI_EVALUATION_DEPLOYMENT string = model.outputs.deploymentName
output AZURE_LOCATION string = location
