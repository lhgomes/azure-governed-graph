# Governed observability and operational cost

ILLUSTRATIVE - synthetic data. Telemetry is evidence, never a learning input.

## Trace and privacy contract

The W3C trace root is the existing SHA-256-derived journey trace ID. The
orchestrator, counsellor, host tools, APIM and Strategy Service share it.
Incoming parents from a different journey are not adopted. Parallel screening
and read-only preflight retain copied context. No demographic field is added
to a runtime request. `contact_key` is a linkable one-way pseudonym of the
existing synthetic member reference, not anonymous data or a ranking input.

Only allowlisted operational scalars are emitted: journey/request/agent IDs,
model deployment, usage counts, outcome, duration and guardrail results.
Native Foundry content capture and baggage propagation stay disabled. Exception
payloads are not added to spans. Entra-authenticated Azure Monitor exporters
use bounded connection/read timeouts and one-hour local retry buffering of
sanitized envelopes. The buffer does not survive replica replacement;
successful flush alone is not proof of ingestion. Existing operational console logs remain.
Decision failures emit `decision_model_failed` with the journey/event IDs,
closed failure-domain code, exception type, HTTP status (`0` before a response),
and a bounded host-defined question identifier when applicable. Response bodies,
exception messages and text are not logged. A `low_confidence` failure also logs the top
two closed labels (`top_choice`, `runner_up`) and their scores, so the margin to the gate is
visible; the labels are the host's own criteria keys, never member or answer text. An optional
question (the answer-review concept checks) that is read without enough confidence logs
`decision_choice_unclear` with the same fields instead of failing the turn. Valid returned
token usage is recorded even if a decision is subsequently rejected; abstention
does not imply missing usage. Chat inactivity emits `chat_idle_expired` with the
journey ID and ends the channel normally, without claiming a message may be saved.
Review question identifiers are `verdict` and `concept_` assessments; each must satisfy
the unchanged confidence gate. `answer_review_quality_waived` (journey ID and issue labels only) marks
a draft delivered without teach-back because relevance or teaching style were the only rejected issues.
Decision throttling uses the existing `model_rate_limited_retry` event and
per-turn waiting budget; a final failure still emits `decision_model_failed`.
**Model-call record (gg-dev only).** With `GOVERNEDGRAPH_LOG_LLM_CALLS=true`, which infra sets only for gg-dev,
each model call emits `llm_call` events with its `journey_id`, a `call_id`, the `kind` (`decision`, `writer`,
`review`, `persona`, `fairness`), the deployment, `part` (`request` or `response`), `chunk`/`chunks` and the
`text`, plus an `outcome` (`ok`, or the failure domain). Join the parts of one `call_id` in order to read the
full payload. Headers are never recorded and credential-like text is withheld. This is the one deliberate
exception to the content-free log, it is read from the container console or `ContainerAppConsoleLogs`, and it
must be switched off before real data is used. `output_gate_blocked` names the gate that blocked a draft or
the approved template, using closed `draft:`/`template:` reasons only.
APIM captures **zero body bytes**, no Authorization header and no client IP.
Suppressing both bodies is stronger than attempting to redact arbitrary
personal text with a regex; there is no raw-prompt wiretap store.

**Explicit synthetic-only exception:** the `gg-dev` configuration must
enable both `GOVERNEDGRAPH_DEPLOY_VOICE_DEMO=true` and
`GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS=true`. Only allowlisted synthetic members'
host-owned user and checked-final messages may enter the dedicated conversation
span, after durable delivery, labelled **ILLUSTRATIVE - synthetic data**.
Credential-like or oversized text is withheld. SDK drafts, system prompts,
tool payloads and exception bodies remain excluded. This is operator inspection
evidence, never a learning input or permission to capture real conversations.

### Native Foundry trace visibility

The Azure Core OpenTelemetry bridge must be selected before the Foundry SDK is
instrumented; installing the tracing plugin alone does not enable it. Native
`invoke_agent`/`chat` spans then use the same provider and journey root. A closed
export allowlist removes message/tool payloads, exception events and status
descriptions, including native SDK error bodies; error status codes remain.

Project attribution is mandatory at export. Set
`AZURE_AI_PROJECT_RESOURCE_ID` to the project's full ARM resource ID, not its
endpoint, name or internal ID. Bicep supplies `project.id` to both runtime
services. The exporter
overwrites both project-ID aliases from this trusted configuration, including
on native spans without an SDK-supplied ID. Missing or invalid configuration
fails explicitly; no local-runtime fallback is supported.

The project connection uses `ProjectManagedIdentity`, never an API
key. Application Insights local authentication stays disabled. Client export
uses the existing runtime publisher grant; do not grant the project publisher
access merely to enable server-side traces, which can capture conversation
content independently of the client's content-off settings.

In Foundry, select `gg-counsellor`, open **Traces**, and include the
new run's timestamp. The linked resource is `appi-gg-<token>`.
Application Insights Logs can independently verify project-scoped trace rows.
Use the exact project resource ID from deployment output in the portal's
mandatory selector; an empty visible filter list does not disable this scope:

```kusto
let projectId = "<AZURE_AI_PROJECT_RESOURCE_ID>";
requests
| union dependencies
| where timestamp > ago(1h)
| where customDimensions["microsoft.foundry.project.id"] == projectId
    or customDimensions["gen_ai.azure_ai_project.id"] == projectId
| summarize startTime=min(timestamp), spans=count(),
            nativeSpans=countif(tostring(customDimensions["gen_ai.operation.name"]) in ("invoke_agent", "chat"))
    by operation_Id
| where nativeSpans > 0
| order by startTime desc
```

Existing host spans are not proof that native spans exist. An empty trace
input/output panel is expected for native SDK spans: their content capture stays
off. Only the explicit synthetic host span above may contain messages. Historical runs
without native spans or project attribution are not backfilled. Acceptance
requires fresh project-selected operation rows containing native spans and
no payload fields, not merely ingestion into the shared Insights resource.

Acceptance of native trace visibility is established by running the in-network synthetic-channel proof
(`python -m scripts.run_dev_proof channel`, see the [runbook](../examples/pension_wtp/docs/demo-runbook.md)) and then checking,
under your authorized operator access, that:

- native agent and model spans are present in the linked Application Insights resource under a shared trace root, with no payload
  fields;
- the proof's journeys match Foundry's project selector and operation grouping, with the project resource attribution on every
  span, and the explanation root includes native `invoke_agent` and `chat` spans;
- both services run healthy revisions on the same immutable image.

This is ingestion acceptance and project-query eligibility. It is not an independent visual check of the Foundry portal, so
record separately whether you rendered it there.

## What the gateway does, and does not do

`/foundry/openai/v1/responses` is an authenticated **Responses front door**.
It allows the existing runtime identity only, forwards that caller's Entra
token and validates the already-approved pinned counsellor, intent,
understanding and answer-review agent versions. There is no direct unpinned
classifier exception. Member
tokens never enter this route. Host-selected model/agent metadata is retained.

The APIM token counter is bound to the host's journey header: **12,000 tokens
per minute and 120,000 per UTC day per conversation**, shared across draft/tool
rounds and classification. This is a fixed-window operational quota, not an
exact lifetime budget or an invoice. Native estimation/concurrency limitations
still apply. A gateway rejection follows existing fail-closed escalation;
no figures are estimated and no gate is skipped. A daily-quota refusal is marked
by the gateway (`X-GG-Limit: conversation-token-quota`) and escalates with
reason `conversation_limit` and its own member wording, not as an outage.

**Compatibility limitation:** [Foundry gateway documentation](https://learn.microsoft.com/azure/foundry/agents/how-to/ai-gateway#supported-configurations)
explicitly excludes the Responses API from its BYOM model-gateway integration.
The existing counsellor uses that API and a verified, pinned prompt agent.
The front door is not an inference-level backend pool. It does **not** claim
deployment load-balancing/failover, silently substitute the compact model,
rewrite agent references or replay a possibly accepted model request.
Changing this route to another supported agent architecture requires an
explicit decision while preserving version verification and all host gates.
Until a compatible integration is independently approved and verified, retain
the pinned front door; on model failure, use permitted checked text or human handover.

## Workbook and queries

The shared Azure Monitor workbook uses these exact queries:

- [Journey SLOs](../infra/queries/journey-slos.kql): acknowledgement dispatch
  p95 <= 1,000 ms; first checked substantive dispatch p95 <= 6,000 ms, both
  inclusive and n >= 20 for certification. Complete audio and guarded processing
  are diagnostics, not substantive-audio acceptance targets. Server dispatch
  is not an acoustic/browser playback measurement.
  The pre-existing `governedgraph.voice_turn.slo_met` processing flag is retained for
  compatibility, labelled as non-audio processing and never used by the workbook.
- Escalation rate and numeric-fidelity blocks, including a rejected draft that
  subsequently uses a checked template.
- Last served suspension IDs/snapshot and suspension operations from the
  existing operational log, not an invented global inventory count; governed uplift windows and kill-switch decisions
  from the existing aggregate evaluator, not a new inference from transcripts.
- [Observed resolved cost](../infra/queries/resolved-cost.kql): actual Responses
  input/cached/output usage, attributable to each agent and journey. Deduplicate
  by response/event ID, never by member utterance.

Resource tags identify the agent/service role. Journey attribution is an
operation dimension, **not** a per-journey ARM tag on a shared resource.
The workbook uses the existing workspace; it creates no role assignment,
new identity, signing-key access or new network route.

## Cost semantics

A successful state is not immediately a resolved-query denominator.
Resolution must have **fourteen complete days of subsequent observation**
and no subsequent contact by the same pseudonymous subject during that window.
New journeys and resumed resolved journeys both count as repeat contacts;
the fourteenth-day boundary is inclusive. This is outcome measurement only,
not phase/calendar gating of functionality.

The pure [reference implementation](../src/governedgraph/cost_reporting.py) requires a
coverage watermark and timezone-aware timestamps, rejects duplicate receipts
and mixed currencies, and leaves cost unknown when there are no receipts or
no qualifying resolutions. The numerator includes failed/unresolved journeys'
costs in the same start cohort. Missing usage is **not free**.

The workbook reports **observed model-token list cost only**, using the
verified regional rates of your own model deployments (record the source and verification date in your rate card).
No retail rate is applied before its verification date. Speech, Search,
embedding/indexing, agent-service and infrastructure charges are excluded.
Do not present this as a bill or full-stack cost. Reconcile telemetry ingestion
against durable journey history before using absence of a repeat for financial
reporting; no audited completeness watermark is inferred from sampled traces.
Full-stack allocation needs an existing authorized cost export/receipt source.
A full-stack method is Azure Cost Management filtered by
`azd-env-name=<your environment>`, divided by qualifying resolved queries for the same
reporting scope/window, with tag coverage/allocation verified. Use only existing
authorized cost access.

Until journeys have matured (fourteen days), both exact KQL queries return **zero mature observed resolutions**
and both unit-cost fields return null, not an assumed zero or a fabricated number.

## Hosted verification and remaining limitations

The in-network channel proof checks disclosure, S5 selection and S2 suspension, advice and injection escalation, deduplication
and durable response linkage. Verify that new starts carry the exact expected journey hash, that the same root joins the
orchestrator/tools, APIM and Strategy, and that complete and unknown model-usage receipts are present. The mature cost cohort is
empty at first; new receipts are never presented as fourteen-day outcomes.

P2 exports the same closed aggregate uplift signal through its existing console
diagnostics, not a new Insights publishing grant. All three entry points use the
shared content-free formatter. The paired native jobs retain their separate
identities and original four-hour schedules.
If a pipeline run meets a **pre-existing protective freeze** (publication returns 422), leave it frozen: observability changes
do not authorize Tier 2 recovery, and a consumed recovery decision is never replayed. Receipts from earlier runs are not proof
that a live service is currently unfrozen. A separate aggregate-only evaluation may report the existing
governed window without publishing, reconstructing a pipeline or releasing it.

Voice warm-up allocates a valid maintenance correlation ID shared by its
screen and versioned intent agent. It creates no member journey, reads no member values,
keeps every check and obeys the same gateway policy. Unattributed warm-up cost is
not part of the mature journey-cohort query; it remains another full-stack
overhead exclusion. Probe stdout is status/IDs only; obtain timing evidence
through existing authorized runtime diagnostics. Small smoke samples are not a
replacement for a twenty-turn voice window, and the workbook correctly does not certify n < 20.
Server-dispatch timings from a smoke are not acoustic or browser p95s. Processing
diagnostics do not replace the two-level voice targets. The workbook also displays
the labelled `synthetic-window` (FCR uplift, repeat
reduction and a `continue` decision); that is aggregate fixture evidence, not a claim of a new successful publication or outcomes.

**MODEL COST ONLY:** report cost only when deduplicated usage receipts are complete
and verified list rates apply. Unknown usage is not zero. A provisional resolution
is not fourteen-day mature. Speech, infrastructure and unattributed warm-up overhead
are excluded; the mature-cohort unit cost remains null until the cohort matures.
