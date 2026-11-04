param location string
param tags object
param token string
param logAnalyticsId string
param serviceBusId string
@minLength(3)
param onCallEmail string
@minValue(1)
param promptShieldsFailureThreshold int = 3

resource onCall 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-gg-oncall-${token}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'GGoncall'
    enabled: true
    emailReceivers: [
      { name: 'dev-oncall', emailAddress: onCallEmail, useCommonAlertSchema: true }
    ]
  }
}

resource deadLetter 'Microsoft.Insights/metricAlerts@2018-03-01' = [for queue in [ 'escalations', 'journey-events' ]: {
  name: 'alert-gg-${queue}-deadletters-${token}'
  location: 'global'
  tags: tags
  properties: {
    description: 'B9: any dead-lettered ${queue} message requires human intervention; do not purge or auto-complete.'
    severity: queue == 'escalations' ? 1 : 2
    enabled: true
    scopes: [ serviceBusId ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    actions: [ { actionGroupId: onCall.id } ]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'any-deadletter'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.ServiceBus/namespaces'
          metricName: 'DeadletteredMessages'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Maximum'
          dimensions: [ { name: 'EntityName', operator: 'Include', values: [ queue ] } ]
        }
      ]
    }
  }
}]

var operationalLogs = '''
union isfuzzy=true ContainerAppConsoleLogs_CL, ContainerAppConsoleLogs
| extend Raw=tostring(column_ifexists("Log_s", column_ifexists("Log", "")))
| extend signal=parse_json(Raw)
'''
var rules = [
  {
    name: 'escalation-age'
    severity: 1
    description: 'B9: oldest queued escalation is older than five minutes.'
    filter: '| where tostring(signal.event) == "escalation_queue_age" and todouble(signal.age_seconds) > 300'
    threshold: 1
  }
  {
    name: 'explainability-or-queue-failure'
    severity: 1
    description: 'Explainability write failed or a durable queue delivery/recovery requires retry.'
    filter: '| where tostring(signal.event) in ("explainability_record_write_failed", "explainability_queue_fallback", "journey_outbox_delivery_failed", "journey_outbox_recovery_failed", "journey_receiver_unavailable")'
    threshold: 1
  }
  {
    name: 'prompt-shields-fail-closed'
    severity: 2
    description: 'Prompt Shields unavailable; generation fails closed. Threshold applies in a five-minute window.'
    filter: '| where tostring(signal.event) == "prompt_shields_unavailable"'
    threshold: promptShieldsFailureThreshold
  }
  {
    name: 'strategy-protective-state'
    severity: 1
    description: 'Strategy Service frozen or serving static fallback outside the control arm.'
    filter: '| where tostring(signal.event) in ("strategy_frozen", "strategy_freeze") or (tostring(signal.event) == "strategy_static_fallback" and tostring(signal.arm) != "control")'
    threshold: 1
  }
]

resource signals 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [for rule in rules: {
  name: 'alert-gg-${rule.name}-${token}'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'GovernedGraph ${rule.name}'
    description: rule.description
    severity: rule.severity
    enabled: true
    scopes: [ logAnalyticsId ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    skipQueryValidation: true
    criteria: {
      allOf: [
        {
          query: '${operationalLogs}\n${rule.filter}'
          timeAggregation: 'Count'
          operator: 'GreaterThanOrEqual'
          threshold: rule.threshold
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: [ onCall.id ] }
  }
}]

output actionGroupId string = onCall.id
