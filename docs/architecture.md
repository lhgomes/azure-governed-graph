# Architecture

This page describes the reference architecture on Azure, using the bundled pension sample as the worked domain.
The domain-neutral model is in [framework concepts](framework.md).

**Scope:** build depth on the Member Communication Agent (Counsellor), the Fairness &
Explainability Agent and the Learning Member Journey Graph. The pension sample adds Transition Calculation, Event
Processing and the Transition Adoption Guide at design depth. **Delivery:** one release, all features and learning live from day one.

**Evidence.** Statements here describe the design and what the code enforces. They are not a live-state check of any deployment:
verify yours with the [runbook](../examples/pension_wtp/docs/demo-runbook.md) procedures and record the results in your own
log. `IMP-nn` labels in these documents identify entries in the maintainers' local (unpublished) decision log.

The design-depth responsibilities, tools and "May NEVER" clauses
are specified in [agents-design-depth](../examples/pension_wtp/docs/agents-design-depth.md).
Their [versioned APIM operations](../examples/pension_wtp/docs/api/design-depth.openapi.json) and
[Service Bus message schemas](../examples/pension_wtp/docs/api/design-depth-messages.schema.json) are
backed by offline stubs only: no new deployment, LLM, identity or permission.
The [implementation notes and clause-to-test matrix](../examples/pension_wtp/docs/agents-design-depth.md#implementation-and-contract-exports-imp-34-supplement)
distinguish local queue receipts and synthetic submissions from production
administrator effects.

## Runtime sequence (P1)

The separate [administration console](administration.md) projects strategy, governance and response-audit
evidence and exposes audited protective controls. Locally it is loopback-only over synthetic files. On Azure it is
an internal Container App with its own identity behind the APIM gateway at `/admin/`: it reads three immutable
evidence containers and its own write-once journal at container scope, and acts only through the role-protected
Strategy API (`Strategy.Freeze` and `Strategy.Suspend`, never publish or reinstate). It is not a member channel or a
learning input. Optional conversation drill-down reads only previously captured, watermarked synthetic `gg-dev`
traces and is off by default on Azure; no transcript retention is added.

1. Member -> channel (Voice Live or chat): AI disclosure (Art. 50)
2. Orchestrator: create/resume journey; step-up verification before personal values
3. Content Safety Prompt Shields screens the utterance (fail closed)
4. Orchestrator -> versioned Intent Routing Agent: closed semantic decision using
   bounded journey context and accepted taught-concept IDs, without member identity
   or demographics. Explicit workflow branches distinguish explanation,
   clarification, understanding, advice, human request and technical failure.
5. `search_approved_content` - approved versions only
6. `get_member_values` via APIM - read-only, member-scoped, circuit breaker
7. `get_ranked_strategies` - signed snapshot, suspensions applied, control arm on expert priors
8. Compose a question-specific answer in the approved strategy's style from
   screened passages and official placeholders; validate the closed draft and
   citations, bind only needed figures; numeric-fidelity + steering checks.
   A separately pinned, tool-free Answer Quality Judge checks relevance,
   language and factual support using cited source bodies and official labels.
   At most one reference-preserving writer revision and one judge verification
   are allowed; all financial/reference gates run again before release.
9. `write_explainability_record` durably commits the checked response metadata or its independent retry outbox
10. Commit the response-linked JourneyEvent; release explanation/teach-back/escalation only after the audit write-ahead barrier
11. Outcome event -> durable runtime projection -> immutable aggregates -> P2;
    operational telemetry is not a learning input (B4)
Alt: option-level or personal-advice intent -> `escalate_to_advisor` (GNA)

**Closed question focus and host-owned structure.** A closed question focus reaches the writer, the repair and the independent
judge without changing the strategy ranking key. Native writers generate bounded explanation parts and a separate meaning-check
question; the host includes the question only for the approved strategy's teach-back setting and renders the approved S5 structure
itself. Runtime answer and audit contracts stay unchanged. The judge returns closed issue codes and verbatim rejected-draft quotes
for one bounded repair using already-authorized evidence; quotes are transient and never audit or learning inputs, and every
offending clause must be identified. The review verdict and issues are derived from the individual checks and the evidence
status, so no failed check or feedback issue loses its veto, and advice still bypasses repair. An uncertainty answer states the
unknown directly and in the first person and may offer to arrange confirmation of that specific point; it never narrates the
evidence pipeline or claims a handover has happened. Cached strategy reads check ETags through ordinary properties reads rather
than generating handled HTTP 304 exceptions; signatures and suspension overlays remain mandatory and genuine errors keep their
failure status. Telemetry uses bounded connection and read timeouts and sanitized local retry envelopes; that buffer does not
survive replica replacement, so verify actual ingestion in your deployment. Judging a stronger model against the baseline is
a B8 human decision.

### Agent Framework and feedback implementation (IMP-43/44/45/51)

Both runtime apps and the native P2 jobs use immutable release images on identity-separated infrastructure with the verified
Foundry adapter.

`agent-framework-core==1.23.0` executes both graphs:

```text
Member: screen-input -> intent-agent -> explain-with-approved-tools -> commit-checked-evidence
                       intent-agent -> understanding-agent -> explanation / clarification / checked outcome
                       intent-agent -> clarify-question / requested-human-handoff / personal-advice-handoff
                       required service failure -> service-failure-handoff -> commit-checked-evidence
Learning: aggregate -> bounded-rank -> fairness-veto -> sign-govern-publish
```

IMP-51 replaces the old semantic routing wrapper with separately versioned,
tool-free Intent Routing and Understanding Assessment agents. Closed decisions,
not keyword matches, drive the explicit branches. Context is reference-based
state/prior intent, closed output language and accepted taught-concept IDs, not
raw stored conversation history. Understanding is assessed only against concepts
targeted by the actual accepted teach-back, never against untaught template facts.
IMP-56 replaces the former self-edit with the independent grounded judge;
non-pass uses an explicit approved fallback, not a semantic-success claim.
The welcome introduces the AI guide warmly without implying access to a personal letter.
For letter help, the writer acknowledges the request, briefly clarifies that the letter is not
visible, and invites the relevant sentence or section without personal details; any general
context stays brief, approved-source-supported and distinct from the unseen letter.
The host declares that no personal letter has been supplied and preserves
same-journey human help. Shared excerpts remain untrusted data, not official values or instructions.
Role/version/closed-decision evidence in explainability
contract 1.3 is linked to each audited
response; only confirmed understanding produces an observed outcome.
The existing versioned Counsellor remains behind the explanation executor.
The native Foundry framework provider currently conflicts with the
verified Projects SDK pin; no silent downgrade or hosted-agent migration is
claimed. Durable reference-only journey transactions, output gates, signed
rankings and segregated fairness remain authoritative; framework execution
messages containing utterances are never checkpointed.
Normal answer completion does not close chat or presenter voice; follow-up
questions re-enter explanation, including during comprehension checks.

IMP-52 extends the explanation executor with previous approved template/strategy
references, not transcript memory. A closed answer envelope identifies supporting
retrieved content. It need not repeat the complete financial template or every
figure on follow-ups; all quoted figures still come exclusively from bound
official placeholders. Invalid drafts retain the checked-template fallback.
Existing state blobs are not re-initialized every turn; conditional creation and
renewable leases still protect concurrent replicas. Idle Service Bus senders are
recycled before reuse, with bounded same-ID recovery and safe cause diagnostics.
Requested/mandatory human handover remains terminal and is never disguised as
an ordinary explanation. Ending the channel does not fabricate resolution.
Offline semantic roles are labelled exact rehearsal fixtures, with explicit
failure for unseen text; they are not evidence of hosted agent accuracy.

Tracing is content-free by default. An explicit gg-dev synthetic-presenter
opt-in permits only host-owned user/checked-final messages after durable delivery,
for allowlisted synthetic members. SDK drafts, system prompts, tools, credentials
and exception bodies remain excluded. Safe dependency status/type metadata and
handled/recovered annotations are observational, never learning inputs.
The existing identity-isolated scheduled P2 host also executes a framework
graph across its aggregate/rank/audit/veto/sign/tier/publish/uplift stages,
retaining its per-stage audit, lease, digest, baseline and checkpoint guards.
Both learning hosts merge missing, already-approved request-key priors into
their baseline before overlaying learned snapshot orders. This repairs sparse
historical snapshots without inventing evidence or allowing unsigned chat
fallbacks; explicit empty orders remain empty (IMP-48).

| Evidence | Durable location | Reader / purpose |
|---|---|---|
| Approved decision, strategy/version, snapshot, arm, response ID | Existing append-only explainability records | Runtime audit; joins delivered events |
| Delivered response and observed teach-back/handover transitions | Existing reference-only journey store | Runtime outcome projection, retries |
| Immutable historical-plus-eligible aggregate CSV | `learning-aggregates/live/<sha256>.csv` | Fairness and P2; closed demographic-free count schema |
| Latest projection, baseline/source/output digests, observed/pending/eligible counts | `learning-aggregates/current.json` | Select immutable evidence; not the ranking itself |
| Fairness-reviewed exact aggregate digest | Existing isolated `learning-audits` handoff | Pins the signer's cycle despite newer interactions |
| Signed ranking used by subsequent treatment journeys | Existing ranking snapshot store and Strategy Service | Runtime verified selection; control retains static priors |

The recovery worker projects outcomes off the voice hot path under an existing
runtime lease. Only committed, delivered approved explanations count.
Explicit successful teach-back is recorded immediately as observed
understanding; completed first-contact/repeat-contact evidence enters ranking
only after its fourteen-day observation window matures. No-response and
unconfirmed automatic resolution are not invented as successful teach-back.
Unconfirmed outcomes remain pending rather than being invented as failures,
even after the observation window closes.
The manifest distinguishes fresh observations from eligible evidence and
digest-identifies the illustrative historical baseline. Rebuilding is
idempotent, not cumulative double counting. The bounded dev projection scans
existing evidence; it is not represented as a production-scale warehouse.

Offline rehearsal also saves this projection under
`.run/demo/learning-aggregates/`; it does not pretend a fresh conversation has
already completed its repeat-contact window. A local cycle can consume its
exact CSV with `python -m governedgraph.learning.run_cycle --outcomes <path>`.
Existing cohort and B10 window inputs remain separately governed synthetic
history, not newly inferred demographics or a fabricated uplift result.
No Azure service, data-bearing resource or identity has been removed. The
aggregate-writer grant deployed under IMP-47 is container-scoped only.

## Offline ranking cycle (P2)
aggregates -> bounded ranking (min sample, max shift) -> cohort audit (the domain pack's cohort dimensions, e.g. age, gender or income band; min cell size) -> veto gate
-> sign (Key Vault) -> Tier 0 auto-publish / Tier 1 Agentic Assurance Committee -> Strategy Service. Control arm keeps static priors.

### Azure ML component packaging (IMP-33, live pipeline acceptance pending)

`learning/azure_ml.py` packages separate aggregate, bounded-rank, fairness audit,
veto, signing, tier-routing, publication and uplift components. Cache reuse is
disabled. The fairness node uses its own compute/identity and has no Key Vault
configuration; other nodes use P2 compute/identity. A prerequisite signs only
fixed synthetic diagnostic bytes to prove P2's snapshot-key positive control and
member-key RBAC denial. No member token is created. Missing dependency artifacts,
changed aggregate digests, unapplied vetoes and stale ranking baselines fail closed.
The Strategy API compares the expected snapshot ID atomically at publication.

The isolated audit executes Fairlearn MetricFrame over integer aggregate counts
and verifies exact parity with the pure-Python metric, including minimum-cell
suppression. Only the demographic-free audit projection leaves the cohort zone.
Tier 1 evidence is create-only in `learning-evidence`; unavailable durable evidence
prevents publication. Azure packs explicitly report pending human notification,
not a successful notification that did not occur.

Treatment exploration uses the existing approved illustrative threshold artefact
(version 1.0, rate 0.05, rank-shift bound 2). Assignment is stable by journey/request
key, independent of changing snapshot IDs. Suspensions and eligibility filter first.
Control, frozen, fallback and threshold-mismatched reads never explore. Exploration
journey/snapshot IDs are logged; no demographic request field is added.
The hosted Azure API enables exploration from construction. Offline/reference
instances are deterministic by default, with explicit activation in exploration
tests; bounded ranking itself remains active in both. This preserves the offline
teach-back demonstration and is not a runtime phase/calendar rollout.
Uplift uses only closed, watermarked arm/window aggregates; its kill switch invokes
`POST /strategy-admin/revert-to-static` with the existing protective Freeze role.
Repeated protective freezes cannot undo static reversion.

A parallel watchdog checks child job metadata so an audit allocation/node failure
can freeze even before audit Python runs. Its deployed custom role permits only
workspace/job metadata reads at the single ML workspace, not data-plane access.
The user explicitly approved that role and its workspace-only assignment. The four-hour recurring job is
enabled only after successful execution and the operator's live acceptance checks;
job completion alone is not evidence of the worked-example outputs or key isolation.
The P2 image target omits local cohorts, personas, aggregate seeds and member-value
fixtures; governed Azure inputs are required. The runtime image retains the
synthetic deterministic-values fixture only.

**Deployment shape.** Bicep deploys the private compute, the workspace private endpoint and DNS, outbound NAT, container-scoped
artifact and evidence grants and a workspace-only job-status reader role. Each learning cycle produces its own evidence: a full
cycle must complete all ten components with signed S5/S2, static-control and uplift checks, and a Tier 1 requirement that fails
is never relabelled as a pass. An expert-prior Tier 1 worked example may be produced for illustration only; it is labelled
`ILLUSTRATIVE - worked example, not published`, has `published:false` and never changes the live ranking or a protective freeze.
A watchdog chaos run proves that an audit execution failure before Python causes a real freeze and last-good rollback; a human
then lifts the freeze through the recovery path in the [runbook](../examples/pension_wtp/docs/demo-runbook.md).

Recurrence uses native Schedule triggers on the two existing identity-isolated jobs, with fairness first and P2 120 seconds
later (the audit duration plus a margin). Each has parallelism and completion count one, and both share the renewable Blob lease
in the ML artifact container. A protected, fresh, same-cycle audit handoff and the actual successful isolated execution are
rechecked before every P2 stage. A missing or failed audit, or loss of the lease, freezes through the existing alert and cannot
publish. No coordinator, identity, role, start permission or lock container is added, and P2 never gains fairness start or
override rights. Container Apps Jobs is the working learning host.

A separate manual fixture publisher can seed the watermarked repository outcomes, an illustrative uplift window and a segregated
cohort input for a dev environment. `deployDevFixtures` defaults to false, is hard-restricted to `gg-dev` and is checked again in
Python. It has two container-scoped writes and a dedicated-registry pull, never signer or runtime privileges. Production replaces
this stand-in with the telemetry pipeline and a DPO-governed cohort feed. Production has all features and learning from launch;
there is no runtime phase or calendar rule.

The compute uses distinct single-identity pools, no node public IPs or SSH, and an undelegated ML subnet with explicit
outbound-only NAT. Both pools use only the actual Blob datastore containers and the dedicated registry; P2 alone writes
`learning-evidence` and never receives cohort-zone or File/account access. The schedule gate requires all ten unique components'
completion metrics, P2 key isolation, durable Tier 1 read-back, positive uplift and live S5/S2 treatment plus an unchanged static
control. Root `Completed` alone cannot enable recurrence.

The authorized temporary hosting adapter is `learning/container_jobs.py`.
It calls the same P2 component runner and domain logic, substituting only a
metadata metric reporter and create-only, read-back-verified artifacts in the
dedicated ML `azureml` container. The operator-side coordinator starts separate
single-UAI P2/fairness jobs; P2 cannot start or override fairness. A parallel
watchdog reads execution metadata at those two dedicated job resources only,
including failures before component Python; unknown, missing, duplicate,
wrong-image/role or incomplete metadata never certifies a successful cycle.
No cohort fields enter the closed aggregate artifacts or runtime. Azure ML
remains the target; its duplicate component-port packaging defect is corrected.
Manual fallback hosting does not imply accepted recurrence or a way to undo a
protective freeze without governance.

The deployed gg-dev recovery route is human-only, default-disabled outside
this explicit environment/operator configuration, and separate from disabled
strategy reinstatement. The user's own Azure CLI delegated token must match
the exact tenant, human object ID, `Strategy.Recover` scope and CLI client.
The Strategy identity reads only execution metadata on the dedicated fairness
job; latest actual audit success requires the approved image, command and role.
Create-only, read-back-verified recovery journals record actor/time/decision/
reason before state mutation. A captured state revision prevents overriding a
concurrent protection, and failed/ambiguous state writes, audit rechecks or
completion-journal failures re-freeze. Recovery changes no ranking or veto.
Recovery decisions use single-use references matching
`^GB-DEV-\d{4}-\d{3}$`. Readiness
certifies the supplied unused reference against existing recovery records;
POST rechecks and claims a deterministic create-only intent to prevent races.
Unreadable history fails closed. Human/delegated authentication and audit/state
checks are unchanged; no recovery is executed by an assistant. The native fairness
command is also recognized for human recovery, without widening authorization.

Native recurrence is accepted only after two actual scheduled cycles with distinct signed Tier 0 snapshots, all guarded stages
and durable read-back, plus a negative proof: skipping the next audit must make the following scheduled pipeline fail before
publication and freeze and roll back to last good, with the Sev1 alert firing. The normal four-hour cadence, with P2 offset by
two minutes, is then restored, and the negative proof's protective freeze stays until a human recovers it. These checks certify
a run, not the current freeze state of any environment. Azure ML remains the target platform; the Container Apps host does not
claim that any Azure ML datastore authentication limitation is repaired.

### Foundry fairness operator (IMP-34, live accepted)

The separate versioned fairness prompt agent uses `gpt-compact` with exactly
four host-bound functions: audit, enforce audited suspensions, open a governance
case and prepare an authorized member explainability report. All arguments are
closed empty objects; the trusted operator host supplies scope. Deterministic
preflight applies protections regardless of the model, and reinstatement is
refused and routed to a durable Tier 2 case before a model response. Model text
cannot authorize behavior. Only receipts enter Foundry, never cohort attributes,
metrics, member references or records; full member reports stay in the host.
The existing segregated storage container remains the cohort zone because
Fabric is disabled. The explicit fairness managed identity has no credential
fallback. `gg-fairness-explainability` version `1` is registered; provisioning
pins the stable endpoint to that exact version, never `@latest`, and checks the
acknowledgment. Foundry's automatically created agent/blueprint identities have
no Azure RBAC assignments and do not replace the host's fairness identity.
The existing fairness job proves S2 suspension and a durable refused
Tier 2 reinstatement case using a per-execution image override, leaving its
native schedule/configuration unchanged. The host reads and verifies the pinned
definition and exact endpoint selector, then uses the official SDK agent endpoint.
Final access is project-scoped `agents/read` only, Agent Consumer at this single
agent only, and Blob Data Reader at the records container only. An initially
granted `responses/write` proved unused and is removed; full Foundry
acceptance is run again afterwards. No agent-write or broad Foundry User rights.
The create-only proof records S2 suspension, durable refused
Tier 2 routing and a host-only member report, without releasing the freeze.

## Durable orchestrator and human handover (IMP-14/IMP-15)

The Container Apps orchestrator uses a private Blob journey store with a
renewable per-journey lease. The state machine remains pure Python. State and
outbox commit atomically; request-scoped counsellors never persist values,
utterances, tokens or rendered answers. `MemberAuthenticator` is the member-authentication
port; the signed-claim reference adapter and direct values tool both enforce
current-request step-up. Each request forwards its original bearer token to APIM.

Every JourneyEvent is sent to `journey-events` with SessionId=journey ID and
MessageId=`{journey_id}:{event_id}`. Escalations go to `escalations` with
MessageId=`{journey_id}:escalation:{sequence}`. Both existing queues explicitly
keep duplicate detection disabled. Delivery is **at least once**: the outbox
retries after restart, and consumers commit message receipts and event projections
atomically. Handover uses the escalation ID as the contact centre's idempotency
key. Inbox/idempotency records have no automatic expiry because retry/DLQ redrive
has no finite upper window; see IMP-14 before designing any retention cleanup.

All five dependency failure domains (values, search, model, safety, strategy)
durably request a human instead of estimating. Outbox/store failure is explicit
503/409, not a successful handover claim. A background peek-lock consumer renews
locks, settles only after durable effect/receipt commit, and allows redelivery
after lock loss. Dead-lettered escalations alert the dev on-call destination.

The proposed dev-only contact-centre Container App has internal ingress, its
own Entra audience/identity, runtime-only app-role access and an isolated private
handover container. It records governed references and `synthetic_pending`
status, never member utterances or figures. A read-only member-scoped advisor
queue is exposed through the orchestrator, with JSON or HTML selected by Accept.
Production replaces the stand-in with Dynamics 365 Contact Center or the operator's
existing platform, behind the same idempotent adapter. **No live callbacks
are claimed.** An action group parameterizes on-call email; alerts cover DLQ,
escalation age, explainability/queue failures, safety outages and frozen/
non-control static strategy fallback.

The durable orchestrator/contact-centre deployment, duplicate-message proof and
paging notification receipt were completed in gg-dev under IMP-14/IMP-15.

## Durable response audit and reconciliation (IMP-32)

In Azure, `BlobExplainabilityStore` creates one block blob per opaque response
ID with `overwrite=False`, below a hashed member prefix. Existing dated JSONL
records remain readable across dates. An identical retry is a no-op; a changed
payload with the same immutable identity is a conflict, never an overwrite.
The existing 30-day WORM policy and protected append setting remain unchanged.
The policy remains unlocked pending the DPO decision; a live synthetic test
confirmed Azure denies overwrite with `BlobImmutableDueToPolicy`.

If the audit account is unavailable, the same closed, metadata-only record is
queued in the existing private journey-state outbox **on the independent content
storage account**, under the journey lease and atomic state commit. The existing
background recovery loop retries it after restart. No Service Bus queue, identity
or grant is added. If neither primary nor outbox acknowledges durable persistence,
generation is withheld, human escalation is requested, and the hosted API returns
503. A concurrent total journal failure cannot be described as a successful
durable handover; the channel must offer direct advisor contact.

The hosted orchestrator audits disclosure, explanations, acknowledgments and
escalations, each with a response ID also present on exactly one JourneyEvent.
Idempotency-key retries do not release another text answer. Withheld outputs are
marked separately; events are not invented to conceal historical gaps.
`python -m governedgraph.explainability.reconcile` is the on-demand reconciliation job:
it retries queued records and verifies exact one-to-one cardinality after event
deduplication. Missing/duplicate records, duplicate references, conflicting
events and unlinked delivery events in the acceptance scope prevent a complete report. An empty
scope is not reported as 100%. Pending retry acknowledgments prevent completion
even if primary record cardinality is already 100%; repeated journey selectors
are deduplicated. A scoped pass is not an all-history certification.

The reconciliation scope is every delivery. A `gg-dev` environment that was first deployed before response IDs were recorded may
configure an optional legacy boundary (`GOVERNEDGRAPH_LEGACY_CUTOVER_UTC`, a timezone-aware instant). Earlier deliveries are then
counted separately as `legacy, untracked (synthetic dev history)` and do not fail the dev job, while everything at or after the
boundary must link. The setting is honoured only when `AZURE_ENV_NAME=gg-dev`, supplied by deployment configuration; other and
unknown environments have no exception, and a fresh deployment sets none.
Production has linkage from day one. This is historical audit reporting only:
no runtime or learning phase/calendar rule, data repair or fabricated backfill.

`GET /members/{member_ref}/explainability-report` (APIM prefix `/journey`)
calls `assemble_explainability_report` for the authenticated member only.
Current-request step-up is required before reading data. Reports include all
that member's stored records, any durable pending records and delivered response
references; conflicting primary/outbox payloads fail explicitly. Successful
responses prohibit caching. These audit references contain no transcript,
rendered answer, token, member figure or runtime demographic field; there is
still no path from records/conversations to prompts, models or catalogue changes.

## Strategy catalogue (IDs fixed)
S1 plain-language summary · S2 worked example with own official figures · S3 visual before/after timeline · S4 what changes /
what stays the same · S5 step-by-step with teach-back · S6 offer a call with a human advisor (always available, not ranked).

## Foundry counsellor and guarded output

The Azure adapter uses **azure-ai-projects 2.9.0**, versioned prompt agents and
the Responses API, not classic threads/runs from azure-ai-agents. Instructions
remain in `INSTRUCTIONS`. The operator runs
`PYTHONPATH=src python scripts/create_agents.py` for a no-write plan and, only
after approval, adds `--apply` to create/reuse an immutable matching definition.
Set `AZURE_AI_AGENT_NAME` and the exact returned `AZURE_AI_AGENT_VERSION`;
runtime verifies the pinned model/instructions/tool definitions and never
creates an agent or selects `latest`.

Function requests execute only in host code against journey-bound
`CounsellorTools`. Member/identity/request-key arguments supplied by the model
are ignored, never forwarded. Other arguments use closed schemas; unknown tools
and model audit writes are rejected. Four tool rounds and eight calls per round
bound work. Read tools and protective escalation are available; explainability
is host-only after the gates pass. Tool outages and Foundry failures/timeouts
log content-free errors and escalate (IMP-14). Returned but invalid drafts still
use the checked approved-template fallback through `_post_process`.

Every model draft passes `_post_process`: preserve the template's placeholders,
reject literal figures, bind administrator display strings, require numeric
fidelity, reject steering, try the approved template only, then escalate if it
also fails. Model text is never returned directly. Explainability contract 1.1
adds required `agent_name` and `agent_version` alongside `model_deployment`;
local records identify the local template composer. Advice escalations are also
recorded, even before any ranking was fetched.

In Azure, `agents/azure_safety.py` supplies Prompt Shields
(`text:shieldPrompt`, 2024-09-01), screening both utterances and retrieved
documents using a dedicated keyless France Central ContentSafety resource.
Missing/partial/nonboolean analyses or any dependency failure deny
generation. The versioned Intent Routing Agent uses the existing full
`gpt-counsellor` deployment for contextual factual/advice/human routing.
The separate Understanding Assessment Agent uses `gpt-compact`; uncertainty
clarifies without recording failure. No runtime keyword/boolean classifier
remains. Required-service failures take an explicit technical branch.

Foundry tracing uses a real OpenTelemetry root span with a trace ID derived
only from the pseudonymous journey ID. Azure SDK calls inherit that context;
the values facade receives its W3C traceparent. Native SDK prompt/response content recording
and baggage propagation are explicitly disabled; no `trace_function` decorator
captures function arguments/results. Root attributes are journey ID, pinned agent name/version, deployment and
content-free turn/screening durations; voice acceptance uses the two-level
targets in voice SLO table below.
The existing Application Insights
resource is linked to the project, with runtime Monitoring Metrics Publisher
scoped only to that resource and `DefaultAzureCredential` for export.

The synthetic live proof uses the existing in-network dev-issuer job and its
existing dedicated identity only, with an in-memory short-lived member token
forwarded unchanged in Authorization on every request. No new identity, grant,
network path, local issuance, developer Key Vault access or token-transfer
helper is approved. The in-process Azure demo's token-file branch must not be
used for this proof. Runtime must
never execute the learning/cohort plane. Required evidence is existing signed
rankings and suspensions, S5/S2 plus a non-fallback Foundry draft, a Blob audit
record with pinned agent metadata, advice handover and a remote-screened
injection. Twenty voice-channel explanation turns (including
the first; no excluded warmup) historically reported nearest-rank p95 against
strictly less than 2000 ms. Screening time is included in the complete turn and reported separately,
not added twice. An SLO miss is explicit. Those historical HTTP voice-channel
measurements exclude STT/TTS and are not speech-to-speech SLO evidence.
The full live evidence requires explicit deployment approval; local validation
does not claim a Foundry trace or Blob write occurred.

### Governed speech channel (IMP-35)

### voice SLO table - Approved illustrative voice SLOs

The design's illustrative SLOs are set before launch. The reference deployment uses these
replacement voice targets, approved by the owner. Other service targets are unchanged.
Both limits are inclusive and start at end of member input:

| Level | Measure | p95 target | Accepted evidence |
|---|---|---:|---|
| Approved, figure-free acknowledgement | First acknowledgement audio dispatch | <= 1 s | 1.892 ms, n=20 warm server turns; browser 266.900 ms, n=2 |
| Figure-bearing answer | First fully checked substantive audio dispatch | <= 6 s | 5,605.955 ms, n=20 warm-turn window |

The window contains 16 successful figure-bearing explanations and four safe
escalations; the explanation-only nearest-rank p95 is also 5,605.955 ms (n=16).
No failed turn is removed from the original twenty-turn report.
Acceptance is for the retained `gpt-counsellor` deployment and the measured warm
operating mode. Three cold SDK/Speech controls remain separately reported,
not reclassified as warm or acoustic measurements. The browser sample is n=2,
not twenty acoustic observations.
Complete-audio readiness remains diagnostic; it has no replacement acceptance
limit and is not the first-substantive measure. Archived and deployed legacy
`target_ms`/`target_met` fields describe the retired complete-audio two-second
probe, not this approved acceptance decision; raw evidence is not rewritten.
Screening, advice classification, authorized values/content/strategy reads,
full draft, numeric fidelity, steering and durable audit are deliberately
retained before substantive synthesis. Their overlapping stage costs are
reported by `scripts/benchmark_voice.py` (see [evaluation method](evaluation.md)).

**Latency correction:** see the voice SLO table below.
The approved template-set 1.1 figure-free acknowledgement plays at end of turn,
concurrently with transcription and checks. It is explicitly distinguished from
the first substantive answer sentence and complete audio readiness; it never
asserts that gates or audit have completed. All input/doc screening, the OR advice
classifier and authorized read-only lookups still run. Voice preflight overlaps
independent operations, but the model waits for all gates and dependencies.
The whole draft passes `_post_process` and durable audit before substantive
synthesis starts. Exact checked sentences synthesize on four independent warmed
Speech sessions; ordered playback waits for each sentence's full protocol/text
validation. No raw delta or unchecked draft is streamed.
The public browser queues validated audio with non-overlapping scheduled playback
and ordered acknowledgments, so complete-audio readiness is not delayed by
waiting for each preceding sentence to finish speaking.

The co-hosted bridge and orchestrator retain at least one warm replica.
Process-local pin/HTTP/credential/Search reuse never caches member values,
utterances or model responses. A pre-session warm-up checks the pinned agent and
screening/classification adapters; it does not bypass per-turn checks. A separate
immutable compact voice candidate uses identical instructions/tools and is
restricted to the synthetic, authenticated dev benchmark until selected.
Twenty warm and three isolated SDK/Speech controls per candidate report p50/p95
and stage-specific n; they are not infrastructure restart or acoustic tests.
Prompt 11 is accepted under the approved two-level targets above. Historical
two-second misses remain evidence, not outstanding acceptance blockers.
Confirmed Service Bus senders are also reused under a lock; failed senders are
discarded and ordinary durable retry retains the same message/session IDs.
Neither audit nor outbox publication becomes fire-and-forget.

`voice/voice_live_bridge.py` is a channel adapter, not a second counsellor.
`POST /journeys` and `POST /journeys/{id}/turns` carry the same original bearer
token, closed request bodies, idempotency keys and traceparent as chat.
The bridge finishes playing the exact authenticated disclosure before accepting
audio. Azure SDK imports stay in `voice/azure_voice.py`.

Voice Live uses GA API `2026-04-10`, pinned SDK
`azure-ai-voicelive[aiohttp]==1.3.1`, manual PCM16/24-kHz input commits and
`azure-speech` transcription with the sample's Dutch legal phrase list. Automatic turn
responses and tools are disabled. Output uses only
`response.create.pre_generated_assistant_message`: this explicitly bypasses
model text inference and receives the final bound, guarded, audited HTTP text.
There is no ordinary model-response request, SSML figure rewrite or direct
Voice Live-to-Foundry-agent connection. The published direct agent quickstart
still uses a preview API; it would also bypass the required orchestrator exit.
Foundry agent pinning and output gates therefore remain behind the orchestrator.

Separate sessions select standard regional voices `en-GB-SoniaNeural`
(counsellor), `en-GB-LibbyNeural` (Annelies) and `en-GB-RyanNeural` (Kees).
No HD/global-preview voice is selected. Only simulator utterances, never persona
profiles, enter Speech/runtime. Audio, tokens and transcripts remain in memory;
completed service conversation items are deleted. Buffer bounds, deadlines,
response-ID/metadata binding and exact returned-text checks fail closed before
audio playback. Duplicate HTTP receipts are not replayed as guessed speech.
Audio failure requests a human through the same endpoint; failed handover
confirmation is an explicit error, not a claim of durable delivery.

The simulator paces all audio at its PCM duration and has a 120-second deadline.
Its scripted actor replays the fixed opening and teach-back fixture after a nonempty
counsellor reply; it does not depend on a particular generated question wording.
The orchestrator still assesses the answer. Only a resolved journey passes the
teach-back gate; escalation ends the simulation, and unfinished journeys stop after four turns.
Presenter takeover replaces only the input source with microphone capture.
Nearest-rank voice p95 separately measures end of captured input to approved
acknowledgement and first checked substantive audio, as voice SLO table specifies.
Complete output audio readiness still includes STT, HTTP orchestration and TTS
but is diagnostic, excluding playback and member speaking time. Total demo time
includes paced playback. The two-turn Annelies browser sample and twenty-turn
server window are reported with their actual counts, not acoustic certification.
Live status, authorization blockers and evidence are recorded in IMP-35;
offline protocol tests are not live acceptance.

The hosted adapter is `POST /voice/simulations` in the existing orchestrator
Container App, under its existing runtime identity. The synthetic endpoint is
enabled only for gg-dev and requires the verified demo member with step-up.
Its closed script contains only opening/teach-back utterances, never a persona
profile, identity, endpoint override or demographic. The dev issuer alone reads
the profile fixture, mints the fifteen-minute token and forwards it in the HTTP
Authorization header. The hosted bridge keeps that bearer request-local and
forwards it unchanged to the ordinary journey APIs. No token is persisted,
placed in container configuration or returned. This request-local use is the
explicitly approved hosted topology, superseding the earlier issuer-owned
Speech attempt.

The runtime already holds Cognitive Services User and Foundry User on the
existing AI account, so no grant is added. Its async managed identity credential
uses the documented `https://ai.azure.com/.default` audience; Learn also accepts
the legacy Cognitive Services audience. The issuer has no AI account role.
Two independent Speech sessions generate the member and counsellor audio.
The response exposes only proof metadata, audit IDs and content-free timing;
it is `no-store`. Historical two-second diagnostic results remain unchanged;
current acceptance uses voice SLO table rather than complete-audio readiness.

The approved gg-dev presenter surface is a dedicated delegated
`VoiceDemo.Access` registration with user assignment required and only the
operator assigned. PKCE sign-in at the exact public APIM origin yields a Secure,
HttpOnly delegated-session cookie. APIM and backend validate scope, role,
operator, tenant, audience and client; authenticated media uses an exact-Origin,
rate-limited WebSocket with no token query parameters. Static page/config confer
no media access. Only runtime has the issuer invocation application role.
The internal issuer app retains the dedicated dev-issuer identity/key-only
grant and signs only the fixed step-up synthetic member; no member token reaches
the browser. A socket owns transient audio/token state in one process, never a
persistent store. The `deployVoiceDemo` flag defaults false and runtime also
requires gg-dev. No new identity or Azure RBAC grant was needed.

Both nearest-rank first-validated-playback-dispatch and complete-audio-ready
p95s are reported with independent counts. No unchecked delta is streamed.
The refreshed 97.196-second browser fallback recorded acknowledgement-inclusive
first/complete p95s of 266.900/4,373.700 ms (n=2 each). Its first substantive
p95 is 3,982.100 ms (n=2). Acknowledgement and substantive audio pass the
approved 1/6-second limits; complete readiness is diagnostic.
The model matrix records all stages and the cold-control limitations.
This is not acoustic latency or SLO certification. Presenter microphone audio is never
recorded; the explicitly requested, watermarked synthetic video is the only
recording exception and cannot feed learning. See the voice runbook and IMP-35.

### A-2: regional screening and model safeguards

The reference deployment keeps the models in its primary EU region with DataZoneStandard;
Search may sit in a nearby EU region under a capacity fallback.
Standalone Prompt Shields runs in a nearby EU region (the reference uses France Central) when the Learn availability
table does not support it in the primary region, as a dedicated keyless resource. Runtime gets Cognitive Services User
only on that resource. The service processes inputs in the selected region
without storing them, human review or training; no blocklists are created
and the application never persists screening payloads.

Both gpt-counsellor and gpt-compact additionally use a Blocking Microsoft.Default-
based policy: Jailbreak and Indirect Attack on prompts, plus Medium harm filters
for Hate, Sexual, Violence and Selfharm on prompts and completions. Model blocks
fall back to the approved template through the same output gates; failed templates
escalate. Screening outages/timeouts instead go straight to a human.
Screened passages are supplied in document-delimited, JSON-escaped context to
avoid forcing an extra tool round while preserving journey-bound host execution.

### DORA third-party register note

Add **Microsoft Azure AI Content Safety, S0, France Central**, as a dependency of
member guidance screening in the operator's ICT third-party register. Data categories:
transient member utterances and approved retrieved passages; synthetic only in
this reference implementation. No blocklists, payload diagnostics or application
screening storage. Entra/managed identity only, with runtime-only resource-scoped
access. A dependency failure denies generation and hands over to an advisor.
Assess EU inter-region transit, additional screening/turn latency, availability,
Microsoft service concentration and exit/manual-advisor continuity together with
the existing Swedish Foundry and French Search dependencies. Contract identifiers,
legal-entity identifiers, subcontractors and critical-function classification must
be completed by the operator's DORA owner, not invented by this implementation.
Regional/privacy documentation was checked on 2026-10-22; see IMP-12's linked sources.

## Strategy Service persistence and authorization

Local mode keeps its offline state file. Azure uses the private
`ranking-snapshots` container in the isolated content-storage account, under a
dedicated Strategy Service identity, not the member-facing runtime identity.
This container holds learning-plane output, separate from board-approved P3
content. Account-level Blob versioning covers it. Verified snapshots are explicit
`current/<digest>.json` and `last_good/<digest>.json` blobs; `state.json` references
them and carries the frozen flag and global suspension overlay. Last-good is
therefore a real rollback blob, not just a storage version.

Every snapshot is verified against its Key Vault signature **and active catalogue
versions**, including on startup and rollback loading. The service never signs.
Its custom Key Vault role permits public-key read and verify only. P2 remains the
only application **snapshot** signer. The deployed IMP-13 revision scopes both
service/runtime verifier assignments to `ranking-snapshot-signing` and scopes
P2's public-key read/sign plus existing-snapshot verification to that key only.
The runtime verification-only role definition is unchanged. A separately gated,
dev-only issuer signs `dev-member-token-signing` only. APIM and orchestrator
validate member tokens with the configured public JWK, so neither needs a member
key grant. All four existing vault-scope assignments were explicitly revoked
after deploying and verifying the replacements, including ML's vault metadata
Reader. Static inventories cover all four identities; the issuer's own live
probe passed member-key signing and snapshot-key RBAC denial. P2's formerly
deferred snapshot-key positive control and member-key denial are proven on its own
private compute by the key-isolation diagnostic (IMP-13/33), and again in each resumed native cycle. The issuer identity was not changed.
IMP-16 separates the ML platform from this signer: its workspace uses
only a system-assigned identity, a dedicated operational vault with no signing
keys, dedicated keyless storage, a dedicated admin-disabled registry and its own
keyless Application Insights component linked to the existing Log Analytics
workspace. Azure ML's automatic grants stay on these dedicated resources;
`allowRoleAssignmentOnRG` is false. P2 is
attached only to the compute/jobs created by prompt 08, never the workspace.
Platform permissions target those dedicated resources, not the signing vault.
Do not restore a P2 vault grant to repair workspace provisioning.
State writes use ETag compare-and-swap with bounded conflict retry. Every Azure
`get_ranked` conditionally reads state, so another replica's committed suspension
applies on the next request, with no TTL propagation window. A startup storage
outage logs an alert and serves expert priors. A refresh failure uses priors and
retains every known suspension/frozen flag; it never clears the overlay to make
the read succeed. Failed suspension/freeze writes return 503 so callers retry or
escalate. Snapshot-load, suspension and freeze audit logs include the snapshot ID
and are exported to Application Insights with managed-identity authentication.

Mutation routes validate Entra v2 access tokens (signature, tenant, issuer,
audience and expiry), require application-only tokens and the appropriate app
role, and independently constrain the principal:

| Route | App role | Identity |
|---|---|---|
| POST `/snapshots` | `Strategy.Publish` | P2 pipeline only |
| POST `/suspensions` | `Strategy.Suspend` | Fairness & Explainability Agent only |
| POST `/freeze` | `Strategy.Freeze` | Runtime, fairness or P2 |
| POST `/revert-to-static` | `Strategy.Freeze` | Protective uplift kill switch; runtime, fairness or P2 |
| POST `/reinstatements` | No app role | Delegated human in configured governance group plus `GB-` decision |
| GET/POST `/freeze-recovery` | No app role; delegated `Strategy.Recover` | Exact approved human/CLI in gg-dev, latest actual audit succeeded, approved dev decision, durable recovery journal and state-revision guard |

Ranking reads and GET `/snapshots` need no mutation role. The service's own
identity has none of these app roles. Reinstatement is disabled while the human
group is unset (the current user-selected configuration). APIM exposes admin
operations at `/strategy-admin`; backend validation also protects direct private
calls. Local-only development routes do not pretend to validate Entra tokens.
`X-Strategy-Replica` and `Server-Timing` on rank responses allow separate
next-request and network-inclusive processing measurements. The voice SLO table
in-memory p99 test measures 5,000 requests and requires strictly less than 50 ms;
it is not evidence of the network-inclusive Azure latency.

P2 storage access is scoped to aggregate reads, demographic-free audit
projections and the new workspace's explicitly discovered artifact containers.
Prompt 08 must discover/authorize those new containers before workload execution;
old workspace container names are not reused. P2 has no access to `cohort-zone`.
IMP-16 replaces the verified-empty workspace and explicitly retires five obsolete
P2 platform grants; incremental ARM alone does not revoke them. Old shared stores,
signing keys and explainability retention are preserved. See IMP-10 for the
earlier storage split and IMP-16 for current platform isolation/replacement proof.

In Azure, the fairness identity separately runs
`python -m governedgraph.learning.azure_inputs`: it reads the isolated cohort zone, applies
vetoes through its Suspend role, retains full findings/cases in that zone, and
publishes only a closed request-key/strategy veto projection to `learning-audits`.
The projection binds the exact de-identified outcome digest and threshold version.
P2 then runs `python -m governedgraph.learning.run_cycle --fairness-audit-id <id>` under its
own identity. It reads `learning-aggregates/outcomes.csv`, rejects every unknown
column (including demographics/transcripts), verifies the matching audit hand-off
and committed vetoes, and publishes through the authorized API. It never runs
the cohort audit under P2 or reads the cohort inputs. There is no Azure fallback
to the repository's synthetic seeds when those governed inputs are missing.
All reference data is ILLUSTRATIVE - synthetic data.

## Operational observability and costs (IMP-37)

Content-free OpenTelemetry joins the orchestrator, counsellor/tools, APIM and
Strategy Service on the deterministic journey trace root, including creation.
The existing managed identities publish operational metadata, never raw
utterances, member values or model bodies. APIM records zero body bytes.
The Entra-authenticated Responses front door preserves pinned agent versions
and enforces conversation quotas without retries or silent model substitution.
Foundry BYOM currently excludes Responses. Under the user's prompt-13 acceptance,
model failover between deployments through APIM remains a production item once
Foundry supports it. Until then retain permitted checked text or human handover
on model unavailability; no silent model substitution.

The deployed workbook shows the approved voice SLO table targets, sample sizes,
escalations, numeric-fidelity blocks, last served suspension overlay and
aggregate uplift decisions. Agent roles are resource tags; journey IDs are
telemetry dimensions, not shared-resource tags.
The cost query requires fourteen complete days without observed repeat contact
and includes failed-journey costs. It reports sourced model-token list cost
only, with null for missing usage or an immature denominator. Full-stack
allocation and ingestion reconciliation remain prerequisites for financial
reporting. The open full-stack method is Azure Cost Management filtered by
`azd-env-name=gg-dev`, divided by qualifying resolved queries for the same
scope/window (section 8.6); the user will inspect the portal without additional
permission requests. See the [operational runbook](observability.md).

## Native vs custom
Native/configure: Foundry Agent Service, models, evaluations and tracing, Voice Live, AI Search, APIM, Service Bus, Content
Safety, Entra ID / managed identities, Key Vault, Azure ML + Fairlearn, Fabric (validate A-3), Monitor. Custom: agent logic and
contracts, orchestrator, Strategy Catalogue, Strategy Service + signed snapshot, ranking pipeline, veto gate, explainability store,
persona simulator. Excluded: Azure AI Personalizer (retired; unconstrained online learning conflicts with B4/B5).

## Content governance hand-off
Production content is authored, labelled and approved in SharePoint + Microsoft Purview (P3).
The P3 publishing integration exports an immutable passage version, stable `content_id`,
language, journey states and the human `approval_ref`; it invokes
`python -m governedgraph.search.reindex_on_approval /secure/p3-approved-document.json`.
An approval reference is mandatory but this synthetic implementation cannot verify a real
SharePoint/Purview approval: production must authenticate the publishing integration and
verify that reference against its P3 approval record. Raw conversations, outcome telemetry,
ranking pipelines and runtime identities must never author or approve this feed.

The reference corpus is `data/content/approved_content.jsonl` (ILLUSTRATIVE - synthetic data).
`python -m governedgraph.search.ingest` validates it before contacting Azure and seeds an authoritative
`corpus.jsonl` feed only if none exists. Later runs preserve the existing feed, including its
newly approved and retired versions; they never restore a superseded version from the seed.
The feed lives in a separate keyless storage account, isolating it from runtime's existing
explainability-storage permissions. Search has only container-scoped Blob Data Reader access,
plus Cognitive Services OpenAI User on the existing AI account. The deployment publisher has
container-scoped Blob Data Contributor; production substitutes its controlled P3 identity.

Azure Policy disables public access on the content account. A Blob private endpoint and
private DNS connect the existing Container Apps VNet; Search uses an owner-approved shared
private link and `executionEnvironment=private` (supported for embedding-only skillsets on
the existing modern Basic service). The manual `job-gg-content-publisher` uses a dedicated
P3 identity with content-container write, Search service/index publishing and ACR pull roles,
but no cryptographic or member-facing rights. The postprovision hook calls
`python -m governedgraph.search.run_publisher_job` through Azure's control plane and requires actual
job success. It refuses to start while the Search private link awaits owner approval.
Never reopen storage public access to make a workstation hook pass.

From a workstation, publish a P3-approved synthetic JSON document with
`python -m governedgraph.search.run_publisher_job --document /secure/p3-approved-document.json`.
The wrapper validates the closed document contract, preserves the full private job template,
and sends the approved document only to its isolated P3 execution. The direct
`reindex_on_approval` command remains available to an authorized private-network runner.
The invoking identity needs job execution permissions, not storage keys or runtime signing.
Build the publisher image before provisioning updates and set `CONTENT_PUBLISHER_IMAGE`
to its immutable ACR tag; its code must match the release being deployed.

Publication holds an exclusive Blob lease, durably records the new approval and predecessor
retirement in the feed, retires previous indexed versions of the same content/language,
verifies retirement is visible, and only then runs the unscheduled Search indexer to
publish replacement vectors. Recording retirement first prevents a retry from reinstating
an old version if the publisher fails between the source and index updates.
Failed document updates, indexing errors,
warnings or timeouts are explicit failures, not successful publication. Retirement happens
first: an indexing failure can leave an availability gap, never intentionally restore stale
content. Search batches are not transactional. Do not run indexers manually outside this
single-writer publishing workflow. After a publisher crash, inspect indexer completion and
the feed before an authorized operator breaks an abandoned infinite Blob lease and retries.
An existing version cannot be edited or reinstated, including under an alias ID:
the `content_id`/`version`/`language` tuple is unique. Approve a new immutable version instead.
Success additionally requires exact active source IDs/version/approval/language metadata
and vector-only retrieval of every active ID (in bounded exhaustive batches). A successful
but zero-document indexer run is not proof of vectorisation. The data source reads its
dedicated container: its optional `query` is a virtual directory, not an exact filename.

The Blob JSON-lines indexer uses the integrated `AzureOpenAIEmbeddingSkill` with the existing
`embedding` deployment (`text-embedding-3-large`, 3072 dimensions). Each already-approved
passage is embedded without model-generated rewrites or runtime chunking. Overlong passages
must be split and approved upstream. The index vectorizer embeds query text through Search's
managed identity. Runtime combines keyword, vector (`k=50`, pre-filtered) and semantic ranking.
Every query filters `status eq 'approved'`, language and journey state; status, version,
language and journey states remain filterable. Responses without approval evidence fail
closed. Each retrieval replaces the tools' previous passage context so subsequent turns
do not reuse retired cached passages.

Member-selected accessibility `standard`, `plain_language`, `slower_pace` and `large_text`
retrieve the domain pack's default language (Dutch, `nl`, in the sample); `english` retrieves English (`en`). This is content retrieval locale,
not a demographic inference or permission to translate approved templates automatically.
The pure-Python local path uses the same version, approval and locale rules with lexical
retrieval, without Azure imports or model calls.
