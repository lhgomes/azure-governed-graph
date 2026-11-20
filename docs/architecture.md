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

## Components

| Component | Role and hosting |
|---|---|
| Chat and voice channels | Authenticated entry points through APIM; voice is an adapter to the same governed journey |
| Orchestrator | Container App owning the state machine, authorization, audit barrier and durable outbox |
| Counsellor | Version-pinned Microsoft Foundry prompt agent; writes explanations using official-value placeholders |
| Intent, understanding and answer review | Closed semantic decisions via the pinned Microsoft Decision 1.1 deployment and SystemOne API; not hosted prompt agents |
| Values facade | APIM-protected, read-only deterministic system-of-record API |
| Approved retrieval | Azure AI Search over human-approved, versioned passages |
| Strategy Service | Identity-separated service verifying signed catalogue rankings and applying universal vetoes |
| Explainability store | Append-only response metadata in private immutable Blob storage |
| Event transport | Service Bus with stable domain IDs and durable consumer deduplication |
| Learning and fairness hosts | Scheduled Container Apps Jobs with separate managed identities |
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
  review verdict and the concept checks.
- **Confidence gate.** A reason or verdict is accepted only above a
  greater-than-half confidence. Transport, response-contract and low-confidence
  failures are distinguished in content-free operational logs. Unavailable
  intent or review evidence fails closed to a human handover (B9).
- **Answer review.** One holistic verdict is returned for the approved rubric
  (most serious issue first; advice always blocks). Every safety defect (advice,
  grounding, document access, uncertainty, language, unassessable evidence)
  blocks the draft and falls back to approved wording. After the single bounded
  repair, a rejection for relevance or teaching style alone delivers the draft
  that already passed the safety checks and every deterministic gate, without a
  teach-back. Concept checks only decide which concepts are recorded as taught,
  so they are optional.
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
   cannot safely be delivered. Genuine clarification remains an intent-routing decision.
   The sample's vocabulary carries the contextual failure wording; it adds no
   substantive transition or opt-out claims.
9. Durably commit response metadata to the explainability store or its independent
   retry outbox, then commit the response-linked journey event.
10. Release the checked response only after the audit write-ahead barrier.
    Project confirmed outcomes into immutable counts for P2.

Option-level or personal-advice requests always take the human-handover route.
The model never authorizes a value read, selects a member identity, writes audit
records or bypasses the host's gates.

### Agent and output contracts

Foundry definitions are immutable and pinned by name and version. Runtime
verifies the approved model, instructions and tools; it never creates agents,
chooses `latest` or silently substitutes a model. Dependency versions are pinned
in the [Azure requirements](../requirements-azure.txt).

Function requests execute only in journey-bound host tools. Model-supplied
identity and member selectors are ignored; other arguments use closed schemas.
Unknown tools and model-originated audit writes are rejected. Tool rounds,
calls, deadlines and repair attempts are bounded.

Understanding is assessed only against concepts targeted by the accepted
teach-back. After an explanation is already resolved, confirmation, thanks or a
voluntary recap with no new request receives the approved bilingual closing
acknowledgment instead of restarting clarification. This preserves the resolved
state and does not manufacture a comprehension event or learning success.
A pending meaning check still requires the Decision model's understanding
verdict; gratitude alone is insufficient. New explanation, advice and human
requests retain their own routes, including after the natural close; this is not
a terminal journey lock.
Journey starts accept a closed, explicitly selected `contact_type`: `first_contact`
(the backwards-compatible default) or `second_contact`. It initializes the existing
journey stage and strategy request key; it does not restore an earlier journey,
prove a previous interaction, link an earlier answer or bypass authentication.
Second-contact explanations address the remaining question in the selected
approved style. Member recaps remain untrusted input, never a factual source.
Each contact still starts with the AI disclosure
and retains all value, output and human-escalation gates (B2, B9, Art. 50).
Letter help acknowledges that a personal letter is unseen and invites a relevant
passage without personal details; excerpts remain untrusted input, not official
values or instructions. Uncertainty must remain explicit. A proposed handover
must not be described as completed until its durable effect is confirmed.

### Customer-scoped conversation continuity

The authenticated, step-up-verified
`MemberIdentity.member_ref`, namespaced by the configured trusted issuer and
Foundry project, owns a durable Foundry conversation across turns,
new contacts, channels and orchestrator restarts. A closed contact selector is
not a memory reset. A new customer or absent earlier exchange gets no invented
recollection. Each administrator test journey has a separate conversation;
it neither reads nor updates ordinary customer history.

Session start recovers the verified customer's conversation before releasing
the audited greeting, using the same customer-before-journey lock order as a
turn. Retained dialogue selects an approved returning greeting regardless of
the contact selector. `second_contact` without retained dialogue selects an
approved follow-up greeting that explicitly states the earlier conversation
is unavailable; it does not create fictional history. New customers retain
the existing first-contact greeting. Every variant includes the AI disclosure
and human-help offer, and the disclosure version is recorded in the audit.
The [closed channel start contract](../src/governedgraph/voice/voice_live_bridge.py)
accepts only these approved greetings, not arbitrary text containing an AI label.
Greetings neither replay historical text or figures nor append a customer turn.
Recovery failures prevent greeting release; a busy customer produces the
retryable conflict rather than an uncontextualized first-contact greeting.

The [continuity adapter](../src/governedgraph/orchestrator/continuity.py) keeps
only an opaque owner key, Foundry conversation ID and pending commit references
under `customer-context/` in the existing `journey-state` container. Ordinary
journey enumeration excludes that prefix. Customer leases serialize concurrent
contacts; customer locks precede journey locks. A busy customer returns the
existing retryable conflict, not a second concurrent model turn. Channel bodies
and model tools cannot select customer or Foundry conversation identifiers.
Non-step-up identities receive no persisted personal context.

Only screened customer text paired with the final checked, audited answer
committed for release is appended, never rejected drafts, internal decisions,
tool outputs or withheld/escalated turns. A reference-only prepare journal is
saved before the Foundry append. Recovery checks the durable journey request
commit and flushes its existing audit/delivery outbox before accepting the pair;
uncommitted appended items are removed before any later model invocation.
Ambiguous appends are not blindly retried. Retention is the most recent **20
completed turns**, not a time-based retention promise. Older Foundry context
items are pruned on successful commit/recovery; cleanup failure prevents reuse
and surfaces an error for retry. Immutable audit/event history is not deleted.
A released reply is not proof of receipt, voice playback or comprehension.

Prior dialogue is re-screened through Prompt Shields in provider-bounded batches.
Historical numbers are masked before role-labelled dialogue is supplied as untrusted
reference data to the Decision model (intent, understanding and answer review)
and the writer. Current authorization, authoritative value reads, approved retrieval,
signed ranking and all output gates still run. History never authorizes tools,
restores an old journey state or becomes approved factual evidence. Demographic
self-descriptions are not profile features and must not affect treatment.
The [context contract](../src/governedgraph/agents/conversation_context.py) is
closed; no demographic attribute or caller-selected identity is added.

Inference remains behind APIM, with `store=false`; intra-answer tool rounds
carry their inputs/outputs only in process memory. Accepted dialogue is retained
through Foundry Conversations CRUD at the existing private project endpoint,
using the existing runtime identity and permissions. This avoids automatically
appending an unchecked model response to durable customer history.
There is no new resource, grant, deployment mode, migration, bootstrap step or
deployment-dependent repository check.

Operational memory is not model training or modification of prompts, templates,
models or catalogue: B4 continues to prohibit those transcript-driven changes.
P2 still reads closed aggregates only, never Foundry conversation content.
See [focused regression evidence and limits](evaluation.md#customer-scoped-conversation-continuity).

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

The source catalogue's S1-S5 version 1.1 adds `second_contact` eligibility without
changing first-contact eligibility or template versions. Version 1.0 definitions
remain available for historical evidence and verified predecessor selection
during rolling delivery, not selection after upgrade. Separate
second-contact expert priors emphasize focused teach-back and clarification;
they never borrow first-contact outcomes. S6 remains unchanged and always
available. The catalogue, expert baseline and revised counsellor instructions
are a material change requiring reviewed Tier 2 delivery and newly verified
signed snapshots before live activation (B3, B5, B8, B10). Updating source alone
does not activate them or certify conversation quality.

Delivery's shared Foundry project client uses a certificate-verified P-256
transport, because hybrid key-share offers from some OpenSSL builds can be
closed before HTTP. Certificate checks are never disabled and TLS is never
downgraded. Selected-tenant credentials and approved immutable writer pins are
unchanged. Request/response transport failures become explicit delivery errors
so both delivery entrypoints finalize failed-stage evidence rather than leave a
running receipt. No identity, grant, network path or runtime fallback is added.

Sample catalogue and priors explicitly cover every catalogue-eligible combination
of explanation intent, journey stage, channel and accessibility mode. New
contexts use the approved default order, except second-contact contexts use the
focused second-contact order; eligibility filters apply before signing. The
signed snapshots, not an unsigned runtime default, authorize treatment. The same
complete priors seed P2 and the control arm, and regression coverage checks all
supported contexts. An upgrade that adds coverage archives prior state and resets
live ordering to the approved baseline; protective suspensions and pauses remain
effective.

Source-pack upgrades use an explicit reviewed predecessor pack and exact target
hashes in [source_upgrade_v1.json](../examples/pension_wtp/data/catalogue/source_upgrade_v1.json).
They are a separate existing-target operation through
[`scripts.upgrade_catalogue`](../scripts/upgrade_catalogue.py), not part of normal
deployment. Normal deployment initializes expert priors before workloads and
does not load or sign upgrade metadata. Empty state and snapshots matching the
running catalogue do not depend on old upgrade files, even for future fresh
catalogue versions. The exceptional upgrade alone rolls out compatible workloads
before state activation. The existing P2 signing
identity signs the human-approved plan and target expert baseline; the separate
Strategy Service initializer verifies both and archives the entire old signed
state before an ETag-conditional manifest update. New versions use their approved
expert priors, never old-version learned ordering. Suspensions and owner pauses
survive unchanged. Frozen state, pending or widening owner decisions and
owner-managed catalogues block this source-pack upgrade. Immutable history is
retained; no permission, schema-field, unfreeze or reinstatement bypass is added.
See [explicit upgrades](deploying.md#explicit-source-catalogue-upgrade).

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
| B3: approved strategies only | [Strategy Service](../src/governedgraph/strategy_service/service.py), [source upgrades](../src/governedgraph/strategy_service/source_upgrade.py), [host tools](../src/governedgraph/agents/tools.py) | [Signed Azure state](../tests/test_strategy_azure.py), [catalogue publication](../tests/test_foundry_publication.py), [upgrade gates](../tests/test_bootstrap.py) |
| B4: no conversation-to-behavior path | [Closed aggregates](../src/governedgraph/common/contracts.py), [feedback projection](../src/governedgraph/learning/feedback.py) | [Ranking inputs](../tests/test_ranking_bounds.py), [learning feedback](../tests/test_learning_feedback.py) |
| B5: bounded reorder-only learning | [Ranking pipeline](../src/governedgraph/learning/ranking_pipeline.py) | [Ranking bounds](../tests/test_ranking_bounds.py) |
| B6: fairness veto for everyone | [Fairness agent](../src/governedgraph/agents/fairness_explainability.py), [read-time overlays](../src/governedgraph/strategy_service/service.py) | [Fairness veto](../tests/test_fairness_veto.py), [Azure overlays](../tests/test_strategy_azure.py) |
| B7: no runtime demographics | [Closed contracts](../src/governedgraph/common/contracts.py), [customer context boundary](../src/governedgraph/orchestrator/continuity.py), [APIM policy](../infra/policies/strategy-request.xml) | [Demographic rejection](../tests/test_contracts_no_demographics.py), [customer isolation](../tests/test_customer_continuity.py) |
| B8: human material-change approval | [Tier routing](../src/governedgraph/learning/run_cycle.py), [assurance committee](../src/governedgraph/learning/assurance_committee.py), [Strategy Service](../src/governedgraph/strategy_service/service.py), [signed source upgrade](../src/governedgraph/strategy_service/bootstrap.py) | [Reinstatement](../tests/test_strategy_service.py), [owner decisions](../tests/test_admin_owners.py), [writer approval](../tests/test_writer_approval.py), [upgrade gates](../tests/test_bootstrap.py) |
| B9: human help always available | [State machine](../src/governedgraph/orchestrator/state_machine.py), [escalation tools](../src/governedgraph/agents/tools.py), [closed intent routing](../src/governedgraph/agents/decision_agents.py) | [Every-state escalation](../tests/test_state_machine.py), [durable handover](../tests/test_durable_orchestrator.py), [Decision model checks](../tests/test_decision_agents.py) |
| B10: continuous governed control | [Arm assignment](../src/governedgraph/strategy_service/service.py), [uplift kill switch](../src/governedgraph/learning/uplift.py) | [Uplift](../tests/test_uplift_and_design_agents.py), [signed static rollback](../tests/test_governed_static_contracts.py) |
