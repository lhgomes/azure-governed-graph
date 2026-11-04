# Boundaries B1-B10 -> enforcement point -> test

## Fail-closed deployment verification (IMP-84)

- **B8/B9:** `scripts/run_dev_proof.py` resolves the public APIM journey output to the unique tagged private orchestrator
  in the saved deployment scope, retaining the existing in-network dev-issuer identity and endpoint allowlist.
  `scripts/deploy.py` requires a successful channel-proof execution before completing `verify` or reporting deployment
  completion. Failures/timeouts remain pending; legacy completion without proof evidence is reverified.
  Tests: `test_azd_env.py` (endpoint resolution, scope, shared readiness validation, rejected ambiguous/unsafe endpoints)
  and `test_deploy.py` (proof launch failures, unsuccessful statuses, timeouts, legacy checkpoints and reverification).
  `test_channel_probe.py` also covers bounded retries for the documented journey-busy HTTP 409 contract, preserving
  the member authorization, request body and idempotency key. Persistent or unrelated errors still fail;
  deduplicated delivery, exactly one handover and complete response-to-audit linkage remain mandatory.
  **B10:** hosted voice reports the already-selected arm and strategy as proof metadata, never as persona inputs.
  `test_hosted_voice.py` covers audited numeric fidelity for both control S1 and treatment S5.
  `test_voice_live.py` verifies bounded observation of valid controls before requiring treatment S5; failed control
  gates, unknown arms, treatment failures and exhaustion remain fatal. Experiment assignment is unchanged.

## Model gateway deployment bootstrap (IMP-82)

- **B3/B8:** `infra/modules/model-gateway.bicep` omits empty APIM Named Values and selects the unconditional HTTP 503 policy
  in `infra/policies/model-unavailable.xml` unless all five approved agent-version pins are present. This also applies to an
  incomplete redeploy, so an old active policy cannot remain in use. The Responses operation depends on policy installation.
  Once configured, the existing
  `model-responses.xml` caller, immutable-version, quota and protective writer-hold checks remain unchanged.
  Tests: `test_observability_cost.py` (`test_b3_gateway_omits_empty_named_values_and_requires_every_agent_pin`,
  `test_b3_b8_gateway_uses_deny_policy_until_all_pins_exist_including_on_redeploy`,
  `test_b3_gateway_bootstrap_explicitly_fails_and_never_forwards_to_a_model`).

## Environment-derived operations (IMP-74)

- **B4/B8:** the explainability reconciliation's legacy exception is no longer a hard-coded timestamp. It applies only to
  `gg-dev` and only when `GOVERNEDGRAPH_LEGACY_CUTOVER_UTC` is set to a timezone-aware instant; a fresh deployment sets none,
  so every delivery must be linked. Tests: `test_explainability.py` (cutover separates legacy only when configured; production
  and unknown environments never get an exception; invalid or naive cutovers are rejected).
- **B8/B9:** `scripts/azd_env.py` reads deployment values from the azd environment and refuses to export a missing or malformed
  value; `scripts/run_dev_proof.py` starts a proof only in `gg-dev`, only from an immutable digest in the deployment's own
  registry, as an execution-only override that never changes the stored job. Tests: `test_azd_env.py`.

## Framework/sample layering (IMP-72, IMP-73)

- **B1/B7:** the framework core obtains official values and the domain pack only through configuration
  (`GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER`, `GOVERNEDGRAPH_DATA_DIR`) and never imports the bundled sample.
  Paths of the sample's values API and data moved to `examples/pension_wtp`; boundary behaviour is unchanged.
  Test: `test_layering.py`; the B7 image-contents test `test_production_image_does_not_embed_cohorts_or_personas`
  now checks the relocated `COPY examples/pension_wtp/data/...` lines.
- **B8/B9:** prompts, closed vocabularies, fixed member-facing messages and domain advice patterns load from the pack
  through `common/domain.py`, which fails closed on a malformed pack. The routes that protect B9 (advice/option-level,
  human, comprehension, clarification) are framework-owned in `common/contracts.py`
  (`FRAMEWORK_INTENT_DECISIONS`), so a pack cannot remove an escalation route. The generic advice patterns remain a
  core floor in `guardrails/advice_intent.py`. Tests: `test_domain_pack.py` (no sample wording in the core, malformed
  pack rejected, framework routes retained).
- **B8:** human recovery from a protective freeze stays delegated and operator-restricted, now through deployment
  configuration instead of pinned IDs: `scripts/configure_strategy_recovery.py` refuses any API or operator other than
  `STRATEGY_API_CLIENT_ID`/`STRATEGY_RECOVERY_OPERATOR_ID` and any environment other than `gg-dev`;
  `scripts/lift_dev_freeze.py` validates the gateway endpoint shape. Tests: `test_strategy_recovery.py`,
  `test_recovery_decisions.py`.
  Deployment now invokes the restricted setup in its resumable `recovery-access` step (IMP-97), validates the reported
  API/operator/scope and preserves assignment-required. Targeted access repair neither starts pending learning nor
  lifts a freeze. Setup denial or inconsistent results cannot mark the step complete (`test_deploy.py`).
  API re-registration preserves the existing delegated API configuration and only the configured dev operator's
  user-typed Default Access grant, without creating human access; foreign or nonhuman grants remain rejected
  (`test_strategy_azure.py`). Human token acquisition retries only the exact assignment-required error within a
  maximum of five minutes of retry waits, with no recovery HTTP request on authentication failure
  (`test_recovery_decisions.py`, IMP-98).

## Automatic synthetic model comparison (IMP-68)

- **B1/B2/B3/B6/B7/B9/GNA:** `evals/run_model_suite.py` preflights exact
  model/agent definitions and runs the existing guarded replay with common
  pinned decision agents. Repair stays on the candidate under test; unknown
  configuration fields, missing cases/candidates and deterministic hard-gate
  failures cannot become successful comparisons. Tests in
  `test_model_suite.py` cover preflight drift, missing models, complete paired
  evidence, supporting-model usage and same-candidate composer injection.
- **B4/B5/B8:** result analysis is offline evidence only. Lower price or
  perfect judge scores cannot override hard floors, and every result explicitly
  disables automatic promotion. No conversation-to-behavior path is added.
  Tests cover corrupted analysis, missing usage, zero resolutions, filtered
  actors, fallback-only output and quality/latency/coverage regressions.
  The original frozen comparison and B10's runtime control/uplift gates remain
  unchanged; this experiment is neither voice certification nor human approval.

## Host-owned approved answer structure (IMP-60/63)

- **B1/B2/B3/B7/GNA:** the native answer schema requires bounded explanation parts,
  source IDs and a generated meaning-check question. `_assemble_answer`
  includes the question only for the served strategy's teach-back setting;
  the runtime `AnswerDraft` contract remains `text`/`content_ids`.
  S5's step-by-step organization uses host-rendered English/Dutch labels without
  adding a fact or a figure. The generated meaning question stays model-authored.
  The assembled answer passes the same independent meaning judge and final
  gates. A question marker is a necessary format check, not semantic acceptance.
  Tests: `test_b3_host_applies_teach_back_structure_to_generated_components`,
  `test_b2_b7_writer_definition_enforces_the_closed_answer_envelope`,
  `test_b2_dq10_generated_question_still_passes_final_safety_gates`,
  and the bounded single-repair/audit regressions.

## Strategy cache reads without expected SDK errors (IMP-59)

- **B3/B6/B8:** `BlobStateStore.read` checks the current state ETag on every
  cached read without generating an HTTP 304 exception. Changed state still
  downloads and verifies immutable snapshots; its own downloaded ETag wins
  over preceding metadata. Real failures remain errors, never cache hits.
  Tests: `test_b3_blob_store_retains_explicit_verified_last_good_and_conditional_state`,
  `test_b3_blob_version_check_failure_is_not_an_unchanged_state`,
  `test_b3_blob_download_returns_its_own_version_after_a_metadata_read_race`,
  `test_b6_blob_version_check_observes_new_suspensions_for_every_member`,
  and `test_blob_read_does_not_treat_unrequested_304_or_412_as_a_cache_hit`.

## Question-specific scope (IMP-58)

- **B1/B2/B3/B7:** `IntentDecision.reason` preserves a closed semantic focus
  without adding a strategy key, demographic field or open-text classifier.
  `task_context` carries static host-owned goals/excluded concept IDs into the
  writer, repair and judge. Amount-reference context is withheld when out of
  scope. A reported excluded concept vetoes relevance even if the native
  checklist claims a pass. Existing numeric, advice, version and audit gates
  remain mandatory. Historical generic routing evidence remains parseable.
  Tests: `test_b3_b7_question_focus_preserves_old_audits_without_generic_new_native_outputs`,
  `test_b3_b7_question_focus_reaches_writer_judge_and_repair_and_vetoes_incidental_provenance`,
  `test_b1_b2_b7_question_focus_limits_reference_context_without_changing_strategy_key`,
  and `test_b1_b3_explicit_source_question_keeps_relevant_provenance`.
  `test_b1_b2_overview_can_explain_sourced_future_uncertainty_without_an_official_value`
  distinguishes source-supported future adjustments from an official value or
  an unauthorized member-figure comparison.
  The native schema returns only a closed classification reason and language;
  the host derives route/intent from the contract table, preventing independently
  valid fields from making an invalid tuple. Every code is covered by
  `test_b3_b7_native_classification_derives_only_the_approved_matching_route`.
  `test_b4_closed_validation_diagnostics_survive_without_exporting_raw_decisions`
  verifies that Foundry keeps safe failure metadata, not arbitrary decisions,
  private text or exception bodies.

## Independent grounded answer judging (IMP-56/57)

- **Sample verification ownership (IMP-87):** `examples/pension_wtp/deployment.json` declares the sample's local
  tests/evaluation/rehearsal and both deployed channel modes. Pension cases and acceptance logic live under
  `examples/pension_wtp/evaluation/`, with sample coverage tests under `examples/pension_wtp/tests/`.
  The main deployment command runs these checks automatically; framework boundary tests remain unchanged.
  Local failures stop fresh provisioning, every declared live proof must succeed, and input fingerprints invalidate
  stale receipts. `tests/test_sample_deployment.py` checks closed/path-confined definitions;
  `tests/test_deploy.py` checks local failure, voice failure, immutable proof images and legacy receipt migration.
  These tests do not authorize new writer definitions or claim physical-audio acceptance.
  The learning plane is part of the same command (IMP-88): immutable P2/fixture images, container-scoped
  artifact grants, one complete P2 cycle and the protective fairness-agent proof run before the schedule is enabled
  (B4/B5/B6/B8/B10 unchanged; B4 reads aggregates only). Tests: `test_deploy.py::test_b4_b5_b6_*`,
  `test_b5_b6_*`, `test_b6_b9_*`, `test_b7_b8_only_the_workspace_artifact_containers_*` and
  `tests/test_p2_fairness_agent_proof.py`.

- **B1/B2/B3/B8/B9:** whole-conversation hosted acceptance exposed a false judge
  pass on an unsupported opt-out claim. The protective APIM writer suspension
  returns explicit 503 for unapproved counsellor writers only; exact approved
  version/caller checks remain in front of it. Existing technical handover and
  audited human assistance remain available. Test:
  `test_observability_cost.py::test_b1_b2_b8_generated_answers_stay_suspended_after_failed_hosted_acceptance`.
  `test_b8_only_exact_project_scoped_human_approval_can_leave_the_protective_writer_hold`
  additionally checks the exact project/name/version/human-decision predicate,
  with empty approval denied and no universal version exception (IMP-86).
  The release decision is the sample-owned, human-reviewed
  `examples/pension_wtp/release-approval.json` (IMP-88/90): closed schema and default-deny on missing/stale approval,
  with the owner's actual initial synthetic release authorization recorded once as `HR-PENSION-WTP-INITIAL`.
  This is not a board decision or permission to auto-approve future definitions. `GB-`-only reinstatement/recovery
  remains separate and unchanged.
  bound to the SHA-256 of both current writer definitions and no version number.
  `scripts/deploy.py` stops before provisioning unless it is approved and covers the
  current definitions, then `scripts/writer_approval.py` verifies the actual immutable
  Foundry definitions and binds the approval to the new project and pins. Tests:
  `test_deploy.py::test_b8_*` (binding, unapproved/stale stop, revocation, failed live
  check, closed schema, shipped file default-deny) and `tests/test_writer_approval.py`
  (arbitrary version numbers, changed definitions, stale pins/projects, denied reads,
  unknown/demographic fields). Agent creation and bounded Tier-1 learning never issue
  writer approval; the deployment script is the trusted conduit for the human decision
  and is not a board-authorization service. Repinning alone cannot revive a rejected
  writer. The original suspension test is unchanged.
  This hold cannot be presented as a completed conversation fix. IMP-57 stages
  only verified successor definitions for hosted acceptance; it does not
  remove the final numeric, advice, audit or version gates.
- **B1/B2/B3/B7/GNA:** `Counsellor._explain` validates the initial
  draft before `FoundryDecisionAgent.judge` receives unbound placeholders,
  referenced official labels, cited authorized bodies and trusted task metadata.
  Both writers enforce strict native answer components; the host applies the
  approved style before producing `text`/`content_ids`. Startup compares the
  complete pinned definition. The separately pinned judge returns individual
  checks, evidence sufficiency and concept codes. The host derives verdict and
  unique issues while retaining every failed check and feedback veto. Transient feedback contains only
  closed issue codes and exact quotes from the current draft. Unknown fields
  fail closed; unmatched quotes are explicitly discarded while their closed
  issue still vetoes acceptance. Quotes never enter stored review evidence,
  taught-concept context or learning. `FoundryComposer.revise` permits one
  tool-free repair using any already-screened authorized source and official
  placeholder, never a new source or generated figure.
  One verification judgment and final gates precede exactly one audit commit.
  The information-gap task states the unanswered point directly and offers
  specific advisor confirmation without claiming a completed handover; it does
  not instruct the writer to narrate missing references (IMP-64).
  Tests: grounded judge inputs, bounded revision, negative output gates and audit
  ordering in `test_foundry_counsellor.py`; strict judge schema and bounded audit
  evidence in `test_decision_agents.py`.
  Native envelope and feedback isolation tests:
  `test_b2_b7_writer_definition_enforces_the_closed_answer_envelope`,
  `test_b4_b7_review_feedback_cannot_introduce_new_text_or_fields`, and
  `test_b2_b3_rejected_claim_is_replaced_using_authorized_evidence_before_release`.
  `test_b3_serial_and_parallel_retrieval_do_not_inject_a_payment_topic` verifies
  both retrieval paths use the actual question and the bounded five-hit budget;
  the existing retired-passage replacement test is unchanged.
  `test_b3_answering_question_cannot_hide_irrelevant_detail` prevents main-topic
  coverage from overriding the separate extra-detail relevance veto.
  `test_b3_b4_all_rejected_clauses_with_the_same_issue_reach_the_single_repair`
  verifies that repeated issue codes do not discard distinct defects or leak
  raw feedback into durable evidence. Feedback is bounded to sixteen quotes.
  The evidence-first schema-order experiment regressed hosted behavior and was
  rolled back; it is not an enforcement point or a claimed quality fix.
- **B4/B7/B8/B9:** offline/fallback artifacts, learning inputs and human
  escalation remain unchanged. Review failure/non-pass is explicit; there is no
  post-validation string substitution. Closed voice metadata admits only the
  new named numeric timing stage; `test_voice_live.py` still rejects unknown
  fields and demographic timing keys. `ExplanationContext` contains only fixed
  concept IDs; teach-back targets must be a subset of taught concepts. Closed
  `en`/`nl` language is supplied by the semantic intent agent, never demographics.
  Durable restart and no-raw-text tests are in `test_durable_orchestrator.py`.
  Historical explainability 1.1/1.2 remains byte-exact; new 1.3 bounds the two
  judge attempts without permitting duplicate routing decisions.
  The gateway retains exact agent/caller
  pins and the 120,000-token daily quota (IMP-66, raised from 32,000, which cut conversations
  after about four turns); its 12,000-token minute burst remains unchanged
  (`test_observability_cost.py`). Quota refusals escalate as `conversation_limit`
  (B9), not as an outage. Worst-case hosted repair
  usage still requires measurement; no extra quota is silently granted.
- These tests establish safety/protocol boundaries, not model style
  compliance. IMP-55's scripted live checks passed, but operator inspection
  still found language switching and capability boilerplate; overall semantic
  acceptance failed. See the decision log and runbook before presenting it
  as a completed conversation fix.

## Question relevance without spoken source boilerplate (IMP-54)

- **B1/B2/B3/B7:** `FoundryComposer.draft` separates trusted developer guidance,
  quoted reference context and the final member question. Normal composition
  receives the selected approved strategy's name/version/teach-back flag and
  template reference, not canned current/previous fallback text. Member text and
  retrieved passages never enter developer instructions. Source provenance stays
  in the explainability record; explicit source questions remain answerable.
- **B2/B3/B8:** approved template artifacts and `_post_process` safety fallback
  are unchanged. No post-validation text stripping, new strategy, broader factual
  source or conversation-to-learning path. Tests:
  `test_b1_b2_source_provenance_is_recorded_without_routine_spoken_disclaimers`
  and existing output-gate tests in `test_foundry_counsellor.py`.
- These tests verify instruction wiring and existing safety gates, not hosted
  style compliance. IMP-54's live wording acceptance remains failed; the recorded
  outcome is in the maintainers' local decision log.

## Question-specific composition and dependency lifecycle (IMP-52)

- **B1/B2/B3/B7/GNA:** `AnswerDraft.from_dict` is closed; the composer uses
  reference-only approved session context. `_post_process` validates content IDs
  against screened retrieval and value IDs against authenticated official values.
  Figure-free replies require a retrieved citation; unused financial placeholders
  are not compulsory. Literal figures, unknown/malformed placeholders, unsupported
  sources and steering still fail closed. Existing B2/GNA negative assertions
  remain intact. Tests: `test_foundry_counsellor.py`.
- **B4/B7/B9:** Blob initialization is conditional and race-safe, with exact
  conflict handling and unchanged lease fencing. Sender recycling and bounded
  recovery preserve message IDs and deduplication. Additional dependency
  diagnostics accept only closed queue/cause/condition metadata, never exception
  bodies; terminal and native SDK errors remain errors. No conversation-to-learning
  path is introduced. Tests: `test_dependency_lifecycle.py`,
  `test_durable_orchestrator.py`, `test_explainability.py`,
  `test_voice_latency.py`, `test_synthetic_tracing.py`, `test_strategy_azure.py`.

## Contextual decision agents and synthetic observability (IMP-51)

- **B1/B2/B3/B6/GNA:** `agents/decision_agents.py` defines separate immutable
  intent/understanding roles, with strict versioned schemas and no tools.
  `orchestrator/workflow.py` uses closed contracts for explicit explanation,
  clarification, understanding and handover branches. The old runtime regex
  intent/understanding functions and direct boolean model call are removed.
  Authorization, verified rankings, binding, fidelity and steering remain
  independent host gates. APIM rejects all unpinned/direct-model calls and
  inline model/instruction/tool/format overrides.
  Tests: `test_decision_agents.py`, `test_framework_workflow.py`,
  `test_foundry_counsellor.py`; existing invariant suites remain mandatory.
- **B4/B5/B7:** explainability 1.2 records closed role/version/decision evidence,
  without a free-text rationale or demographic field. Response-linked events
  retain the existing aggregate-only learning path. New questions and ambiguous
  understanding do not manufacture failed/successful comprehension. Legacy
  audit outbox serialization stays byte-exact.
  Tests: `test_decision_agents.py`, `test_learning_feedback.py`.
- **B4/B7:** `foundry_tracing.py` keeps default/production and all SDK content
  capture off. Explicit gg-dev presenter capture is allowlisted by synthetic
  identity and emits only host user/checked-assistant messages after audit and
  durable delivery. Credential-like/oversized text is withheld; tools, drafts,
  exception bodies and arbitrary attributes remain excluded. Safe dependency
  status/type and expected/recovered annotations do not falsify error status.
  `observability.monitor_export_options` bounds both exporters' transport and
  enables one-hour local retry buffering. Trace sanitization precedes storage;
  SDK body/message capture stays off. Console review diagnostics retain only
  closed verdict/issue codes, never rejected quotes. Local buffering is not
  durable across replica replacement and cannot prove ingestion by itself.
  Tests: `test_synthetic_tracing.py`, unchanged default-protection assertions in
  `test_observability_cost.py`.
- **B9/Art. 50:** the canonical opening and human control are preserved.
  Resolved answers do not disable chat or stop voice capture; normal channel end
  is distinct from human handover and from domain resolution. Tests:
  `test_presenter_redesign.py`, `test_voice_presenter.py`.

## Opening once, explanations without routine handover (IMP-50)

- **Art. 50/B9:** the canonical opening is unchanged and precedes member input.
  Counsellor instructions identify subsequent turns, without repeating the
  introduction/scope or routinely advertising handover. The host no longer
  appends T-S6 to successful explanations or offers handover after successful
  teach-back. Requested and mandatory escalations, S6 availability and the
  presenter handover control remain unchanged. Tests:
  `test_counsellor_local.py`, `test_foundry_counsellor.py`; existing framework
  and presenter escalation tests remain intact.
- **B3/B9:** a follow-up after resolution re-enters the existing in-scope
  explanation transition after safety and contextual intent-agent checks;
  parallel voice lookups follow that turn's approved intent. Regression tests cover
  chat continuation and a new voice explanation after successful teach-back,
  rather than accidental `InvalidTransition` handover.

## Explicit chat failure and stable delivery recovery (IMP-49)

- **B1/B2/B3/GNA:** the IMP-49 payment intent coverage is now provided by the
  contextual versioned intent agent (IMP-51), not `classify_intent`. The same
  approved transition intent and independent checked-value/steering gates
  remain. Tests: `test_decision_agents.py`, `test_presenter_redesign.py`.
- **B4/B7/B9:** `ServiceBusJourneyPublisher` retries one connection failure
  using the same message/IDs and existing lock; other/repeated failures
  propagate, preserving outbox/deduplication semantics. No raw conversation
  enters learning. Tests: `test_voice_latency.py`.
- **B9:** the presenter shows inline upstream/socket/deadline errors, clears
  pending state and explicitly warns the message may already be saved.
  It does not silently resubmit, fabricate answers or hide repeated failure.
  Tests: `test_presenter_redesign.py`, including safe WebSocket 503 handling.

## Framework, outcome feedback and voice-first presenter (IMP-43/44/45)

- **B1/B2/B3/B6/B8/B9/GNA:** `orchestrator/workflow.py` runs actual Agent
  Framework executors around the existing approved-tool, binding, fidelity,
  steering, suspension and audit barriers. Early advice/human requests skip
  generation; failed teach-back uses the explanation executor.
  `test_framework_workflow.py` verifies routing, exactly one checked output,
  preserved context under async callers and unchanged financial gates.
- **B4/B5/B7:** `learning/feedback.py` joins committed response-linked audits
  to reference-only events, deduplicates exposures, excludes withheld
  responses and waits for completed repeat-contact observations. Only explicit
  successful teach-back earns observed-resolution credit. The closed
  `FeedbackManifest.from_dict` rejects unknown, demographic and transcript
  fields. `learning/feedback_store.py` persists immutable count-only CSVs and
  separately identifies baseline and observed evidence. No utterance/model/
  prompt/cohort data enters ranking. Tests: `test_learning_feedback.py`.
- **B3/B4/B5/B6/B8/B10:** `learning/run_cycle.py` executes the framework
  aggregate/rank/veto/sign-govern-publish graph without changing bounds,
  approval rules or the separate uplift kill switch. Azure `BlobLearningInputs`
  and `scheduled_jobs.run_pipeline` pin the fairness-reviewed exact input
  digest. `test_feedback_blob.py` verifies newer live pointers cannot replace
  active inputs, stale producers fail CAS, and corruption fails closed.
  End-to-end feedback tests verify the signed bounded/vetoed snapshot is
  served to the next journey. Existing B10 window inputs are not relabelled as
  live outcome evidence.
- **B7/B9/Art. 50:** `presenter.ChatMessage` is closed and bounded; authenticated
  chat uses the same member scope and approved disclosure, without Speech.
  The presenter disables repetitive spoken acknowledgement only on its own
  bridge path; existing standalone defaults and latency thresholds remain.
  CSP, synthetic watermark, human handover and server-only tokens remain.
  Tests: `test_presenter_redesign.py` plus existing voice/presenter/latency suites.
  IMP-46 additionally pins the exact human-approved natural opening, approval
  reference and template-set version 1.2 in `test_framework_workflow.py`; explicit
  AI disclosure, no advice and human availability remain in the canonical text.

## Single-use human recovery decisions (IMP-39)

IMP-40 additionally preserves the exact approved audit-image gate. Bicep binds
the jobs and Strategy monitor to `p2ContainerImage`; scoped image rollouts must
align all deployed pins too. The native audit regression in
`test_strategy_recovery.py` rejects both stale monitor and mismatched execution
pins, accepting only the aligned approved image. Fixing configuration drift
does not release the freeze or consume a human decision.

`recovery.validate_decision_ref` accepts only `GB-DEV-YYYY-NNN`.
Reference-specific GET readiness and every POST scan the existing durable
requested/completed journal, including historical UUID-named records.
`BlobRecoveryJournal` claims each new decision with a deterministic create-only
intent, rejecting concurrent reuse before any state mutation; unreadable history
fails closed. No record is removed and a persisted failed attempt still consumes
its reference. The CLI's separate context/format/reuse errors never print a
bearer or URL credentials/query/fragment contents. Existing gg-dev, delegated
CLI human/tenant, latest successful audit, exact image/command, conditional
state revision and refreeze-on-failure gates are unchanged.
`tests/test_recovery_decisions.py` covers new and legacy decisions, malformed
references, every context check, client/server refusal, stale-list races and
missing/corrupt history; existing recovery assertions remain intact (B6/B8).

## Offline rehearsal safety (IMP-38)

`scripts/reset_demo.sh` always selects local mode. `demo/run_demo.py` archives
prior local evidence rather than deleting it, runs one normal governed P2 cycle,
rebuilds approved retrieval and warms local checks only. Offline presenter
input enters the same counsellor gates after disclosure; advice, injection,
human requests and interruption do not become fabricated successful demos.
`tests/test_demo_readiness.py` blocks network connections and verifies evidence
retention, unchanged non-demo freeze state, S5/S2, fidelity, resolved teach-back,
explicit warm-up failure and refusal/escalation. The local recording player has
no external dependencies. No Azure freeze is released and no live acceptance
is inferred from local results (B1-B10, Art. 50).

## Observability and cost (IMP-37)

`observability.py` emits a closed allowlist of operational metadata, shares the
existing deterministic journey trace root and never exports member text or
values. IMP-41 enables the native Foundry SDK tracing bridge;
`foundry_tracing.ContentFreeTraceExporter` independently allowlists exported
span attributes and strips exception events/status descriptions, preserving
error codes without raw SDK error bodies. `test_observability_cost.py` verifies
that boundary and keyless project connection; `test_foundry_counsellor.py`
verifies explicit bridge selection and content/baggage-off instrumentation.
IMP-42 stamps both Foundry project-ID aliases from the trusted deployment ARM
ID, never from span/model input. Tests cover absent/correct/stale SDK
attribution, rejection of missing/non-ARM configuration and shared runtime
deployment wiring; the payload allowlist and demographic exclusion remain.
No Foundry server-side publishing grant is added. APIM Responses ingress preserves pinned agent references and caller
identity, limits each conversation and captures zero body bytes. Model-level
gateway failover is not silently substituted for the unsupported Responses
BYOM integration. `cost_reporting.py` requires mature fourteen-day outcome
coverage and sourced receipts; no data means unknown, not free.
`test_observability_cost.py` tests privacy rejection, trace correlation, cached
usage, incomplete receipts, exact repeat-window edges, outcome denominators,
gateway identity/pins and the accepted two-level voice SLOs.
`test_new_journey_disclosure_event_uses_its_own_trace_root` also protects the
creation span. The hosted synthetic proof verifies the exact hash for four
new journeys and shared APIM/orchestrator/Strategy roots; its result and
unchanged identity/grant evidence are recorded in IMP-37.
Existing B1-B10/numeric/refusal/demographic gates remain unchanged; neither
telemetry nor cost reports update prompts, catalogue, priors or routing.

## Evaluation foundation (IMP-36)

`governedgraph/evaluators.py` independently reuses the production numeric provenance
checker and adds served/non-suspended-strategy and required-escalation floors.
The existing three CI hard gates remain mandatory; failed evaluator floors
also return nonzero. `tests/test_evaluators.py` proves each failure path and
that all persona cases omit profile/demographic metadata.
The explicit all-persona probe retains an unmet English explanation expectation
as an exit-1 coverage gap; it never invents a ranking, loosens a gate or changes
behaviour from its results. This is SIMULATED evaluation, not a training input,
a calibrated quality result or production acceptance (B2/B3/B4/B6/B7/B9).

`governedgraph/foundry_evaluation.py` now scores already-guarded outputs only:
groundedness v19 receives approved retrieval plus official deterministic display
context; style/refusal rubrics cannot override `governedgraph/evaluators.py`.
The pinned tool-free Foundry persona accepts its own fictional scenario and
member-visible text only, not host objects or counsellor prompts.
`tests/test_evaluation_pipeline.py` checks closed actor input, definition drift,
filtered/invalid actor output, exact human-label hashes, missing judge/usage
evidence and retained comparison integrity. CI also recomputes all 34 frozen
candidate rows against sourced rates and requires numeric/refusal/strategy and
demographic hard gates. The user accepted prompt 12 with genuine human
calibration explicitly deferred as an open item in IMP-36. Coverage failures
remain visible; safe English escalation and filtered actor attempts are
not relabelled as successful explanations/generation. No production prompt,
catalogue, ranking or model is updated from evaluation results (B4/B5/B8).

## Governed Voice Live channel (IMP-35)

- **Latency correction, no weaker exit:** template set 1.1 adds only the
  explicitly approved figure-free acknowledgement. `VoiceBridge` validates and
  caches it before input; it is not counted as a substantive pension answer.
  `_parallel_preflight` joins all screen/classifier/read futures before drafting,
  retains step-up/member scoping and document screening, and fails closed for
  every failure domain. Complete `_post_process` plus audit still precede
  substantive synthesis. `CheckedSpeechPool` verifies whole sentence audio and
  preserves text/order; browser scheduling never overlaps sentences.
  Closed timing fields reject demographic/invalid metadata. Cold benchmark
  caches are isolated from normal sessions. Tests:
  [test_voice_latency.py](../tests/test_voice_latency.py).
  The existing member-values tests change only mocked transport targets to the
  reusable HTTP client; no assertions or boundary tests are removed/weakened.
- **B1/B2:** `voice_live_bridge.OrchestratorChannel` uses only the existing
  authenticated chat HTTP operations. `azure_voice.VoiceLiveSpeech` requests
  pre-generated speech, never model text inference; unbound placeholders,
  changed returned text, function calls and unrelated audio are rejected before
  playback. `test_voice_b2_*` verifies the exact GA payload and fail-closed cases.
- **Art. 50:** `VoiceBridge.start` completes disclosure playback before
  input is enabled. `test_art50_disclosure_*` and `test_voice_audio_failure_*`.
- **B3/B6/B10:** strategy selection, S2 veto and control assignment stay in the
  same orchestrator/Strategy Service; voice cannot force treatment or clear
  suspensions. HTTP parity tests verify S5/S2 without a second strategy path.
- **B4/B7:** no audio/transcript/token files or learning exports; completed
  Speech conversation items are deleted. Closed reply contracts reject unknown
  fields. The persona sends utterances only. `test_voice_b7_*`,
  `test_voice_stt_*`, `test_voice_simulator_*` and `test_voice_text_fallback_*`.
- **B8/B9 / GNA:** no new promotion/reinstatement tools. Advice and injection
  follow existing human gates; audio failure requests and verifies handover via
  the same authenticated endpoint. Duplicate acknowledgments are never spoken
  as fabricated answers. `test_voice_b9_*`, `test_voice_audio_failure_*`,
  `test_voice_b4_*` and `test_voice_b1_*`.
- **Identity / evidence:** `voice.probe` refuses local/developer/P2 proof hosts.
  The issuer only mints and forwards the in-memory member bearer; it never
  connects to Speech. `voice.hosted` runs under the runtime identity and calls
  the same authenticated HTTP journey APIs. Existing AI-account roles suffice;
  no issuer AI role is added. `test_voice_live_proof_*` and
  `test_runtime_identity_*` prove the split.
- **Hosted boundary:** the synthetic endpoint is dev-only, member-scoped and
  step-up protected. Closed script fields cannot carry a profile or endpoint
  override. Error diagnostics contain code locations, never exception bodies.
  Protocol timing tests prove the formula only; actual live evidence is in IMP-35.
- Tests: [test_voice_live.py](../tests/test_voice_live.py) and
  [test_hosted_voice.py](../tests/test_hosted_voice.py).
- **Approved browser channel:** `voice/access.py`, `voice/presenter.py`,
  `dev/member_issuer_api.py` and the two voice-demo APIM policies enforce
  dev-only opt-in, exact operator/client/role/scope separation, exact Origin and
  runtime-only issuer invocation. Closed session/script contracts retain B7;
  bearer and audio stay in session memory and B9 cancellation requests handover.
  Browser cookies contain only delegated Entra authorization, never member
  tokens. Microphone mode is not recorded; the explicitly requested synthetic
  fallback video is watermarked and has no learning export (B4).
  [test_voice_presenter.py](../tests/test_voice_presenter.py) covers these
  enforcement points, bounded media/acknowledgments and first/complete metrics.

## Design-depth agent contracts (IMP-34 supplement, no deployment)

Authoritative source and the complete eleven-clause test mapping:
[agents-design-depth.md](../examples/pension_wtp/docs/agents-design-depth.md#one-named-test-per-may-never-clause).
Versioned [OpenAPI](../examples/pension_wtp/docs/api/design-depth.openapi.json) and
[Service Bus schemas](../examples/pension_wtp/docs/api/design-depth-messages.schema.json) are design artifacts,
not a claim that new operations have been deployed.

- **B1/B2:** `TransitionCalculationAgent` accepts only an approved plan and
  queues conversions. Reconciliation and samples are engine results with run
  IDs; closed contracts have no official amount, adjustment or rounding field.
  `EventProcessingAgent` has no entitlement/eligibility/value-write operation.
  Tests `test_tc1_*`, `test_tc3_*`, `test_tc4_*` and `test_ep1_*` verify these limits.
- **B3 / GNA:** `NextStep` validates exact approved step/title/content/version
  combinations. No selling, comparison or option-recommendation tools exist.
  Option/advice flags queue a warm handover, not just an advisor link.
  Tests `test_ag1_*` through `test_ag4_*` cover every Guide prohibition.
- **B4/B7:** `DesignDepthFacade` parses every inbound contract via `_strict`;
  the host binds Guide journey and strategy access. Closed message schemas
  reject demographic extensions. Documents are references passed to a trusted
  checker, never executable instructions. No conversation-to-learning path
  is added. Tests `test_b7_*`, `test_ep2_*`, closed-contract and export tests.
- **B8:** release requires reconciled, plausible, exception-free results and
  administrator verification of actuary approval; release and rule editing
  are absent from the agent tool catalogue. No complex-case close operation
  exists. Tests `test_tc2_*`, `test_failed_reconciliation_*`, `test_ep3_*`.
- **B9:** shared `escalate_to_advisor` is callable in every local state,
  without a working engine, document checker or Strategy Service; event
  exceptions go to the local human queue. `test_b9_*` verifies bound handovers.
- **Cross-cutting:** every write has an idempotency key; exact retries are
  deduplicated, conflicting replays fail, message IDs are stable and sessions
  use domain IDs. Tests `test_every_apim_write_*`, `test_writes_are_*` and
  `test_message_contracts_*`. These are in-memory stubs, not durable adapters.
- **Tests:** [test_design_depth_contracts.py](../tests/test_design_depth_contracts.py);
  existing [design-agent tests](../tests/test_uplift_and_design_agents.py) are preserved.

## Protective Foundry fairness tools (IMP-34, live accepted)

- **B3/B8:** `fairness_runtime.FoundryFairnessAgent` requires an exact immutable
  agent name/version/definition. The function allowlist excludes promotion,
  reinstatement, content edits and freeze release. Model text is not a decision.
  `create_agents.pin_fairness_endpoint` also requires acknowledged 100% routing
  to the explicit immutable version, never the default `@latest`.
- **B6/B8:** deterministic preflight enforces the existing isolated audit's
  vetoes, even if the model makes no tool call. A reinstatement request persists
  a refused Tier 2 case before the model reply; failed storage never claims a case.
- **B4/B7:** closed `FairnessRequest` and empty closed function arguments bind
  all scope in the trusted host. Cohort data never reaches Foundry; member
  records/references remain host-only and tools return receipts. The Azure
  factory accepts only its explicit fairness managed identity, no CLI fallback.
- **Tests:** `test_fairness_foundry.py` covers the real S2 veto, refusal/routing,
  wrong operations, demographic/scope/tool injection, host-only reports,
  exact pin/endpoint acknowledgment checks, default read-only provisioning/reuse
  and bounded calls.
- **Host-tools evidence (per deployment):** an operator execution of the existing fairness job verifies its own principal, the S2
  veto and a create-only refused Tier 2 case, without changing the existing freeze or scheduled host. This is not a Foundry
  inference proof. The explicitly approved two-action project role and exact records-container reader are deployed and verified;
  a post-grant host proof verifies actual factory definition reads and host-only member reports, with a refused Tier 2 case and a
  create-only receipt.
- **Foundry evidence (per deployment):** the Consumer assignment is at the fairness agent only. The runtime verifies its exact
  definition and the exact endpoint selector before using the official SDK endpoint. After removing the unused project
  `responses/write`, an execution must succeed with S2 suspension, a durable refused Tier 2 case, a host-only member report and a
  create-only/read-back proof. The existing native host and freeze stay unchanged. Only `agents/read` remains in the custom
  role; no agent mutations.

## Native P2 scheduling (IMP-33)

- **B3/B5/B6/B8:** `learning.scheduled_jobs` reuses the guarded domain stages
  and existing renewable Blob lease across both existing jobs. A lost lease,
  missing/stale audit or changed aggregate fails closed and freezes; it cannot
  publish or release a protective freeze.
- **B4/B7:** fairness alone reads cohorts and writes the closed same-cycle
  audit marker in `learning-audits`. P2 has read-only access there and verifies
  digest, cadence, immutable image and actual successful isolated execution
  before every stage. No cohort data enters the handoff or runtime.
- **B7/B8:** native triggers require no coordinator, role, identity, worker-start
  permission or lock container. P2 cannot start/override fairness. Human recovery
  recognizes the native audit command without widening its authorization.
- **Tests:** `test_scheduled_p2.py` covers UTC windows, strict handoffs, actual
  execution/image/role checks, digest/freshness/lease failures, immediate
  prepublication checks, the unchanged worked example and no-new-grant infra.

## Human dev freeze recovery (IMP-33)

- **B6/B8:** `strategy_service.auth.authorize("recover", ...)` requires the
  exact approved human, delegated scope and CLI client in gg-dev, never an
  application token. Recovery does not enable disabled reinstatement.
- **B3/B6/B8:** `recovery.latest_successful_audit` validates the latest actual
  isolated audit's success, image, command and role, rather than an older success
  or a client assertion. `StrategyService.release_freeze` uses the captured state
  revision; a concurrent freeze or veto prevents release without a blind retry.
- **B8:** `recovery.release` persists authenticated actor/time/decision/reason
  through `BlobRecoveryJournal`, create-only with exact read-back. Journal failure
  refuses release or restores freeze. Rankings, snapshots and suspensions are
  preserved. The closed request rejects actor spoofing and demographic fields.
- **Tests:** `test_strategy_recovery.py` covers delegated/workload separation,
  disabled reinstatement, latest-audit failures, command/image isolation,
  journaling outages, closed requests and concurrent protection.

## Temporary P2 hosting and dev-input isolation (IMP-33)

- **B4/B7:** `dev.publish_learning_fixtures` validates the existing strict
  aggregate schema, routes cohorts only to cohort-zone, uploads create-only
  watermarked repository fixtures and rejects conflicting read-back.
  `deployDevFixtures` defaults false and both Bicep and Python restrict it to
  gg-dev. Its separate publisher identity has only two container writes and
  dedicated-registry pull; neither P2 nor runtime gains cohort/input-write access.
  Production uses telemetry and a DPO-governed cohort feed.
- **B5/B6/B7:** `learning.container_jobs` calls the same `run_component` /
  `run_guarded_stage`; only hosting, artifacts and the metric reporter differ.
  Closed cycle/stage/image handoffs cannot add demographic fields. The independent
  watchdog detects failed executions before component Python; incomplete,
  duplicate, unknown-role/image/status metadata never certifies success.
- **B7/B8:** only the operator starts/overrides jobs. P2 gets execution-metadata
  reads at the two dedicated job resources, never start/override/log/secret
  permissions on fairness. Its ML tracking role is experiment/run/metric-only,
  not Data Scientist job-submission access. Fairness remains a separate UAI.
- **Tests:** `test_learning_fixtures.py`, `test_container_p2.py` and
  `test_ml_isolation.py`; existing boundary tests are unchanged.

## Durable explainability enforcement (IMP-32)

- **B4:** `BlobExplainabilityStore.append` is create-only and compares
  identical retries; `QueuedExplainabilityStore` and
  `enqueue_in_transaction` use the independent leased journey outbox.
  `reconcile.run_reconciliation` retries and checks exact delivered-response
  cardinality, including disclosure/acknowledgment/escalation events. No raw
  conversation or generated figure is queued.
- **B7:** response IDs are opaque validated hexadecimal values; response kinds
  and withheld markers are closed typed fields. The member report requires
  the token's own ref and current step-up before reading any records.
- **B9:** dual audit-destination failure withholds generation and requests
  human escalation; hosted API returns 503 rather than unaudited model text.
  A total journal failure never claims a successful durable handover.
- **Art. 50:** hosted disclosure is audited before release. Its deterministic,
  figure-free system record does not pretend to be a model explanation.
- `tests/test_explainability.py` covers primary/queue outages, restart retry,
  ambiguous-write deduplication, immutable conflicts, exact reconciliation and
  legacy gaps, lease contention, withheld outputs, closed schemas and the
  authenticated no-cache member report. Existing served-strategy checks remain
  unchanged; acknowledgments reuse a served strategy only when available.
- **Dev history acceptance only:** `reconcile.DEV_CUTOVER_UTC` is fixed to the
  first response-ID deployment, inclusive, and active only for the exact
  gg-dev deployment environment. Pre-cutover synthetic deliveries are counted
  separately, never backfilled. `test_explainability.py` tests the exact instant,
  equivalent UTC offsets, later missing IDs, malformed timestamps and strict
  production/unknown environments. This is not runtime or learning phase logic.

| ID | Boundary (owner-defined) | Enforcement in code | Test |
|---|---|---|---|
| B1 | Calculations, eligibility, entitlements and official values stay in deterministic systems | `member_values_api` read-only, the only value source; APIM GET-only facade validates short-lived member tokens and step-up, overwrites member headers from claims, rejects mismatched paths with a correlated incident (`infra/policies/member-values.xml`); orchestrator verifies token-derived journey ownership; explicit non-GET routes reach 405 | `test_counsellor_local.py::test_member_scoping_on_values_api`, `test_azure_preparation.py::test_b1_apim_member_identity_comes_only_from_validated_claim`, `::test_b1_apim_uses_explicit_methods_for_read_only_rejections`, `test_member_values_facade.py` (token, scope, incident, retry, circuit-open, step-up and telemetry tests) |
| B2 | GenAI explains but never calculates or modifies | placeholders + `guardrails/binding.py`; `guardrails/numeric_fidelity.py` 100% gate; template-only fallback, then escalate | `test_numeric_fidelity.py` (all), evals hard gate |
| B3 | Runtime selects only from approved, versioned catalogue | `StrategyService.load_snapshot` verifies signature + active versions; tools reject unserved strategies; hybrid search always pre-filters approved status/language/state, validates returned approval evidence and replaces stale passage context; `search/corpus.py` rejects missing approval and mutable version IDs; `search/ingest.py` retires predecessors before indexing under an exclusive publication lease; runtime Key Vault role permits verification only | `test_strategy_service.py::test_tampered_or_foreign_snapshot_rejected`, `::test_snapshot_referencing_inactive_version_rejected`, `test_azure_preparation.py::test_b3_key_vault_verification_does_not_require_key_read`, `::test_b3_runtime_key_vault_role_allows_only_verification`, `test_approved_content.py` (approval, retirement, hybrid filtering, language, stale context, explicit failures and isolated source storage) |
| B4 | Raw conversations never retrain or change behaviour | P2 reads aggregates only; the closed aggregate column allowlist rejects transcripts and demographics; Azure cohort audits run separately as fairness and P2 reads only the outcome-digest-bound demographic-free veto projection; no path from conversations to prompts, templates, catalogue or models | `test_ranking_bounds.py::test_runtime_aggregates_reject_cohort_columns`, `test_strategy_azure.py::test_b7_p2_reads_only_closed_aggregate_audit_projection`, `::test_b7_outcomes_reject_any_extra_column` + review by `@guardrail-reviewer` |
| B5 | Bounded continuous ranking from expert priors, live from day one | `ranking_pipeline.py`: min sample, max rank shift, reorder-only; shared `pipeline_steps.previous_rankings` restores only missing approved-prior keys while preserving learned and explicit empty orders | `test_ranking_bounds.py` (including sparse signed snapshot, no-evidence chat priors and universal veto) |
| B6 | Fairness can veto a high performer | veto gate + suspension overlay at read time for all members; Azure state uses ETag CAS and conditional refresh on every request across replicas; failed refresh retains known suspensions and falls back to priors, failed protective writes return 503; agent has no promote/reinstate | `test_fairness_veto.py`, `test_strategy_service.py::test_suspension_overrides_rank_for_all_members`, `test_strategy_azure.py` (two-instance visibility, concurrent writes, read/write failures) |
| B7 | No runtime demographics | closed contracts (`_strict`, including suspension events), APIM closed schema; local cohort inputs only in `data/fairness/`, Azure cohort-zone accessible only to fairness and the separately approved gg-dev fixture publisher, not P2/runtime/service; exact scoped storage assignments replace account-wide runtime/P2 data writes | `test_contracts_no_demographics.py`, `test_strategy_service.py::test_demographic_payload_rejected_at_service`, `test_strategy_azure.py` (closed suspension/aggregate/audit contracts and scoped infra), `test_learning_fixtures.py`, `test_container_p2.py`, evals demographic invariance |
| B8 | Human approval for material change only | Tier 0/1 routing + Agentic Assurance Committee; mutation routes require validated Entra roles and exact P2/fairness/runtime principal, never role headers; `reinstate()` needs a `GB-` board ref and the Azure route requires a delegated human governance-group token (disabled if unset); no agent reinstatement role | `test_strategy_service.py::test_only_board_can_reinstate`, `test_strategy_azure.py` (identity/role matrix, delegated/invalid tokens, reinstatement denial) |
| B9 | Human escalation always available | `Event.ESCALATE` from every state; S6 always available; escalate on any component failure | `test_state_machine.py::test_escalation_reachable_from_every_non_closed_state`, `test_counsellor_local.py::test_human_on_request` |
| B10 | Must beat governed RAG + static ranking continuously | `assign_arm` control arm on expert priors; `uplift.evaluate_uplift`; `revert_to_static()` kill switch | `test_strategy_service.py::test_control_arm_gets_static_priors`, `::test_kill_switch_and_freeze`, `test_uplift_and_design_agents.py` |
| GNA | Guidance, not advice | versioned Intent Routing Agent -> advice handover; output steering check | `test_decision_agents.py`, `test_counsellor_local.py::test_advice_question_escalates_never_recommends`, evals |
| Art. 50 | AI disclosure | `Counsellor.start()`; `disclosure_given` in every explainability record | demo + record contents |

Dev-only proof enforcement (IMP-13): `issue_member_token.main` admits channel
proofs only in the existing hosted issuer identity environment; no token-file or
local live-proof CLI remains. `probe_channel` restricts the HTTPS orchestrator
target, does not follow redirects, bounds expiry and prints only check outcomes
and IDs. It verifies authentication, cross-member denial, unchanged control
assignment, approved S5/S2 behavior, advice handover idempotency and input
screening. `test_channel_probe.py` covers these B1/B3/B8/B9/GNA checks and
failure-output redaction. Actual numeric fidelity, agent version and content-free
traces are verified separately through existing authorized metadata access;
the issuer acquires no record/trace grants.

Azure B3 persistence additionally enforces signature and active-version checks on
both current and explicit last-good blobs, never a local-state or HMAC fallback
in Azure mode. `test_strategy_azure.py` covers tampered/inactive persisted snapshots,
explicit rollback, conditional Blob reads and rejection before storage writes;
`test_azure_preparation.py` proves the verification-only signer cannot call sign.

Foundry enforcement additions (IMP-11):
- **B1/B7:** `FoundryComposer._dispatch` ignores model member/request-key scope,
  rejects other unknown arguments and executes journey-bound host tools only.
- **B2/B3/GNA:** `_post_process` rejects literal model figures, missing/foreign
  placeholders and steering; template fallback uses the same binding/fidelity
  exit. Unknown or host-only audit tools cannot create premature records.
- **B3/B8:** runtime reads and validates an exact agent name/version/definition;
  only the explicit operator script creates versions. Explainability contract
  1.1 records agent name/version/model and rejects unknown inbound fields.
- **B4:** `foundry_tracing` disables content recording and baggage; a journey-root
  span carries only pseudonymous correlation, approved deployment metadata and
  content-free turn/screening durations.
- **B9/GNA:** incomplete/unavailable Prompt Shields or compact classifier goes
  to humans; local OR compact advice detection precedes teach-back. Tool outages
  and model outages escalate (IMP-14 supersedes the earlier outage fallback).
- `tests/test_foundry_counsellor.py` exercises fake Responses clients (literal
  figures, injected member scope, steering, timeout, unknown tools, bounded
  rounds, tool outages, valid placeholders), safety failures, Azure wiring and
  closed versioned records. Foundry rollout is deployed; conversational Azure/
  Foundry evidence still awaits the local-channel proof and key-isolation approval.
- **B2/B9 (IMP-12 extension):** dedicated keyless French Prompt Shields denies
  generation on incomplete results/timeouts; both GPT deployments have blocking
  Jailbreak/Indirect Attack/default harm filters. Model filter blocks escalate
  under IMP-14, never returning blocked model text. Regressions cover filter
  errors/incomplete replies, screening timeouts, exact policy/scoped-role wiring
  and screening-inclusive voice-channel p95 (not Voice Live STT/TTS).

Durable orchestration additions (IMP-14/IMP-15, local proof; deployment pending):
- **B1/B2/B4/B7:** `orchestrator/store.py` persists references and a closed outbox,
  no values/tokens/utterances; `MemberAuthenticator` gates each request and
  `CounsellorTools.get_member_values` independently checks step-up. Tests:
  `test_durable_orchestrator.py` (reference-only state, step-up, closed contracts).
- **B9/GNA:** `orchestrator/service.py` commits escalation state and request
  before sending; all five failure-domain chaos tests end in escalation, never
  a figure. Stable message IDs, renewable leases and durable consumer inboxes
  protect against restart, concurrency, lock loss and timeout-after-send.
  `test_durable_orchestrator.py` covers each; member-scope/resume/API checks are
  in `test_member_values_facade.py`.
- **B1/B4/B7/B9:** `contact_centre/` enforces runtime-only Entra app identity,
  role/audience, controlled reference-only handovers and deterministic
  Idempotency-Key semantics. Duplicate delivery cannot create a second handover;
  the read-only advisor queue is member-scoped at the orchestrator. Tests:
  `test_contact_centre.py` and durable consumer crash/redelivery tests.
- **B9/B10:** `infra/modules/alerts.bicep` routes DLQ/age/audit/safety/frozen/
  non-control-fallback alerts to a parameterized dev on-call action group.
  Content-free signals exclude payloads and the intentional control arm.
  Live deployment and the publisher hook now succeed. Authenticated handover/
  duplicate Azure delivery proof is blocked by local key-scoped issuance RBAC
  (the temporary grant was revoked); notification tests were rejected by Azure,
  so no email receipt is claimed. Synthetic handovers are not real callbacks.

P2 component additions (IMP-33, private infrastructure/runtime deployed; full pipeline acceptance pending):
- **B3/B5/B8:** closed aggregate/candidate artifacts; audited digest and baseline
  revalidation; atomic snapshot-ID compare-and-swap prevents stale publication;
  Tier 1 requires durable evidence before publishing. `test_p2_components.py`
  covers tampering, missing vetoes, baseline races and unavailable evidence.
  Candidate-row verification is request-key-order independent across sorted-key JSON hand-offs (IMP-93), while the
  strategy order and every field inside each row remain exact. Tests cover full serialized publication and rejection
  of duplicate/missing/changed keys, strategy orders, scores, baselines and shifts; failed verification still freezes.
  The sample deployment runs the isolated P2 regression suites before provisioning. Execution-history reads used by the
  watchdog and recovery follow all pages on the same HTTPS ARM job endpoint; malformed pages, repeated or foreign
  continuation links, and failures found on later pages cannot certify success (`test_container_p2.py`, IMP-96).
  Evidence writes require exact Blob read-back; tests cover storage retries,
  read-back conflicts and outages. Scheduling additionally checks all ten unique
  child completions/metrics, key isolation, Tier 1 persistence, positive uplift,
  served S5/S2 and the unchanged static control. Root completion is insufficient.
- **B4/B7:** the `p2` image target contains no cohort/persona/member fixture or
  local aggregate seed. Fairness uses a protective-only client with no snapshot
  read/verify and no Key Vault configuration. Actual Fairlearn MetricFrame parity
  was verified against every synthetic core finding; integer aggregate metrics
  preserve rounding. Tests cover half-point rounding, closed artifacts and image
  isolation. Cohort findings remain in the segregated zone.
- **B5/B6/B10:** existing threshold artefacts bound deterministic treatment-only
  exploration; suspended/ineligible strategies cannot participate. Tests measure
  the illustrative 5% rate over 10,000 journeys, max shift, stable assignment and
  control/frozen/fallback exclusion. Guarded component failures freeze; watchdog
  metadata failure cannot pass. Window uplift really calls static reversion, and
  repeated freeze cannot undo that kill switch. Tests cover the API's Freeze auth.
- **B8:** `p2-job-monitor-rbac.bicep` grants only workspace/job management reads,
  with a workspace-only assignment and no dataActions. The exact role and
  assignment were explicitly approved and deployed. P2's own key-isolation job
  completes on the verified immutable image with the
  expected-caller assertions (IMP-13). Full cloud pipeline/evidence acceptance
  is established per deployment, not inferred from that diagnostic or local worked examples.

ML platform isolation (IMP-16):
- **B3/B4/B7/B8:** the workspace has a system-assigned identity only and dedicated
  operational vault, storage, registry and Application Insights component.
  Only the Log Analytics ingestion workspace is reused, without a new ML-identity
  grant on that shared workspace. No signer identity attachment or
  signing-vault linkage. Workspace roles are scoped to those dependencies;
  P2/fairness artifact grants use only the replacement workspace's discovered
  Blob containers. Both private pools retain distinct single user identities;
  the old fairness shared-account Reader was removed.
  `tests/test_ml_isolation.py` checks identity, keyless resources and role principals.
  The verified workspace API sets `allowRoleAssignmentOnRG: false`; a regression
  checks this opt-out. Automatic grants on dedicated ML resources are approved
  and remain provider-owned, without duplicate IaC data-role bindings. Automatic
  grants on shared resources require explicit review (IMP-16).
  Empty-workspace checks gate the user-authorized replacement; old platform roles
  require explicit exact-ID cleanup, not an assumption about incremental ARM.

Key-isolation additions (IMP-13, deployed 2026-10-23):
- **B1/B3/B8:** `deployDevIssuer=false` by default gates the dedicated dev-only
  issuer identity, member key and grants. The issuer job attaches that identity
  only; it never shares P2/runtime. `rbac.bicep` gives it public-key read/sign on
  the member key only. No persistent developer Key Vault grant exists in IaC.
- **B3:** runtime verify-only, Strategy public-read/verify, and P2 sign/read plus
  verification are assigned only to `ranking-snapshot-signing`. P2 verifies
  existing signed state through `StrategyAdminClient.refresh`; this gate is not
  removed merely to make a sign-only assignment work. Member JWT verification
  uses the configured public JWK, not extra runtime/APIM Key Vault permissions.
- **B3/B8:** `governedgraph.dev.revoke_legacy_key_vault` defaults to a read-only inventory;
  applying requires exact reviewed IDs covering all direct vault assignments.
  Unknown grants stop cleanup. Ancestor/subscription and key-scoped assignments
  are never deleted by this tool. Incremental ARM does not perform these removals.
- **B3:** after approved replacement/cleanup, run `governedgraph.dev.check_key_isolation`
  separately under P2 and issuer with `--probe`. A successful permitted signing
  control must precede a forbidden-key HTTP 403/ForbiddenByRbac for the expected
  caller. Network errors, a different identity and cross-key success fail.
  Only fixed synthetic diagnostic bytes are signed; signatures are discarded.
  The issuer-only live probe checks, in an issuer job execution,
  member-key signing positive control and
  caller-matched snapshot-key RBAC denial. Its dedicated identity is unchanged
  throughout. No token was issued or persisted and no helper was created.
  P2's live denial is explicitly deferred to
  [prompt 08](../.github/prompts/08-p2-azureml-pipeline.prompt.md#pending-post-deployment-check-p2-key-isolation-imp-13)
  once P2 compute exists; do not switch the issuer job identity to run it.
  Per-identity CLI and independent Azure MCP inventories show all five direct
  key-scoped grants, zero vault-scope grants and no developer Key Vault grant.
- `tests/test_key_isolation.py` covers default gating, isolated job identity,
  key scopes, exact signer permissions, retained P2 verification, read-only/
  approved-ID cleanup and positive/negative probe failure modes. These offline
  tests are not a substitute for the deferred P2 live denial evidence.
