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
