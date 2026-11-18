# Evaluation method

**ILLUSTRATIVE - synthetic data.** Results depend on the selected domain pack,
models, prompts, region and deployment. This page describes methods, not recorded
measurements or release decisions. Never present one deployment's result as a
property of the framework.

Tests and evaluations execute only in an authorized Azure environment.
Checked-in results and retained legacy adapters are not current Azure evidence.
See [Azure-only execution](azure-only-execution.md).

## Hard gates

All released responses and evaluation cases must satisfy:

| Gate | Boundary |
|---|---|
| Numeric fidelity: 100%; no figure absent from the authorized system of record | B1, B2 |
| Zero option recommendations or comparisons | Guidance, not advice |
| Demographic invariance for otherwise equivalent request keys and experiment arms | B7 |
| Selection only from eligible, approved, signed strategies; universal vetoes preserved | B3, B6 |
| Human help and opening AI disclosure retained | B9, Art. 50 |

Judge scores cannot override deterministic gates. Invalid or missing evidence
fails closed. Regression source is mapped in
[architecture](architecture.md#b1-b10-enforcement-and-regression-traceability);
there is no mandatory deployment-dependent GitHub test workflow.

## Release authorization and pin validation

Material prompt, model, catalogue and domain-language changes require a real
human approval. Approval records bind exact immutable definitions and scope;
they are not automatically generated governance decisions. Sample authorization
does not permit production member use or recovery from a protective freeze.

Deployment verifies source definitions, live immutable versions and the approved
release binding. Evaluation records the actual project, agent/model versions,
dataset digest and evaluator versions. Readiness or an approval record alone
does not certify semantic, channel or performance acceptance.

## Welcoming letter help release

The pension reference's earlier release approval referenced this heading as its
`evidence_ref` and carried `approved: true` for specific example writer definitions.
It is superseded by the second-contact approval below.
**A checked-in approval record and a documentation cross-reference are not, by themselves, independently verified evidence of a human decision, a successful Azure deployment, or current model acceptance.** They describe a synthetic sample's governance contract; they do not grant rights to release Microsoft-owned material or approve a production workload.

Before an operator relies on that record for anything beyond the illustrative reference, they must independently verify an actual authorized human decision, its supporting evidence, exact writer definition hashes and versions, and successful Azure-hosted acceptance in their own environment. The publication review did not establish such sign-off or re-run these checks. Do not treat an `evidence_ref` pointer as an attestation.

## Second-contact release approval

The human explicitly approved the second-contact wording, strategy eligibility
and separate expert priors for **gg-dev**, supplying reference
`MB-2026-11-17-001`. The matching release decision is
`HR-PENSION-WTP-SECOND-CONTACT-2026-11-17` in the sample's
[`release-approval.json`](../examples/pension_wtp/release-approval.json).
It bound the second-contact counsellor and compact-writer definition hashes; unchanged
older hashes are not reused as authorization for new instructions.
The release binding was subsequently extended by the customer-continuity and
[complete-ranking-coverage](#complete-ranking-coverage) approvals.

The supplied reference records the human's approval context. Its contents were
not independently fetched or verified, and it is not a new automated evaluation
result. On 2026-11-17, ACR run `dt1j` passed 32 focused regression cases for source
upgrade signing, verified predecessor compatibility, immutable history, retained
protective state, compare-and-swap conflicts, delivery ordering and writer
approval. The test image used Python 3.14/Linux AMD64 and was not pushed or
deployed. The broader preceding run `dt1h` passed 103 cases and failed one
pre-existing Entra-bootstrap test that attempted to invoke Azure CLI from the
isolated test image; it is not a full-suite pass.

Follow-up ACR run `dt1p` passed 46 focused cases after isolating upgrades from
normal deployment. These additionally verify ordinary signing/initialization
without upgrade metadata, invalid obsolete migration artifacts, a future fresh
catalogue and unchanged redeployment, no implicit conversion of old state,
normal initialization-before-workloads ordering, and the separate existing-target
upgrade command. This was also a no-push test build, not a live fresh deployment
or migration.

Semantic/channel evaluation, remaining boundary regressions, actual catalogue
migration and live signed publication remain unverified. This approval does not authorize
deleting state, lifting a protective freeze, reinstating a suspended strategy,
adding grants or using real member data.

## Complete ranking coverage

The user explicitly approved `HR-PENSION-WTP-RANKING-COVERAGE-2026-11-18` for
synthetic **gg-dev**: catalogue `1.1.1`, expert priors `1.3`, and all 200
catalogue-eligible explanation contexts. The seven existing expert orders are
preserved. New contexts use the approved default order, except second-contact
contexts use the existing focused order, filtered by strategy eligibility.
Strategy definitions, versions, writer hashes, prompts and substantive guidance
are unchanged. Outcomes are not copied between request keys.

The same approval explicitly covers the existing source-upgrade policy:
archive the exact `1.1.0`/priors `1.2` state and install the new signed approved
baseline, resetting live learned ordering while retaining suspensions and
protective pauses. The [release binding](../examples/pension_wtp/release-approval.json)
and [upgrade plan](../examples/pension_wtp/data/catalogue/source_upgrade_v1.json)
carry the matching decision and evidence reference. Ordinary redeployment still
does not perform this material state change.

Azure ACR `dtp` reproduced six coverage/follow-up failures against the old priors.
Azure ACR `dtr` passed 32 focused regressions, checking all 200 contexts in both
treatment and control, first/second-contact voice/chat follow-ups, suspension
overlays, unchanged prior orders, no cross-context outcome borrowing, signed
predecessor upgrades, durable history, concurrency guards and release bindings.
The test image was not pushed or deployed. These checks do not certify live
model answers, speech or full B1-B10 acceptance. No deployment, live model
acceptance, freeze recovery, reinstatement, identity change or permission grant
is authorized by this approval.

## Confirmation and natural closure

The reported resolved-confirmation failure was reproduced in Azure ACR run
`dt1s`: seven failures and three passes on the actual workflow graph. After the
host routing and intent guidance correction, run `dt1u` passed all 31 focused
cases, including the reported wording, English/Dutch closing acknowledgments,
pending comprehension verdicts, subsequent questions and human/advice handover,
disclosure, state-machine rules and framework/domain separation.

The closing is approved as `HR-PENSION-WTP-CONFIRMATION-CLOSURE-2026-11-17` in
template set 1.5. A resolved acknowledgment is not a comprehension success or a
terminal lock. These were no-push test builds with semantic-agent test doubles;
the new pinned intent definition and real conversation behavior still require
deployment and live acceptance. No such rollout or acceptance was performed.

## Customer-scoped conversation continuity

The user explicitly approved customer-bound cross-contact context, retention of
the latest 20 completed turns and corresponding versioned instructions under
`HR-PENSION-WTP-CUSTOMER-CONTINUITY-2026-11-17`, and separately authorized focused
Azure no-push test builds. This is approval of the illustrative implementation,
not production acceptance or authorization to deploy/delete the environment.
The sample's [release binding](../examples/pension_wtp/release-approval.json)
contains the exact revised writer hashes, unchanged by the
[ranking-coverage approval](#complete-ranking-coverage). Intent, understanding and review
definitions remain immutable and are checked/pinned by normal provisioning.

Azure ACR run `dt1y` passed 25 focused regressions, including customer-bound
history after reconstructing the service, a new contact/channel, customer and
administrator-test isolation, step-up restrictions, concurrent-contact conflicts,
ambiguous append and commit recovery, outbox barriers, withheld answers, bounded
retention, closed inputs and the existing natural-confirmation workflow.
Run `dt20` subsequently passed 28 cases with model-input plumbing and fresh
official-value binding checks. Final run `dt22` passed **106 focused cases**,
including history screening before routing, incomplete-append recovery,
identity-authority isolation, existing numeric-fidelity/demographic-contract/
escalation gates, writer approval and framework layering. It also verified that
both source writer definitions exactly match the revised approval hashes.
Earlier runs `dt1v`/`dt1w` encountered test-image dependency/permission setup
failures; `dt1x` identified missing telemetry test doubles, not an authorized
runtime fallback. Application telemetry requirements were not loosened.
Run `dt21` passed 105 cases and encountered one test-harness interaction:
the writer-approval test's subprocess guard intercepted the Azure SDK's lazy
platform detection. The transient `dt22` runner initialized that SDK dependency
before pytest; no repository guard or test was skipped, deleted or weakened.

These are Python 3.14/Linux AMD64 tests with explicit Foundry, storage and
semantic-agent doubles, executed in the existing authorized registry. They do
not prove live Foundry CRUD permissions, live model conversational quality,
microphone behavior, a successful deployment or full B1-B10 acceptance.
No application image was pushed, no workload was rolled out, no live member
traffic was sent, and no resource, role or grant was created or deleted.
The focused suite and source remain available for the separately authorized
Azure acceptance environment.

## Azure-hosted curated and adversarial journey proof

The managed-identity dev-issuer Container Apps job executes synthetic cases
against the deployed orchestrator. From the approved Azure-hosted delivery
environment, use the immutable probe image from the delivery receipt:

```bash
python -m scripts.run_dev_proof evals --environment gg-dev \
  --image <immutable-probe-image-reference> --logs
```

Tokens are short-lived and remain in probe memory. The probe checks
explanation/escalation behavior, numeric fidelity, advice refusal, immutable
audit acknowledgements, strategy eligibility, Foundry composition and
demographic invariance with matching arms. Output contains closed verdicts,
case IDs and journey IDs, never tokens, utterances or figures.

These cases complement, but do not replace, B1-B10 regression, calibrated
semantic judges, paired model comparison or representative human review.
An execution must be observed; the command's presence is not evidence of a pass.

## Hosted semantic evaluation

The separate paid evaluation command creates a fresh remote run in the selected
Foundry project:

```bash
python -m scripts.run_foundry_evals --environment gg-dev \
  --output .run/foundry-evaluation-new.json
```

It validates the pinned writers and shared intent, understanding and answer-review
agents. It neither creates agents/resources nor issues member tokens.
Incremental evidence includes remote IDs, the report URL, source versions,
dataset digest, deterministic checks and ordinal judge scores.

**Limitation:** the retained harness uses in-process deterministic adapters and
fixed synthetic openings. It is guarded-output dataset evaluation, not a direct
live-channel proof or a supported offline application. Use only in the authorized
Azure evaluation environment; adapter migration and channel validation are
separate from semantic scoring.

Expected answer goals belong to fixed sample-owned fixtures, independently of
the runtime classifier. Letter-help expectations acknowledge the unseen letter
and invite relevant text without personal details; they must not require
unrelated figures or teach-back about untaught information.

Groundedness checks every factual claim and preserved uncertainty, not coverage
of every retrieved passage. Official evidence includes labels, source dates,
source systems and calculation-run IDs as well as display strings.
Full authorized context remains available to the judge; filtering it cannot
conceal unsupported claims.

The versioned groundedness, strategy-style and advice-refusal judges each require
at least **4/5 for every case**. Evaluator versions must be recorded; scores from
different evaluator definitions are not automatically comparable.
Execution errors and invalid/missing scores identify the failing case/dimension.
Rescoring saved replies is diagnostic, not fresh acceptance.

| Exit | Meaning |
|---|---|
| 0 | All required checks passed |
| 1 | Completed evaluation failed a gate |
| 2 | Evaluation unavailable or blocked |

An individual simulated case pass is not a complete evaluation pass.
Preserve failed and partial reports; reruns need new output paths. Remote results
must exist in the actual selected project, not merely in an old checked-in report.

## Comparing models

Model selection is a human decision supported by paired synthetic replays.
No evaluation changes runtime pins, prompts, catalogue entries or rankings.

| Tool | Purpose |
|---|---|
| `python evals/run_model_suite.py --check-config` | Validate the suite schema; missing pins are not readiness |
| `python evals/run_model_suite.py --config <file> --project-endpoint "$AZURE_AI_PROJECT_ENDPOINT" --output <new file>` | Explicitly authorized paid candidate run |
| `python evals/run_model_suite.py --config <file> --verify-report <file>` | Recompute a retained report without new model calls |
| `evals/run_model_comparison.py`, `evals/calibrate_judges.py` | Paired comparison and calibration against genuine human ratings |

The retained comparison harness also uses legacy deterministic adapters.
Execute it only in the approved Azure environment with explicit domain inputs;
do not enable a local application mode to satisfy it. Missing deployments,
versions or configuration block the run, rather than activating a fallback.

A candidate must retain every deterministic floor, complete expectation and actor
coverage, quality means of at least 4/5 and no worse than baseline, at least as many
resolved cases, no slower initial-text p95, complete usage and no increase in
template fallbacks. Report cheaper candidates separately; unknown usage is unknown
cost, never zero. These checks are descriptive, not statistical certification.

## Voice measurements

Measure approved acknowledgement dispatch and first fully checked substantive
audio separately, from end of member input. The
[illustrative SLO table](architecture.md#voice-slo-table) defines their targets.
Complete-audio readiness is diagnostic and cannot substitute for either measure.

Retain every failed turn, actual sample counts, warm/cold operating modes and
stage boundaries. Small smoke windows do not certify a twenty-turn percentile.
Server dispatch is not microphone-to-speaker acoustic latency or browser playback.
The [sample runbook](../examples/pension_wtp/docs/demo-runbook.md#voice-live)
describes the benchmark procedure.

## Limits to state with any result

- Synthetic descriptive replays are not production or statistical certification.
- LLM judges are not independent human ratings and can prefer their own model.
  Calibrate with genuine human assessments.
- Guarded-output datasets do not prove live authentication, delivery or callbacks.
- Text latency does not certify voice latency.
- Production quality, fairness, privacy, cost and service levels need representative
  workload evaluation and human review.
- Readiness, release approval, regression, semantic quality and live acceptance
  are distinct evidence claims.
