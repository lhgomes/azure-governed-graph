// Scheduled gap review: reads declined interactions and writes inert improvement proposals for owner review.
// Its identity can read response records and journey state and write the proposal container only; it cannot sign,
// publish content, change the catalogue or start jobs.
param location string
param token string
param image string
param identityId string
param identityClientId string
param env object
@description('Daily at 03:30 UTC; the review is idempotent, so a repeat run adds no duplicate proposals.')
param cronExpression string = '30 3 * * *'

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-gg-${token}'
}
resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: 'crgg${take(token, 16)}'
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-gg-gap-review-${token}'
}
resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identity.id, 'AcrPull')
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
var envList = [for item in items(env): { name: item.key, value: string(item.value) }]
resource review 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-gg-gap-review'
  location: location
  tags: { data: 'synthetic', purpose: 'gap-review-batch', targetPlatform: 'ContainerApps' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      scheduleTriggerConfig: { cronExpression: cronExpression, parallelism: 1, replicaCompletionCount: 1 }
      registries: [ { server: registry.properties.loginServer, identity: identityId } ]
    }
    template: {
      containers: [
        {
          name: 'gap-review'
          image: image
          command: [ 'python', '-m', 'governedgraph.learning.gap_review_job' ]
          env: concat(envList, [
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
            { name: 'GOVERNEDGRAPH_MODE', value: 'azure' }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}
output name string = review.name
