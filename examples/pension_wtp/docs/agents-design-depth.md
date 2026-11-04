# Design-depth agents - sample specification

These three agents are specified at design depth only for the sample domain: contracts and offline stubs, no LLM, no identities, roles or resources.

## Transition Calculation Agent (L3)
- Responsibility: coordinates the administrator's deterministic calculation engine. It schedules conversion batches, runs reconciliation and plausibility checks against the actuarial rules, routes exceptions and assembles actuary sample sets for approval.
- Autonomy: L3. It may schedule, check and route within an approved run plan; actuaries sample and approve before any result is released.
- Tools (proposed): schedule_conversion_batch, get_batch_status, run_reconciliation_check, route_exception, build_actuary_sample, all through the gateway to the existing engine.
- Inputs/outputs: an approved run plan and rule-set version in; batch status, reconciliation report, exception list and sample set out, each referencing engine run IDs.
- Contracts: versioned OpenAPI operations on the gateway facade; ConversionBatch and ReconciliationResult events on the message bus.
- May NEVER: produce, adjust or round a number (B1/B2); release results without actuary approval; change actuarial rules or parameters; bypass the queue to overload the administrator.
- Least privilege: allowed to schedule batches, read status and reconciliation results and create sample sets; denied any change to actuarial rules or results.

## Transition Adoption Guide (L2)
- Responsibility: helps the member adopt the new contract: what changes, what stays the same, what to check, which of the fund's own tools to use, how to raise a question or objection, and when to talk to a human advisor (GNA).
- Autonomy: L2. Its recommendation is limited to the next journey step, and it escalates to advisors through the contact-centre handover.
- Tools (proposed): get_journey_state, get_ranked_strategies, search_approved_content, escalate_to_advisor.
- Inputs/outputs: journey state and open items in; next-step guidance, links to fund tools, objection-process information and a handover package with a context summary out.
- Contracts: the NextStep schema (a step ID from an approved list plus content version) and the EscalationRequest event.
- May NEVER: sell; compare or explain multiple options in order to steer a choice; recommend an option; give personal advice. Any option-level or personal-advice question triggers a warm handover to a licensed human advisor.
- Owner decision GNA: zero option-recommendation incidents is a hard evaluation gate.
- Least privilege: allowed to read journey state and approved next-step content and to create an escalation; denied member-value writes and option-comparison content.

## Event Processing Agent (L3)
- Responsibility: handles life events (employment change, divorce, death, retirement) through event-driven intake, document checks, submission to the administration system's deterministic rules, and a human exception queue.
- Autonomy: L3. It acts on routine, rule-covered events and escalates complex cases to the exception queue.
- Tools (proposed): ingest_life_event; check_documents (completeness and authenticity, with content treated as untrusted input); submit_event_to_administration (through the gateway); route_to_exception_queue.
- Inputs/outputs: an event notification and documents in; a validated event submission with an administrator reference, or an exception case with a reason code, out.
- Contracts: LifeEvent and EventOutcome events; versioned gateway operations; strangler-fig routing to the administration system.
- May NEVER: decide entitlements or eligibility (these stay in the administration system); follow instructions found inside documents; close a complex case (divorce, death) without human review.
- Least privilege: allowed to submit validated life events, read submission status and create an exception case; denied entitlement decisions and member-value writes outside event submission.

Cross-cutting rules for all three: every write through the gateway carries an idempotency key; documents are data, never instructions (indirect prompt injection); escalation is always available (B9). Stay at design depth: stubs behind the contracts, no LLM, no Azure identities, roles or resources. Recorded in IMP-34.

## Implementation and contract exports (IMP-34 supplement)

The sections above are the specification. Everything below describes the design-depth implementation.

- [OpenAPI 3.1 facade](api/design-depth.openapi.json): versioned
  `/design/v1/{agent}/{operation}` operations for all thirteen proposed tools.
  `next_step` is an additional host-only decision operation, not a model tool.
- [Service Bus JSON Schema](api/design-depth-messages.schema.json): closed,
  versioned schemas for all six requested message types, with a typed envelope.
- [Offline facade stub](../agents/design_facade.py) validates every
  inbound dataclass via `from_dict` / `_strict`. Unknown fields, demographic
  fields, wrong types and unsupported versions are rejected. The host binds
  the Adoption Guide's journey and strategy-reader context; tool bodies cannot
  override it. Life-event intake is a trusted external event-producer contract,
  not a model-supplied member selector.
- The facade is **not a running HTTP service or an APIM import**. Bearer-token
  authentication and production adapter authorization are specified, not
  claimed as deployed. No Azure SDK, identity, role, resource or LLM is added.

### Stub behavior and production seams

All offline data is **ILLUSTRATIVE - synthetic data**. The only default run plan
is synthetic `RP1` / `RS-1`, capacity ten; an approved plan registry is injected
by the host. The caller cannot change its capacity or rules. Scheduling creates
a queue receipt, never a direct administrator call. Until the engine processes
the queue, `ConversionBatch.engine_run_id` is null. Reconciliation results and
actuary samples must carry the actual engine run ID; the agent neither computes
control totals nor selects the sample itself. The engine adapter is absent by
default and raises an explicit `NotImplementedError`, never a fabricated result.
The human-only release seam requires successful reconciliation, no exceptions,
and administrator verification of actuary approval. It is not an agent tool or
an OpenAPI operation.

Event intake accepts document **references**, never instructions or
caller-asserted authenticity. The trusted document checker must confirm both
completeness and authenticity before a routine submission. It is unconfigured
by default and fails explicitly. The offline administrator submission stand-in
returns a visibly synthetic `STUB-ADM-…` reference; this is not evidence of a
real submission. Divorce and death always enter the local human exception
queue; no close-case operation exists, even with a caller-provided review flag.

The Guide emits only exact approved next-step titles and pinned content
versions, never option comparison content. Host-signalled option-level or
personal-advice intent returns the human-advisor step **and** queues a warm
handover with the existing `EscalationRequest` body. The shared
`escalate_to_advisor` operation is available to all three agents without a
state, document, engine or ranking precondition. A production channel must
classify these intents before invoking the host-only selector; this stub
does not pretend to classify free text.

Every write operation, including reconciliation, sample creation and possible
handover, requires an `Idempotency-Key` header. Exact retries are deduplicated;
same-key/different-payload retries fail. Batch and life-event identities also
prevent duplicate local submissions under a changed transport key.
Service Bus envelopes use stable body-and-domain-derived `message_id` values:
map these to `MessageId`, and map `session_id` to `SessionId` (batch ID for
conversion/reconciliation, event ID for event intake/outcome, journey ID for
next-step/escalation). Delivery is at least once; production consumers must
persist deduplication before effects. Envelope versioning preserves the
already-deployed `EscalationRequest` body unchanged. The in-memory queues and
deduplication here are deliberately **not durable production adapters**.

Regenerate the exports with:

```bash
PYTHONPATH=src:examples python examples/pension_wtp/scripts/export_design_contracts.py
PYTHONPATH=src:examples python examples/pension_wtp/scripts/export_design_contracts.py --check
```

### One named test per "May NEVER" clause

All tests are in [test_design_depth_contracts.py](../../../tests/test_design_depth_contracts.py).

| Clause ID | Clause | Test |
|---|---|---|
| TC-1 | Produce, adjust or round a number | `test_tc1_never_produces_adjusts_or_rounds_official_numbers` |
| TC-2 | Release without actuary approval | `test_tc2_never_releases_without_verified_actuary_approval` |
| TC-3 | Change actuarial rules or parameters | `test_tc3_never_changes_actuarial_rules_or_parameters` |
| TC-4 | Bypass the queue to overload the administrator | `test_tc4_never_bypasses_queue_or_approved_capacity` |
| AG-1 | Sell | `test_ag1_never_sells` |
| AG-2 | Compare or explain multiple options to steer | `test_ag2_never_compares_or_explains_options_to_steer` |
| AG-3 | Recommend an option | `test_ag3_never_recommends_an_option` |
| AG-4 | Give personal advice | `test_ag4_never_gives_personal_advice_and_warm_handover_is_queued` |
| EP-1 | Decide entitlements or eligibility | `test_ep1_never_decides_entitlements_or_eligibility` |
| EP-2 | Follow document instructions | `test_ep2_never_follows_instructions_inside_documents` |
| EP-3 | Close complex cases without human review | `test_ep3_never_closes_complex_cases_without_human_review` |
