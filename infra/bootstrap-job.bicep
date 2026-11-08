// Bounded resource-specific bootstrap, not another foundation/workload provision.
param location string
param jobName string
param environmentId string
param image string
param identityId string
param identityClientId string
param identityPrincipalId string
param registryName string
param command array
param env object
var envList = [for item in items(env): { name: item.key, value: string(item.value) }]

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identityPrincipalId, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: { data: 'synthetic', purpose: 'bounded-bootstrap' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1200
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [ { server: registry.properties.loginServer, identity: identityId } ]
    }
    template: {
      containers: [ {
        name: 'bootstrap'
        image: image
        command: command
        env: concat(envList, [
          { name: 'AZURE_CLIENT_ID', value: identityClientId }
        ])
        resources: { cpu: json('0.5'), memory: '1Gi' }
      } ]
    }
  }
  dependsOn: [pull]
}
output jobName string = job.name
