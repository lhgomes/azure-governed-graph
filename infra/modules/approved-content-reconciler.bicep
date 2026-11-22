// Separate P3 scheduled reconciler; existing publisher verifies immutable owner journal.
param location string
param token string
param image string
param identityId string
param identityClientId string
param registryServer string
param env object

var envList = [for item in items(env): { name: item.key, value: string(item.value) }]
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-gg-${token}'
}
resource reconciler 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-gg-approved-content-reconciler'
  location: location
  tags: { data: 'synthetic', purpose: 'p3-owner-approved-content-reconciliation' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1200
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '0 4 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [ { server: registryServer, identity: identityId } ]
    }
    template: {
      containers: [
        {
          name: 'approved-content-reconciler'
          image: image
          command: [ 'python', '-m', 'governedgraph.search.reindex_on_approval' ]
          args: [ '--from-approvals' ]
          env: concat(envList, [
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}
output name string = reconciler.name
