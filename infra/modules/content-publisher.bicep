param location string
param token string
param image string
param identityId string
param identityClientId string
param registryServer string
param env object

var envList = [for item in items(env): { name: item.key, value: string(item.value) }]
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = { name: 'cae-gg-${token}' }
resource publisher 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-gg-content-publisher'
  location: location
  tags: { data: 'synthetic', purpose: 'p3-approved-content-publisher' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1200
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [ { server: registryServer, identity: identityId } ]
    }
    template: {
      containers: [
        {
          name: 'publisher'
          image: image
          command: [ 'python', '-m', 'governedgraph.search.ingest' ]
          env: concat(envList, [
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}
output name string = publisher.name
