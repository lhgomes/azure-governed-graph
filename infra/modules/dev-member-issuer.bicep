@description('Synthetic development only. Never deploy this issuer in production.')
param location string
param token string
param image string
param issuerIdentityId string
param issuerClientId string
param memberTokenIssuer string

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: 'kv-gg-${take(token, 13)}' }
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = { name: 'cae-gg-${token}' }
resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-gg-dev-member-issuer'
  location: location
  tags: { data: 'synthetic', purpose: 'dev-only-member-issuer' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${issuerIdentityId}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [ { server: 'crgg${take(token, 16)}.azurecr.io', identity: issuerIdentityId } ]
    }
    template: {
      containers: [
        {
          name: 'issuer'
          image: image
          command: [ 'python', '-m', 'governedgraph.dev.issue_member_token' ]
          args: [ '--public-jwk' ]
          env: [
            { name: 'GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER', value: 'true' }
            { name: 'AZURE_CLIENT_ID', value: issuerClientId }
            { name: 'DEV_ISSUER_CLIENT_ID', value: issuerClientId }
            { name: 'AZURE_KEY_VAULT_URL', value: vault.properties.vaultUri }
            { name: 'MEMBER_TOKEN_ISSUER', value: memberTokenIssuer }
            { name: 'APIM_GATEWAY_URL', value: 'https://apim-gg-${token}.azure-api.net' }
          ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}
