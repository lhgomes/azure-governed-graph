param location string
param tags object
param workspaceName string
param subnetId string
param pipelineIdentityId string
param fairnessIdentityId string

resource workspace 'Microsoft.MachineLearningServices/workspaces@2024-10-01-preview' existing = {
  name: workspaceName
}

var pools = [
  { name: 'gg-p2', identityId: pipelineIdentityId, maxNodes: 2 }
  { name: 'gg-fairness', identityId: fairnessIdentityId, maxNodes: 1 }
]
resource compute 'Microsoft.MachineLearningServices/workspaces/computes@2025-09-01' = [for pool in pools: {
  parent: workspace
  name: pool.name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${pool.identityId}': {} }
  }
  properties: {
    computeType: 'AmlCompute'
    disableLocalAuth: true
    description: 'ILLUSTRATIVE - synthetic data; isolated ${pool.name} managed-identity jobs'
    properties: {
      vmSize: 'Standard_D2s_v3'
      vmPriority: 'Dedicated'
      osType: 'Linux'
      enableNodePublicIp: false
      remoteLoginPortPublicAccess: 'Disabled'
      subnet: { id: subnetId }
      scaleSettings: {
        minNodeCount: 0
        maxNodeCount: pool.maxNodes
        nodeIdleTimeBeforeScaleDown: 'PT5M'
      }
    }
  }
}]
