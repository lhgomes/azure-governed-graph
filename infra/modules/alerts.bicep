param location string
param tags object
param token string
param logAnalyticsId string
param serviceBusId string
@minLength(3)
param onCallEmail string
@description('Enable scheduled gap-review freshness monitoring only with the admin review job.')
param monitorGapReview bool = false
@description('Monitor P3 owner-approved-content reconciliation and publication backlog only when administration is enabled.')
param monitorPublication bool = false
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
var rules = concat([
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
    name: 'gap-review-failed'
    severity: 2
    description: 'Nightly review failed or exceeded its safe scan budget; an operator must investigate.'
    filter: '| where tostring(signal.event) == "gap_review_failed"'
    threshold: 1
  }
  {
    name: 'strategy-protective-state'
    severity: 1
    description: 'Strategy Service frozen or serving static fallback outside the control arm.'
    filter: '| where tostring(signal.event) in ("strategy_frozen", "strategy_freeze") or (tostring(signal.event) == "strategy_static_fallback" and tostring(signal.arm) != "control")'
    threshold: 1
  }
], monitorPublication ? [
  {
    name: 'publication-reconciliation-failed'
    severity: 2
    description: 'The approved-content reconciliation failed; approval alone does not publish a passage.'
    filter: '| where tostring(signal.event) == "publication_reconciliation_failed"'
    threshold: 1
  }
  {
    name: 'publication-content-refused'
    severity: 2
    description: 'Owner-approved passage refused due to live content/version conflict; owner decision required.'
    filter: '| where tostring(signal.event) == "publication_content_refused"'
    threshold: 1
  }
  {
    name: 'publication-backlog-stale'
    severity: 2
    description: 'Owner-approved passages remain unpublished or unrefused for longer than 26 hours.'
    filter: '| where tostring(signal.event) == "publication_backlog_stale"'
    threshold: 1
  }
] : [])

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

// A missing daily batch emits no error, so a separate freshness query detects silent failure.
resource gapReviewFreshness 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = if (monitorGapReview) {
  name: 'alert-gg-gap-review-stale-${token}'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'GovernedGraph gap-review freshness'
    description: 'No successful gap-review batch logged in 36 hours; check job execution and ingestion.'
    severity: 2
    enabled: true
    scopes: [ logAnalyticsId ]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT48H'
    autoMitigate: true
    skipQueryValidation: true
    criteria: {
      allOf: [
        {
          query: '''
let latest = toscalar(
  union isfuzzy=true ContainerAppConsoleLogs_CL, ContainerAppConsoleLogs
  | extend Raw=tostring(column_ifexists("Log_s", column_ifexists("Log", "")))
  | where tostring(parse_json(Raw).event) == "gap_review_completed"
  | summarize max(TimeGenerated)
);
print TimeGenerated=now(), missed=iif(isnull(latest) or latest < ago(36h), 1, 0)
| where missed == 1
'''
          timeAggregation: 'Count'
          operator: 'GreaterThanOrEqual'
          threshold: 1
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: [ onCall.id ] }
  }
}

// The completion event comes only from the scheduled P3 reconciler, not from a
// manual operator invocation; a missed job cannot be masked by manual publishing.
resource publicationFreshness 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = if (monitorPublication) {
  name: 'alert-gg-publication-reconciliation-stale-${token}'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'GovernedGraph approved-content reconciliation freshness'
    description: 'No completed scheduled P3 content reconciliation for 36 hours.'
    severity: 2
    enabled: true
    scopes: [ logAnalyticsId ]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT48H'
    autoMitigate: true
    skipQueryValidation: true
    criteria: {
      allOf: [
        {
          query: '''
let latest = toscalar(
  union isfuzzy=true ContainerAppConsoleLogs_CL, ContainerAppConsoleLogs
  | extend Raw=tostring(column_ifexists("Log_s", column_ifexists("Log", "")))
  | where tostring(parse_json(Raw).event) == "publication_reconciliation_completed"
  | summarize max(TimeGenerated)
);
print TimeGenerated=now(), missed=iif(isnull(latest) or latest < ago(36h), 1, 0)
| where missed == 1
'''
          timeAggregation: 'Count'
          operator: 'GreaterThanOrEqual'
          threshold: 1
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: [ onCall.id ] }
  }
}

output actionGroupId string = onCall.id
