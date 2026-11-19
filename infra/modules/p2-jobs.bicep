param location string
param token string
param image string
param pipelineIdentityId string
param pipelineClientId string
param fairnessIdentityId string
param fairnessClientId string
param env object
param pipelineEnv object = {}
param scheduleEnabled bool = false
param scheduleProof bool = false

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-gg-${token}'
}
var roles = [
  { name: 'pipeline', identityId: pipelineIdentityId, clientId: pipelineClientId }
  { name: 'fairness', identityId: fairnessIdentityId, clientId: fairnessClientId }
]
var commonEnv = [for setting in items(env): { name: setting.key, value: string(setting.value) }]
var pipelineSettings = [for setting in items(pipelineEnv): { name: setting.key, value: string(setting.value) }]
resource jobs 'Microsoft.App/jobs@2024-03-01' = [for role in roles: {
  name: 'job-gg-p2-${role.name}'
  location: location
  tags: { data: 'synthetic', purpose: 'scheduled-p2-${role.name}', targetPlatform: 'ContainerApps' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${role.identityId}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: union({
      replicaTimeout: 4200
      replicaRetryLimit: 0
      registries: [ { server: 'crgg${take(token, 16)}.azurecr.io', identity: role.identityId } ]
    }, scheduleEnabled ? {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: scheduleProof ? (role.name == 'fairness' ? '*/5 * * * *' : '2-59/5 * * * *') : (role.name == 'fairness' ? '0 */4 * * *' : '2 */4 * * *')
        parallelism: 1
        replicaCompletionCount: 1
      }
    } : {
      triggerType: 'Manual'
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
    })
    template: {
      containers: [
        {
          name: role.name
          image: image
          command: scheduleEnabled ? ['python', '-m', 'governedgraph.learning.scheduled_jobs'] : ['python', '--version']
          env: concat(
            commonEnv,
            role.name == 'pipeline' ? pipelineSettings : [],
            [
              { name: 'AZURE_CLIENT_ID', value: role.clientId }
              { name: 'GOVERNEDGRAPH_MODE', value: 'azure' }
              { name: 'GOVERNEDGRAPH_P2_ROLE', value: role.name }
              { name: 'GOVERNEDGRAPH_P2_STAGE', value: role.name == 'fairness' ? 'audit' : 'pipeline' }
              { name: 'GOVERNEDGRAPH_P2_CADENCE_SECONDS', value: scheduleProof ? '300' : '14400' }
              { name: 'GOVERNEDGRAPH_P2_IMAGE', value: image }
            ]
          )
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}]
