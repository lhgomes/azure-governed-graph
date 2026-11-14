# GovernedGraph framework concepts

GovernedGraph separates **what may be said** (deterministic and approved) from **how it is explained**
(generative, learned within bounds). This page describes the model; the boundaries it enforces are listed in the
[README](../README.md#the-ten-boundaries), the Azure realisation in [architecture](architecture.md).

## The idea in one paragraph

A member asks a question during a journey. Official values come only from a deterministic system of record.
Knowledge comes only from approved, versioned content. A generative agent drafts an explanation
around a **placeholder** (`{{value:<id>}}`) and never the value itself. The host binds the official value, checks
numeric fidelity and advice-steering, records an explainability record and only then releases the answer.
Which *explanation strategy* (style, structure, teach-back) the agent uses is chosen from a small **approved
catalogue** by a **signed ranking snapshot** that Azure-hosted learning may reorder within hard limits and a
fairness audit may veto for everyone.

## Two planes

| Plane | Purpose | Learns? |
|---|---|---|
| **P1 runtime** | Serve a member journey: orchestrator state machine, counsellor agent, values facade, approved retrieval, strategy service, explainability store | No |
| **P2 isolated learning** | Re-rank approved strategies from immutable aggregate counts, audit cohorts for fairness, apply tiered approval, publish a signed snapshot | Yes, bounded |

There is no path from raw conversations or telemetry to prompts, templates, models or the catalogue (B4). P2 reads aggregates only.

## Roles

- **Orchestrator**: owns journey state, authorization, escalation and audit-before-release. Human escalation is reachable from every state (B9).
- **Counsellor** (generative): drafts a question-specific explanation using placeholders and approved passages. Cannot compute, round or select outside the served ranking.
- **Intent routing, understanding assessment, answer-quality judge** (generative, tool-free): closed semantic decisions and one bounded repair.
- **Strategy Service**: verifies and serves the signed Ranking Snapshot; applies suspension overlays at read time for all members (B3, B6).
- **Fairness & Explainability Agent** (protective only): audits segregated cohort aggregates and can suspend a strategy for every member (B6).
- **Assurance committee and tiers**: bounded ranking moves are autonomous (Tier 0/1); anything that widens what the system may say, use or decide is a human Tier 2 decision (B8).
- **Control arm and kill switch**: static expert ranking is retained and the graph must keep beating it, otherwise treatment reverts (B10).

## Protective versus widening actions

Protective actions (suspend, freeze, escalate) are always autonomous. Anything that widens the approved catalogue,
the content, the data used or the decisions made requires human approval and, for reinstatement, a governance board reference.

## What a domain supplies

See [adopting](adopting.md). In short: an approved strategy catalogue, templates with placeholders, approved
content, expert priors, fairness thresholds and cohort dimensions, a deterministic values source, and the counsellor prompt.
Anything beyond that (for example domain-specific agents or back-office integrations) is the domain's own addition and stays
outside the framework, as the pension sample's design-depth agents do.

## Components and where they run

The framework defines two business agents, not two deployed model definitions: the member-facing counsellor and the
Fairness & Explainability Agent. A domain adds its own agents on top; the pension sample adds three design-depth ones
(Transition Calculation, Event Processing, Adoption Guide), which are **not deployed administrator integrations**.

| Component | Implementation / hosting (Azure reference) |
|---|---|
| Member Communication / Counsellor | Foundry prompt agents; Agent Framework orchestration |
| Intent, understanding and answer review | Separate, tool-free Foundry semantic roles supporting the counsellor |
| Fairness & Explainability Agent | Protective Foundry agent plus segregated aggregate cohort audit |
| Journey, values facade and Strategy Service | Container Apps, APIM, private Blob state and Service Bus |
| Approved content and response evidence | Azure AI Search; append-only explainability records in immutable Blob storage |
| P2 bounded learning | Identity-isolated scheduled Container Apps Jobs |
| Administration console | Container App behind the gateway at `/admin/`; see [administration](administration.md) |

Azure Public is the only supported execution environment. Pure domain logic and
injected test doubles are separate from deployed adapters; they are not a local
application runtime. See [architecture](architecture.md) for the Azure design.

## Limits to state with any result

- Passing isolated regression gates is not evidence of full conversational quality, live integration or production acceptance.
- Conversational quality requires representative evaluation and human review.
- Voice text-channel checks do not certify microphone behaviour or end-to-end audio latency.
- Production cost and service-level targets require workload-specific validation.
- The synthetic handover demonstrates the integration contract, not a real callback; the contact-centre and member-token issuer are
  development stand-ins for real adapters.
- The console shows conversation text only for synthetic `gg-dev` members, from existing traces; raw conversations are never retained
  or fed back into behaviour (B4). Entra group authorization is required; production
  administrator access needs operator review.
- The bundled sample is a synthetic reference, **not a production service**.
