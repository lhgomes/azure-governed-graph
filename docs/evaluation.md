# Evaluation method

> **ILLUSTRATIVE - synthetic data.** This page describes how GovernedGraph is evaluated. It deliberately contains no
> measurements: results depend on your models, region, prompts and domain pack, so each deployment records its own. Never
> present a result from one deployment as a property of the framework.

## Initial synthetic sample release

The release owner authorized deployment of the bundled pension sample, including its initial writer definitions,
and clarified that this authorization must not require another approval for every fresh environment. The repository
originally recorded that actual human release decision as `HR-PENSION-WTP-INITIAL` in
`examples/pension_wtp/release-approval.json`. It was a release-owner decision, not a fictional governance-board decision
or an automatically generated approval.

The authorization is limited to the two exact definition hashes in that file and the synthetic `gg-dev` reference
use case. It does not authorize changed definitions, new models, production member use or recovery from a protective
freeze. Deployment still computes the source definitions, checks the live immutable definitions, binds the approved
release to its new project/version IDs and runs all sample acceptance gates before reporting completion.
This authorization is not a claim that hosted semantic, voice or performance acceptance has already passed.
Passing tests or requesting a future deployment does not rewrite this decision or approve a changed hash.

## Welcoming letter-help release

On 2026-11-04, the release owner explicitly approved a warmer AI welcome and consistent letter-help wording,
then separately approved both exact revised writer hashes. The current record in
`examples/pension_wtp/release-approval.json` is `HR-PENSION-WTP-WELCOME-2026-11-04`:

| Writer | Approved definition SHA-256 |
|---|---|
| `gg-counsellor` | `bd85a4100359f0422b15ba75d09e87485953601faa434f137bb6080ac88c4f6f` |
| `gg-counsellor-voice-compact` | `ea18a71a3c172b8147620e3242b3290272dafda2249ef6751bc7e59d6753757c` |

This supersedes the initial hashes, not the scope or safeguards: synthetic `gg-dev` only, unchanged models,
exact definition validation and all live acceptance gates still required. The welcome retains AI disclosure,
guidance-not-advice and human help. Letter help distinguishes approved information from an unseen personal
document and invites a relevant passage without personal details; shared text is never an official value source.
No live deployment or hosted semantic acceptance is claimed by this approval or the local regression tests.

## Synthetic evaluation repair approval

On 2026-11-06, the release owner approved the scoped `gg-dev` repair and resume,
recorded as `HR-PENSION-WTP-EVAL-REPAIR-2026-11-06`. Expert priors version `1.1`
adds the missing first-contact transition `chat / english` key using the existing
approved default order. No strategy, eligibility, outcome evidence or ranking bound changes.
P2 must still audit, sign and publish that key; runtime must not accept an unsigned fallback.
The intent prompt distinguishes Dutch and English requests for a personal choice
("should I object or not?") from factual permission or procedure questions.
The two approved writer definitions remain unchanged. This approval does not certify
hosted evaluation success or authorize recovery from a protective freeze.

## Approved evaluation-contract correction

On 2026-11-06, the release owner selected "Correct the evaluation contract and finish
acceptance." Letter-help cases now declare their expected answer goal in fixed
sample-owned fixtures, independently of the runtime classifier. The expected
behaviour uses the approved `personal_letter_context` policy: welcome the request,
acknowledge the unseen letter and invite a relevant passage without personal details.
It must not require unrelated figures or teach-back about information not yet explained.
An unwanted figure-filled fallback remains a failure, even when its figures are official.

The three versioned custom Foundry judges are `gg-groundedness`, `gg-strategy-style`
and `gg-advice-refusal`. Groundedness assesses support for every factual claim and
preserved uncertainty, not coverage of every retrieved passage. Full retrieved
context and official displays are retained; unsupported claims are not hidden by
filtering context. Official evidence includes the value label, source record's
`as_of`, source system and calculation-run ID, not just its display string; a valid
bound source date must not be falsely marked invented because the harness omitted it.
Honest unseen-letter requests are not penalized merely for
omitting unrelated reference material.

Reports record the groundedness evaluator name and its actual registered version.
These scores are not directly comparable with historical `builtin.groundedness`
version `19` scores. Each dimension still requires at least **4/5 for every case**;
all deterministic, demographic, coverage and pin-validation gates remain unchanged.
A grader execution error or invalid/missing score fails closed and identifies the
case and dimension. Diagnostic rescoring of unchanged saved replies is not fresh
deployment acceptance and never completes the tracked acceptance attempt.

The release owner then separately approved the scoped letter-help fallback repair
and saved-state resume, recorded as `HR-PENSION-WTP-LETTER-FALLBACK-2026-11-06`.
Template set `1.4` adds approved English and Dutch passage-invitation wording.
When the trusted intent selects that goal, both local composition and guarded
fallback preserve it without inserting unrelated figures. The served strategy,
human escalation route, original numeric templates and writer definitions are
unchanged. Missing configured goal-language wording fails closed rather than
silently switching to a generic explanation.

## Hard gates (always on)

`python evals/run_evals.py` runs the frozen synthetic cases against the local deterministic adapters and fails unless **all** hold:

| Gate | Boundary |
|---|---|
| Numeric fidelity is 100% (no figure the system of record did not supply) | B1, B2 |
| Zero option recommendations or comparisons | guidance, not advice |
| Demographic invariance (same request key gives the same treatment) | B7 |

Evaluator floors (strategy adherence, advice refusal) must also hold. These gates run in CI and in the Definition of Done; a
change that needs a gate loosened is wrong.

## Foundry results for this deployment

After deployment readiness passes, the separate
`python -m scripts.run_foundry_evals --environment gg-dev` creates a **fresh remote evaluation run**
in the project selected by the
`gg-dev` azd environment. It uses the two pinned writers and the pinned intent, understanding and answer-review agents,
with local deterministic adapters and fixed synthetic openings. It does not mint member tokens or create agents,
identities, grants or model deployments. These are guarded-output dataset evaluations, not direct-agent or live-channel proofs.

To run it independently, activate the virtualenv installed from `requirements-azure.txt`, then run:

```bash
python -m scripts.run_foundry_evals --environment gg-dev
```

The command makes paid model and judge calls. It saves incremental evidence under `.run/foundry-evaluation-*.json` and
prints the exact project endpoint, remote evaluation/run IDs, completion status and Foundry `report_url`. Open that link
or the same project's **Evaluations > Runs**; output-only runs need not appear under an individual agent.
The source versions, dataset digest, deterministic floors and ordinal judge scores are retained. Fixed fixtures are
labelled as scripted, never as agent-generated personas. Failed gates return a nonzero exit code and block
acceptance, not deployment completion. Remote failures, timeouts and invalid grader evidence are reported as
`unavailable` (exit 2), never a pass or a measured low-quality score. Completed low-scoring runs fail with exit 1.
Retained remote IDs and partial evidence remain available for diagnosis. Judge scores cannot override hard gates.

`SIMULATED PASS` lines cover individual deterministic case checks, not the later judge scores
or the complete run. A completed remote run with `evaluation_passed: false` is a failed
acceptance gate, not a provisioning failure. Inspect failed case flags and judge scores
in the retained report, fix the cause, then rerun the evaluation with a new output path.
Reports remain under `.run/foundry-evaluation-*.json` and cannot certify channel/voice acceptance.
Runtime/domain-catalogue changes require explicit delivery with approved definitions;
evaluation-only changes do not rebuild services, recreate agent pins or rerun learning.
Each report retains its actual project, immutable versions and dataset digest.

The offline CI sweep does **not** create Foundry runs. Historical checked-in reports do not establish that an evaluation
exists in the current project: recreating a project removes its remote evaluation history, even if its display name is
unchanged. Never use an old report as evidence of a fresh deployment's acceptance.

## Comparing models

Model selection is a human decision backed by paired, synthetic, descriptive replays. Nothing in a result changes a runtime pin,
prompt, catalogue or ranking.

| Tool | Purpose |
|---|---|
| `python evals/run_model_suite.py --check-config` | Offline schema check of the suite template; missing pins are listed, never reported as readiness. |
| `python evals/run_model_suite.py --config <file> --project-endpoint "$AZURE_AI_PROJECT_ENDPOINT" --output <new file>` | Explicitly started, paid run of the frozen openings across the candidates in your copy of `evals/datasets/model-suite.json`. |
| `python evals/run_model_suite.py --config <file> --verify-report <file>` | Recomputes every result with no Azure calls. |
| `python evals/run_model_comparison.py` / `evals/calibrate_judges.py` | Two-candidate paired replay, re-scoring and judge calibration against genuine human ratings. |

Copy the suite template and fill in your own verified deployments and agent versions; never assume an old version has today's
instructions. The runs use local deterministic adapters, the pinned semantic agents shared by every candidate, and an isolated
persona; they use no member token, Key Vault access or live-proof credentials, and they create no model, agent, identity or
permission. Missing deployments or definitions block the whole paired run; each run needs a fresh output path.

A candidate meets the automated checks only if it keeps every deterministic floor, has complete expectation and actor coverage,
has quality means of at least four of five and not below the baseline, resolves at least as many cases, is no slower at initial
text p95, reports complete usage and has no increase in template fallbacks. Exit codes: **0** all candidates meet the checks,
**1** a failed check or invalid evidence, **2** blocked preflight. A cheaper model is reported separately and need not exist for
an evaluation to pass. Unknown or failed usage means unknown cost, never zero.

## Limits to state with any result

- A descriptive replay on synthetic cases, not statistical certification.
- LLM judges score guarded responses; they are not independent human ratings and the baseline judge can show self-preference
  bias. Calibrate with genuine human ratings before relying on them.
- Latency here is the initial guarded text response, not voice or audio latency. Measure voice separately with
  `scripts/benchmark_voice.py` (see the [runbook](../examples/pension_wtp/docs/demo-runbook.md)); it is a validated-dispatch
  measurement, not acoustic certification.
- Conversational quality, live integration and production service levels need representative evaluation and human review.

The sample's frozen evidence is `evals/results/model-comparison-2026-10-27.json` with its rate card, which CI re-verifies, and
the 2026-10-30 three-candidate files. They are synthetic and are not claims about your deployment.
