# Evaluating owner-approved improvements

The gap-review process records the proposal and the owner's decision, but publication alone does not demonstrate improved member outcomes. Use the bounded, transcript-free [effectiveness evaluator](../src/governedgraph/learning/improvement_effectiveness.py) on **authorized closed aggregate snapshots** to compare an exact situation and version.

## Contract

The input is a private JSON object with the exact keys `decision_ref`, `proposal_id`, `situation`, `published_at`, `before` and `after`. Both windows require `start`, `end`, `eligible`, `declined`, `observed_journeys`, `coverage_complete` and `source_digest`. Use an identical situation (stage, topic, channel, accessibility, language), nonoverlapping observation windows, no open/unobserved outcomes in the denominators, no test journeys, and identical eligibility rules. The `after` window begins **after** recorded publication, not after human approval.

Never pass raw questions, utterances, customer IDs, model drafts or demographic attributes. Count unknown, skipped, and repeat contacts separately in the governed outcome projection; those cases do not imply resolution.

Run an operator-created evidence package in the approved Azure environment:

```bash
python -m governedgraph.learning.improvement_effectiveness /secure/closed-windows.json
```

The resulting output is a **descriptive** rate comparison and flags insufficient coverage/sample size; it does not infer causality or alter the approved catalogue. It is intentionally not a model-input, ranking-input, or automatic release gate. The official B10 effectiveness comparison remains governed by the static expert-ranking control arm and its own independent safety/fairness evidence. Store the full result privately, keyed by owner decision reference and source digests; publish only an approved summary.

## Operational integration

An operator should obtain the two closed count snapshots from an authorized Azure job or protected reporting source, with a signed/full-coverage watermark. Do not derive authoritative rates from the administration console's capped `/api/journeys` view or from the gap review's five-example sample. Until a complete source is connected, mark outcome impact **not measured** instead of showing invented gains.

See [evaluation](evaluation.md) and [governed feedback](governed-feedback-and-rollback.md).
