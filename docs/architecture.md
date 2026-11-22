# Architecture

GovernedGraph is an Azure Public reference architecture for governed member
guidance. Deterministic systems own authoritative facts; generative agents
explain them within approved content, strategy and safety boundaries.
The domain-neutral model is described in [framework concepts](framework.md).

**ILLUSTRATIVE - synthetic data.** The bundled pension-transition sample is a
worked domain, not a production pension service. This document describes
architecture and enforcement, not the live state or acceptance of a deployment.
Deployment-specific identifiers, approvals, measurements and operational
evidence do not belong in public documentation.

## Scope and separation

| Plane | Responsibility | Permitted inputs |
|---|---|---|
| P1: member-facing runtime | Authenticate, guide, retrieve approved facts, enforce output gates and record delivery | Authorized member requests, approved content, official values and verified strategy state |
| P2: isolated learning and assurance | Reorder approved strategies, audit fairness, govern and sign publication | Immutable outcome counts and segregated cohort audit evidence |
| Gap review: separately governed improvement | Analyze eligible declined explanation cases, generate inert proposals for an owner | Declined-response metadata and questions retained up to 30 days, approved content and style patterns |
| Content governance | Approve and version the knowledge and explanation patterns | Human-reviewed domain material |

All features and bounded learning are available from the first release. There
is no runtime phase or calendar-based rollout. Observation windows measure
outcomes; they do not gate availability.

The framework lives in [src/governedgraph](../src/governedgraph) and never imports
the [sample domain](../examples/pension_wtp). A domain supplies its approved pack
and deterministic values adapter through explicit deployment composition.
The sample's Transition Calculation, Event Processing and Adoption Guide agents
are [design-depth contracts](../examples/pension_wtp/docs/agents-design-depth.md),
not deployed administrator integrations.

Deeper design pages: [conversation continuity](architecture-conversation-continuity.md),
[catalogue versions and source upgrades](architecture-catalogue-operations.md) and
[gap review](architecture-gap-review.md).

## Components

| Component | Role and hosting |
|---|---|
| Chat and voice channels | Authenticated entry points through APIM; voice is an adapter to the same governed journey |
| Orchestrator | Container App owning the state machine, authorization, audit barrier and durable outbox |
| Counsellor | Microsoft Foundry prompt agent called by name, so its latest published version answers; writes explanations using official-value placeholders |
| Intent, understanding and answer review | Closed semantic decisions via the pinned Microsoft Decision 1.1 deployment and SystemOne API; not hosted prompt agents |
| Values facade | APIM-protected, read-only deterministic system-of-record API |
| Approved retrieval | Azure AI Search over human-approved, versioned passages |
| Strategy Service | Identity-separated service verifying signed catalogue rankings and applying universal vetoes |
| Explainability store | Append-only response metadata in private immutable Blob storage |
| Event transport | Service Bus with stable domain IDs and durable consumer deduplication |
| Learning and fairness hosts | Scheduled Container Apps Jobs with separate managed identities |
| Gap-review job and Foundry drafting agent | Independent nightly Container Apps Job and a tool-free `gg-gap-review` Foundry agent; writes proposals, not approvals |
| Content publisher | Separately authorized job verifies owner approval, guards against newer content versions, and publishes approved passages |
| Signing | Key Vault; only the isolated P2 signer can sign ranking snapshots |
| Administration console | Internal Container App behind `/admin/`; evidence, protective controls and human owner decisions |
| Observability | Content-free OpenTelemetry, Application Insights and Azure Monitor |

Microsoft Agent Framework coordinates explicit host-owned workflows. Agent
messages containing utterances are not persisted as framework checkpoints.
Native Azure services supply infrastructure; the journey logic, closed
contracts, strategy ranking, vetoes and release gates are framework code.

### Decision model

A single pinned Decision model performs the closed semantic checks: intent
routing, answer review and teach-back understanding. There is no second judge
or model fallback.

- **Closed outputs.** Each request asks one question and returns one label from
  a closed set. The domain pack supplies a description of every label
  (`decision_criteria` in the vocabulary), validated against the closed reason
  and concept sets. Unrelated questions are never combined in one request:
  intent reason and response language are separate parallel requests, as are the
  review verdict, the review language check and the concept checks.
- **Confidence gate.** A reason or verdict is accepted only above a
  greater-than-half confidence. Transport, response-contract and low-confidence
  failures are distinguished in content-free operational logs. Unavailable
  intent or review evidence fails closed to a human handover (B9).
- **Answer review.** The reviewer reads plain text in five labelled parts: the
  member's question, the last exchange (context only), the draft, the official
  amounts the draft refers to, and the approved sources it cites; it never sees
  the writer's task or the whole conversation. It returns one of four labels,
  described by the domain pack: `pass_draft`, `advice`, `unsupported` (a fact,
  promise, guarantee, amount comparison or claim about the member's own
  documents that no cited source or official amount states; a claim a source
  does state is supported) and `off_target`. Whether the draft is in the
  response language is a separate parallel request, so it never competes with
  those labels. Advice and unsupported claims, and a wrong language, block the
  draft; after the single bounded repair, a rejection for `off_target` or
  teaching style alone delivers the draft that already passed the safety checks
  and every deterministic gate, without a teach-back. An unclear review
  (confidence at or below the gate) is neither a pass nor a service failure:
  nothing is released and the host uses the approved wording that offers a
  person. A transport or contract failure still fails closed to a human
  handover (B9). Concept checks only decide which concepts are recorded as
  taught, so they are optional; they see the draft alone.
- **Understanding.** A restatement is `understood`, `needs_explanation` or
  `uncertain`. `uncertain` records neither a pass nor a failure (B5) and returns
  approved retry wording that asserts nothing about correctness, figures or
  options (B2, GNA). The judge assesses meaning and never verifies figures.
- **Retries.** HTTP 429 responses use one Retry-After retry within the shared
  per-turn wait budget. Other errors and rejected judgments are not retried.
- **Diagnostics.** Model request and response text is never part of ordinary
  operational telemetry. An opt-in, synthetic-data-only diagnostic mode is
  described in [observability](observability.md); it is never an input to
  prompts, ranking, templates or learning (B4).

The deterministic numeric-fidelity, steering and audit gates are unchanged by
any of these checks.

The foundation provisions the pinned Decision model in the workload's Foundry
account. The workload takes the deployment name from the foundation output;
the separate benchmark account is never a runtime dependency.

### Private application ingress

APIM reaches externally exposed Container Apps routes through the environment's
explicit `managedEnvironments` private endpoint in the existing private-endpoints
subnet. The environment remains internal with public network access disabled.
The existing environment-domain private DNS zone maps its apex and wildcard to
the private endpoint's allocated address, not the environment's `staticIp`.
Environment-only routes remain environment-only; no identity or permission is
shared or added by this network connection. An explicit Private Link endpoint is
used rather than the implicit internal ingress address, which is not reliably
reachable from the VNet; it adds recurring network charges and does not change
B1-B10, channel authorization, catalogue approval or learning.

## Runtime sequence (P1)

1. Start an authenticated journey and deliver the approved AI disclosure before
   accepting member input.
2. Validate current-request identity and step-up before any personal-value read.
3. Screen the utterance and retrieved documents with Content Safety Prompt Shields.
   Missing, malformed or unavailable screening denies generation.
4. Route through closed intent branches: explanation, clarification, understanding,
   requested human help, personal advice or required-service failure.
5. Retrieve approved passages and member-scoped official values through authorized
   read-only interfaces.
6. Verify the signed ranking against active catalogue versions; apply eligibility,
   suspensions and protective state. The control arm retains approved expert order.
7. Compose a question-specific draft with `{{value:<id>}}` references. Bind only
   authorized official display strings; reject literal invented figures and steering.
8. The Decision model's answer review checks support, relevance and language.
   At most one evidence-preserving repair is allowed across the deterministic output
   gate and answer review. A literal-digit contract failure without a usable
   approved fallback can use that repair before review; all deterministic gates
   and the answer review must then pass. An internal answer failure never asks
   the member to repeat an already classified question: the approved answer-failure
   acknowledgment offers human help in the same journey, or escalates if that offer
   cannot safely be delivered. A task fallback that asks for missing context is
   clarification, not an answered explanation: it is audited as an acknowledgment
   and leaves the journey explaining, without a resolution or teach-back event.
   The sample's vocabulary carries the contextual failure wording; it adds no
   substantive transition or opt-out claims.
9. Durably commit response metadata to the explainability store or its independent
   retry outbox, then commit the response-linked journey event.
10. Release the checked response only after the audit write-ahead barrier.
    Project confirmed outcomes into immutable counts for P2. An optional outcome
    prompt is not appended when the released response already contains a question;
    feedback must not compete with a request for context or a meaning check.

Option-level or personal-advice requests always take the human-handover route.
The model never authorizes a value read, selects a member identity, writes audit
records or bypasses the host's gates.

### Agent and output contracts

Foundry agent versions are immutable, but runtime calls each agent by name and
the agent's latest version answers; there is no version pin and no runtime
comparison of the definition. Delivery publishes a new version only when a
definition changed (`scripts/create_agents.py`), so a prompt change needs no
redeployment of the workloads. The model deployment is fixed in the agent
definition, the gateway admits only the approved agent names and rejects any
model, instruction, tool or text override, and runtime never creates agents.
Prompt changes are controlled through the reviewed pull request and the
authorized delivery pipeline. Dependency versions are pinned in the
[Azure requirements](../requirements-azure.txt).

Function requests execute only in journey-bound host tools. Model-supplied
identity and member selectors are ignored; other arguments use closed schemas.
Unknown tools and model-originated audit writes are rejected. Tool rounds,
calls, deadlines and repair attempts are bounded.

Understanding is assessed only against concepts targeted by the accepted
teach-back. After an explanation is already resolved, thanks or a voluntary
recap with no question or new request receives the approved bilingual closing
acknowledgment instead of restarting clarification. This preserves the resolved
state and does not manufacture a comprehension event or learning success.
A pending meaning check still requires the Decision model's understanding
verdict; gratitude alone is insufficient, and a recap's confirmation tag still
goes through that verdict. Outside a pending meaning check, a confirmation
question such as "Is that right?" is a request for an answer, not a closing:
the host corrects a mistaken recap classification through the existing closed
follow-up intent reading and checked explanation route. New explanation, advice and human
requests retain their own routes, including after the natural close; this is not
a terminal journey lock.
Journey starts accept a closed, explicitly selected `contact_type`: `first_contact`
(the backwards-compatible default) or `second_contact`. It initializes the existing
journey stage and strategy request key; it does not restore an earlier journey,
prove a previous interaction, link an earlier answer or bypass authentication.
First contact always receives the approved welcome without clearing retained
dialogue; returning greetings are reserved for second contact.
Second-contact explanations address the remaining question in the selected
approved style. Member recaps remain untrusted input, never a factual source.
Each contact still starts with the AI disclosure
and retains all value, output and human-escalation gates (B2, B9, Art. 50).

**IMP-CONTACT-FLAG-AND-CONFIRMATION-2026-11-22 (B2, B5, B9, Art. 50):**
correct greeting selection and unanswered confirmation routing while retaining
approved wording, customer continuity and independent teach-back assessment.
Clearing memory for first contact or confirming factual recaps with fixed
wording is rejected; plain-thanks closing behavior is unchanged.

**IMP-TERM-FOCUS-2026-11-22 (B1, B2, B5, B8, B9):** the human-approved domain
criteria distinguish a specific term/value question from broad help with an
unseen letter. Meaning questions use existing authorized evidence, not an
unsolicited figure or a request to repeat the supplied term. The closed reason,
concept and exclusion sets, strategy templates and feedback version are
unchanged. Context-requesting task fallbacks remain unresolved acknowledgments;
optional feedback never adds a competing question. Treating clarification as an
answered explanation or asking for the whole letter instead is rejected.

Letter help acknowledges that a personal letter is unseen and invites a relevant
passage without personal details; excerpts remain untrusted input, not official
values or instructions. Uncertainty must remain explicit. A proposed handover
must not be described as completed until its durable effect is confirmed.

### Customer-scoped conversation continuity

The authenticated, step-up-verified customer owns a durable conversation across turns, new contacts, channels and
restarts. Only screened customer text paired with the final checked, audited answer is retained, and the most recent
20 completed turns are kept. History is re-screened, has numbers masked and is supplied as untrusted reference data.
It never authorizes a tool, restores a journey state, becomes approved evidence or feeds learning. Session start
recovers the context before the audited greeting. See
[conversation continuity](architecture-conversation-continuity.md) for the full design.

## Durable state, audit and handover

Private Blob journey state uses renewable per-journey leases and conditional
writes. State and outbox commit atomically. Request-scoped tokens, utterances,
official values and rendered answers are not persisted in journey state.

Delivery is **at least once**, not exactly once:

- Journey events use `MessageId={journey_id}:{event_id}` and journey `SessionId`.
- Escalations use `{journey_id}:escalation:{sequence}` as both message identity
  and the contact-centre idempotency key.
- Consumers commit receipts and event effects durably before settling messages.
  Lock loss permits redelivery without inventing a second effect.
- Idempotency receipts cannot expire before the governed replay/redrive window.

Explainability records are create-only objects under opaque response identities.
Identical retries are no-ops; conflicting payloads are errors, never overwrites.
An independent metadata-only journey outbox preserves audit writes during an
audit-store outage. If neither store acknowledges durability, the answer is
withheld. Failed handover persistence is an explicit error; channels must retain
a direct human-contact option.

[Reconciliation](../src/governedgraph/explainability/reconcile.py) verifies
one-to-one linkage between delivered response events and immutable records.
Empty scopes, pending writes, missing links and conflicts cannot certify success.
A scoped result is not an all-history certification.

Member explainability reports require current step-up and ownership, include
durable pending references and prohibit caching. They contain audit metadata,
not transcripts, tokens, member figures or demographics.

The sample's [contact-centre adapter](../examples/pension_wtp/docs/api/contact-centre.md)
records synthetic handovers, not real callbacks. Production must provide a
durable idempotent adapter behind the same contract.

## Strategy catalogue and governed publication

The sample starts with six approved strategies:

| ID | Explanation style |
|---|---|
| S1 | Plain-language summary |
| S2 | Worked example using the member's official figures |
| S3 | Visual before/after timeline |
| S4 | What changes / what stays the same |
| S5 | Step-by-step explanation with teach-back |
| S6 | Offer a human advisor; always available and never ranked |

Catalogue versions, complete expert priors for every eligible context and reviewed source-pack upgrades are
described in [catalogue versions and source upgrades](architecture-catalogue-operations.md). Updating the source
catalogue alone never activates it: activation is a reviewed Tier 2 delivery with newly verified signed snapshots
(B3, B5, B8, B10).

The Strategy Service verifies signatures and active catalogue versions on read,
startup and rollback. Immutable current, last-good and static snapshots are
referenced by conditionally updated state. Universal suspension overlays apply
to treatment and control on subsequent reads. Unavailable or invalid state must
not authorize an unsigned treatment ranking.

Protective static rollback serves treatment only from a verified signed expert
baseline. A deployment without that baseline requires governed initialization
before it can use signed rollback; otherwise it fails closed.

Human owners may propose catalogue changes using existing approved explanation
patterns and closed eligibility vocabularies, not arbitrary prompt text.
P2 signs only the exact recorded proposal after its ordinary audit and isolation
checks. Activation atomically installs verified catalogue definitions, expert
baseline and ranking. A freeze blocks activation; protective findings and owner
pauses survive. Historical evidence retains its original versions, and old-version
outcomes cannot train a replacement strategy version.

Owner orders require a recorded human decision and an exact matching P2 signature.
They affect treatment only. Unpause decisions cannot suppress newer fairness
findings. Freeze and static reversion revoke widening overrides while retaining
protective owner pauses. The [console guide](administration.md) describes these
controls and their independent authorization.

## Isolated learning and fairness (P2)

The learning sequence is:

```text
immutable outcomes -> bounded reordering -> isolated cohort audit
-> universal veto -> signing -> tiered governance -> conditional publication
-> treatment/control uplift monitoring
```

Minimum samples and maximum rank shifts constrain reordering. Learning cannot
add strategies, alter content, change prompts or train models. Confirmed
understanding, pending outcomes and eligible observation-window evidence remain
distinct. Projection is idempotent; missing or unconfirmed outcomes are not
invented as successes or failures. Selected-style test journeys are excluded.
See [feedback and rollback](governed-feedback-and-rollback.md) for attribution
limits and optional outcome prompts.

Cohort dimensions exist only in the segregated audit zone. The fairness identity
can read that zone; P2 and runtime cannot. Minimum-cell suppression protects small
cohorts. Only a closed demographic-free veto projection leaves the zone, bound
to the exact outcome digest and approved thresholds. The host applies protective
actions independently of model behavior. Full member reports stay in the host;
only bounded receipts reach the fairness prompt agent.

Scheduled fairness and ranking jobs have separate identities and a shared lease.
Publication requires a fresh, matching successful audit and unchanged baseline;
missing audit evidence, failed stages or lost leases freeze publication.
Watchdogs cover failures before component execution. Operator recovery is
human-only, separately authorized and journalled, and does not change rankings
or suspensions. Reinstatement requires a configured governance group and a
`GB-` decision; absent configuration keeps it disabled.

Tier 0 permits bounded automatic adjustments. Tier 1 uses agentic assurance.
Material changes require human Tier 2 approval. The expert control arm remains
static, and aggregate uplift monitoring can freeze treatment and revert to its
signed expert baseline. Publication alone is not proof that the graph beats
the control.

## Identity and network isolation

Azure integrations use managed identity / `DefaultAzureCredential`, not keys
or connection strings. Each role has its own identity. Shared-store permissions
are container-scoped; signing and verification permissions are key-scoped.
The scheduled fairness and ranking jobs use the application registry and private artifact container; fairness cannot sign ranking snapshots.

APIM and backends independently validate signature, tenant, audience, expiry
and required role or delegated scope. Publication belongs to P2, suspension to
authorized protective roles, and widening owner actions require both a delegated
human token and the pinned journal-writing admin proxy. The Strategy Service does
not sign. Member tokens are request-local and forwarded unchanged to the values
facade; neither APIM nor the orchestrator receives member-signing permission.

Private ingress, storage endpoints and DNS protect backend services. A synthetic
development issuer is isolated from snapshot signing, disabled outside the sample
environment, and never exposes member tokens to a browser, file or log.
Live probes run in-network under its existing identity without extra grants.
See [member authentication](../examples/pension_wtp/docs/api/member-authentication.md).

## Approved content governance

Production content requires human approval and immutable version metadata.
A controlled publishing integration must authenticate and verify approval against
its source governance record; a synthetic approval reference is not production proof.

The publisher holds a single-writer lease, records approval and predecessor
retirement durably, verifies retirement, then indexes the replacement.
Publication failures can cause an availability gap, never permission to restore
retired content. Existing versions cannot be edited or reinstated under an alias.
Search reads only its dedicated approved-content container.

Integrated embedding and query vectorization use Search's managed identity.
Retrieval combines keyword, vector and semantic ranking, filtered by approval,
language and journey state. Results without approval evidence fail closed.
Each retrieval replaces prior passage context so later turns do not reuse retired
cached material. Accessibility selects approved retrieval language and presentation,
not a demographic inference or automatic translation permission.

## Gap review

A daily job (03:30 UTC by default, when administration is enabled) analyzes **eligible explanation-routed declines and final-review-flagged responses**, not every failed conversation. A situation needs at least five eligible declines across three journeys in the default 30-day lookback. A separate tool-free Foundry agent may analyze redacted kept questions and draft a passage or recommend a style on an existing approved pattern. Its output is inert and untrusted; the owner verifies material claims and records the decision.

Approved styles enter the **signed catalogue release**, while owner-approved passages enter the **separate content publisher** with journal and version checks. Neither the job nor the agent can change runtime behavior directly. This human-governed path is the limited B4 exception for short-lived member wording; aggregate-only P2 ranking still cannot read raw conversations. Later effectiveness must be measured against the static control and safety/fairness gates; publication alone does not demonstrate uplift. See [gap review](architecture-gap-review.md).

## Governed speech channel

Voice is a transport adapter, not a second counsellor. It uses the same
authorization, closed journey requests, idempotency and audit barrier as chat.
The approved disclosure completes before input capture. Automatic model responses
and tools are disabled; synthesis receives only final checked text.
Returned text, response identity, audio bounds and ordering are validated before
playback. No unchecked model delta is streamed.

Connection reuse and warm-up do not cache member values or bypass per-turn
screening. Figure-free acknowledgements are distinguished from substantive answers
and cannot claim that checks or audit have completed.

Warm-up supplies an explicitly empty conversation context to the same
Decision model request path used by live turns; it never loads a customer's
history or creates a journey. A presenter connection that closes before
confirmed completion becomes a visible error, including during startup; an
explicit service error is preserved. B1-B10 and disclosure playback are
unchanged.

### Voice SLO table

These are **illustrative targets**, not measured framework guarantees. Both
measurements begin at end of member input and have inclusive limits.

| Measure | Illustrative p95 target |
|---|---:|
| First approved figure-free acknowledgement audio dispatch | <= 1 s |
| First fully checked substantive audio dispatch | <= 6 s |

Record actual sample counts, failures, operating mode and warm/cold controls.
Complete-audio readiness is diagnostic. Server dispatch is not acoustic or
browser-playback certification. See [evaluation](evaluation.md) and the
[sample runbook](../examples/pension_wtp/docs/demo-runbook.md) for measurement procedures.

## Observability and production limits

Operational traces carry allowlisted IDs, durations, usage and guardrail results;
APIM records zero body bytes. SDK prompts, drafts, tools, credentials and exception
payloads are excluded. Optional captured synthetic conversations require explicit
development-only configuration and never enter learning.

Screening requires a dedicated keyless service in a supported region. Operators
must assess regional processing, latency, availability, service concentration
and human-advisor continuity in their privacy and third-party dependency reviews.
The reference architecture does not establish regulatory compliance.

Model-token list cost is not full-stack cost. Mature outcome denominators,
verified usage, allocation and ingestion completeness are required for financial
reporting. Native Responses gateway limitations do not authorize silent agent
or model failover. See [observability](observability.md).

Azure readiness, hard-gate regression, semantic quality, speech performance
and production acceptance are separate claims. No checked-in document certifies
a deployment. Execution and evidence requirements are in
[Azure-only execution](azure-only-execution.md).

## B1-B10 enforcement and regression traceability

The entries below identify enforcement and regression source, not executed results.
Guidance-not-advice and opening AI disclosure apply across the runtime.

| Boundary | Enforcement | Regression source |
|---|---|---|
| B1: deterministic authority | [Values tools](../src/governedgraph/agents/tools.py), [history number masking](../src/governedgraph/agents/conversation_context.py), [sample facade](../examples/pension_wtp/member_values_api) | [Values facade](../tests/test_member_values_facade.py), [customer continuity](../tests/test_customer_continuity.py) |
| B2: no AI calculations | [Placeholder binding](../src/governedgraph/guardrails/binding.py), [numeric fidelity](../src/governedgraph/guardrails/numeric_fidelity.py) | [Numeric fidelity](../tests/test_numeric_fidelity.py), [Foundry output](../tests/test_foundry_counsellor.py) |
| B3: approved strategies only | [Strategy Service](../src/governedgraph/strategy_service/service.py), [source upgrades](../src/governedgraph/strategy_service/source_upgrade.py), [host tools](../src/governedgraph/agents/tools.py) | [Signed Azure state](../tests/test_strategy_azure.py), [owner catalogue/content publication](../tests/test_admin_proposals.py), [upgrade gates](../tests/test_bootstrap.py) |
| B4: no conversation-to-behavior path | [Closed aggregates](../src/governedgraph/common/contracts.py), [feedback projection](../src/governedgraph/learning/feedback.py), [gap review](../src/governedgraph/learning/gap_review.py) | [Ranking inputs](../tests/test_ranking_bounds.py), [learning feedback](../tests/test_learning_feedback.py), [gap review](../tests/test_gap_review.py) |
| B5: bounded reorder-only learning | [Ranking pipeline](../src/governedgraph/learning/ranking_pipeline.py) | [Ranking bounds](../tests/test_ranking_bounds.py) |
| B6: fairness veto for everyone | [Fairness agent](../src/governedgraph/agents/fairness_explainability.py), [read-time overlays](../src/governedgraph/strategy_service/service.py) | [Fairness veto](../tests/test_fairness_veto.py), [Azure overlays](../tests/test_strategy_azure.py) |
| B7: no runtime demographics | [Closed contracts](../src/governedgraph/common/contracts.py), [customer context boundary](../src/governedgraph/orchestrator/continuity.py), [APIM policy](../infra/policies/strategy-request.xml) | [Demographic rejection](../tests/test_contracts_no_demographics.py), [customer isolation](../tests/test_customer_continuity.py) |
| B8: human material-change approval | [Tier routing](../src/governedgraph/learning/run_cycle.py), [assurance committee](../src/governedgraph/learning/assurance_committee.py), [Strategy Service](../src/governedgraph/strategy_service/service.py), [signed source upgrade](../src/governedgraph/strategy_service/bootstrap.py), [proposal decisions](../src/governedgraph/admin/proposals.py) | [Proposal decisions](../tests/test_admin_proposals.py), [Reinstatement](../tests/test_strategy_service.py), [owner decisions](../tests/test_admin_owners.py), [upgrade gates](../tests/test_bootstrap.py) |
| B9: human help always available | [State machine](../src/governedgraph/orchestrator/state_machine.py), [escalation tools](../src/governedgraph/agents/tools.py), [closed intent routing](../src/governedgraph/agents/decision_agents.py) | [Every-state escalation](../tests/test_state_machine.py), [durable handover](../tests/test_durable_orchestrator.py), [Decision model checks](../tests/test_decision_agents.py) |
| B10: continuous governed control | [Arm assignment](../src/governedgraph/strategy_service/service.py), [uplift kill switch](../src/governedgraph/learning/uplift.py) | [Uplift](../tests/test_uplift_and_design_agents.py), [signed static rollback](../tests/test_governed_static_contracts.py) |
