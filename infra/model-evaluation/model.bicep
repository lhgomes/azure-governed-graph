param accountName string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource modern 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: 'gpt-modern'
  sku: { name: 'DataZoneStandard', capacity: 50 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-5.4-mini', version: '2026-03-17' }
    raiPolicyName: 'gg-blocking-guardrails'
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

output deploymentName string = modern.name
