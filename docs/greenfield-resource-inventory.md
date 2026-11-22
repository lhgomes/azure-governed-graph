# Greenfield resource and dependency inventory

The standard pension WTP reference composition deploys the following services. Optional demo/operator workloads remain opt-in at the infrastructure layer (the current sample delivery explicitly enables its synthetic issuer, contact centre and admin console).

| Resource | Consumer/purpose |
| --- | --- |
| Foundry account and project | Model and agent execution |
| gpt-counsellor | Primary approved writer |
| gpt-compact | Compact writer, fairness operator and optional persona |
| embedding | Approved-content search vectorization |
| configured Microsoft Decision 1.1 deployment | Direct intent, understanding and answer-review decisions |
| Content Safety | Explicit Prompt Shields before inference |
| AI Search | Approved-content grounding and indexing |
| APIM | Governed member/strategy/admin APIs and pinned Responses |
| Container Apps environment | Orchestrator, strategy, member values, optional admin/contact-centre and voice; bootstrap, scheduled P2 and optional daily gap-review jobs |
| Application ACR | Runtime, bootstrap, probe, P2 and fixture images |
| Main storage | Explainability records, learning aggregates, optional immutable administration journal and optional improvement proposals |
| Private content storage | Approved content, ranking state, journey state/inbox, learning audits, cohort zone, P2 artifacts/evidence and optional handovers |
| Service Bus namespace | journey-events and escalations only |
| Key Vault | Snapshot signing and verification, optional dev issuer key |
| Virtual network, private endpoints, DNS, NSG | Runtime and gateway connectivity, scoped storage/key access |
| Log Analytics, Application Insights, alerts/workbook | Shared observability |

## Hosted agents

The standard agent provisioning set is gg-counsellor, gg-counsellor-voice-compact and gg-fairness-explainability. The fairness agent is used by the explicit protective operator proof; native scheduled fairness is deterministic and remains independent. gg-persona-demo is optional. gg-counsellor-modern and its model are confined to explicit model evaluation. Intent, understanding and answer review use Microsoft Decision 1.1 directly via Entra ID rather than hosted agents.

## Workload permissions

Retain distinct runtime, member-values, strategy, P2 pipeline, fairness and content-publisher identities; with the console, also the gap-review identity (reads response records and journey state, writes `improvement-proposals` only). Each receives only relevant role assignments; the pipeline and fairness identities pull from the main ACR and access p2-artifacts. The pipeline reads learning-aggregates and learning-audits, writes evidence and signs only with the approved key. Fairness alone reads its restricted cohort zone and writes its audit; never grant it signing authority. Native job execution metadata remains readable at the job scope for pipeline monitoring and approved recovery.

## Fresh Azure validation

Confirm the resource inventory contains no MachineLearningServices workspace/compute or ML-specific dependency resources, only the two consumed queues, three mandatory hosted agents and four standard model deployments. Verify isolated job execution, same-cycle fairness evidence, bounded ranking, signing, veto, publication, recovery/freeze, content ingestion, inference, and operational monitoring. No deployment or measured cost savings are certified by this inventory.
