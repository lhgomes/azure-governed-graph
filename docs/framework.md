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

## Two runtime and learning planes, plus a governed improvement workflow

| Plane | Purpose | Learns? |
|---|---|---|
| **P1 runtime** | Serve a member journey: orchestrator state machine, counsellor agent, values facade, approved retrieval, strategy service, explainability store | No |
| **P2 isolated learning** | Re-rank approved strategies from immutable aggregate counts, audit cohorts for fairness, apply tiered approval, publish a signed snapshot | Yes, bounded |

Raw conversations and telemetry cannot directly modify approved prompts, templates, models or the catalogue, or retrain a model (B4). P2 reads aggregates only. A **separate daily gap-review job** may analyze eligible declined explanations (including short-lived redacted member wording sent to a tool-free Foundry agent) to prepare **inert owner-reviewed proposals**; it does not feed bounded ranking or publish changes itself. The owner can approve a passage or an explanation style using an existing approved pattern, subject to the governed release paths.
This does not prohibit operational conversation memory: authenticated customers
can continue across contacts using their own screened, accepted Foundry dialogue.
Only the most recent 20 completed turns are retained as conversation context;
historical figures never replace fresh authoritative reads. Administrator test
journeys are isolated from customer history. See
[customer continuity](architecture-conversation-continuity.md).

## Roles

- **Orchestrator**: owns journey state, authorization, escalation and audit-before-release. Human escalation is reachable from every state (B9).
- **Counsellor** (generative): drafts a question-specific explanation using placeholders and approved passages. Cannot compute, round or select outside the served ranking.
- **Intent routing, understanding assessment, answer-quality review** (Microsoft Decision 1.1 via SystemOne): closed semantic decisions; the host controls the single bounded repair.
- **Strategy Service**: verifies and serves the signed Ranking Snapshot; applies suspension overlays at read time for all members (B3, B6).
- **Fairness audit** (protective only): deterministic analysis of segregated cohort aggregates can suspend a strategy for every member (B6). The separately authorized Fairness & Explainability prompt agent is retained for explicit operator proof; scheduled fairness does not depend on it.
- **Assurance committee and tiers**: bounded ranking moves are autonomous (Tier 0/1); anything that widens what the system may say, use or decide is a human Tier 2 decision (B8).
- **Control arm and kill switch**: static expert ranking is retained and the graph must keep beating it, otherwise treatment reverts (B10).
- **Gap-review job and agent**: a separate scheduled Container Apps Job groups eligible declined explanation interactions; the tool-free Foundry `gg-gap-review` agent proposes content drafts or approved-pattern style changes. The administration console holds owner decisions and the two different controlled publication paths (B3, B4, B8).

## Protective versus widening actions

Protective actions (suspend, freeze, escalate) are always autonomous. Anything that widens the approved catalogue,
the content, the data used or the decisions made requires human approval and, for reinstatement, a governance board reference.

## What a domain supplies

See [adopting](adopting.md). In short: an approved strategy catalogue, templates with placeholders, approved
content, expert priors, fairness thresholds and cohort dimensions, a deterministic values source, and the counsellor prompt.
Anything beyond that (for example domain-specific agents or back-office integrations) is the domain's own addition and stays
outside the framework, as the pension sample's design-depth agents do.

## Components and where they run

The reference uses named Counsellor prompt agents (primary and compact), a separately authorized Fairness & Explainability prompt agent for explicit operator proof, and a tool-free gap-review drafting agent when the administration feature is enabled. Intent, understanding and answer-quality checks use the **Decision 1.1 SystemOne API directly**; they are not separate Foundry prompt agents. A domain may add design-depth agents; the pension sample's Transition Calculation, Event Processing and Adoption Guide are contracts/stubs, **not deployed administrator integrations**.

| Component | Implementation / hosting (Azure reference) |
|---|---|
| Member Communication / Counsellor | Foundry prompt agents; Agent Framework orchestration |
| Intent, understanding and answer review | Microsoft Decision 1.1 direct SystemOne API; host-owned routing and quality gates |
| Fairness & Explainability Agent | Deterministic segregated aggregate cohort audit; protective Foundry agent for explicit operator proof |
| Journey, values facade and Strategy Service | Container Apps, APIM, private Blob state and Service Bus |
| Approved content and response evidence | Azure AI Search; append-only explainability records in immutable Blob storage |
| P2 bounded learning | Identity-isolated scheduled Container Apps Jobs; aggregate counts only |
| Gap review and proposal drafting | Separate scheduled Container Apps Job plus tool-free Foundry drafting agent; short-lived eligible question records |
| Owner-approved changes | Administration console journal; signed catalogue publication or content publisher |
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
- The console shows conversation text only for synthetic `gg-dev` members, from existing traces.
  Separate customer-scoped Foundry context supports operational continuity, not
  model training or changes to approved behavior (B4). Entra group authorization is required; production
  administrator access needs operator review.
- The gap-review implementation and its publication workflow require live Azure end-to-end validation before operational effectiveness claims. Publishing an improvement is not itself proof of uplift.
- The bundled sample is a synthetic reference, **not a production service**.
