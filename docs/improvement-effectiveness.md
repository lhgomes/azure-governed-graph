# Evaluating owner-approved improvements

The gap-review process records the proposal and the owner's decision, but publication alone does not demonstrate improved member outcomes. Use the bounded, transcript-free [effectiveness evaluator](../src/governedgraph/learning/improvement_effectiveness.py) on **authorized closed aggregate snapshots** to compare an exact situation and version.

## Contract

The input is a private JSON object with the exact keys `decision_ref`, `proposal_id`, `situation`, `published_at`, `before` and `after`. Both windows require `start`, `end`, `eligible`, `declined`, `observed_journeys`, `coverage_complete` and `source_digest`. Use an identical situation (stage, topic, channel, accessibility, language), nonoverlapping observation windows, no open/unobserved outcomes in the denominators, no test journeys, and identical eligibility rules. The `after` window begins **after** recorded publication, not after human approval.

Never pass raw questions, utterances, customer IDs, model drafts or demographic attributes. Count unknown, skipped, and repeat contacts separately in the governed outcome projection; those cases do not imply resolution.

The legacy operator-created counts mode remains descriptive, but it does **not** independently verify the source values. For source-verified evaluation, supply only the decision reference, situation and two dates-only windows (no counts or member data), for example:
```json
{
  "decision_ref": "GB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "situation": {
    "journey_state": "first_contact",
    "topic": "payment_continuity",
    "channel": "chat",
    "accessibility_mode": "standard",
    "language": "en"
  },
  "before": {"start": "2026-10-01T00:00:00+00:00", "end": "2026-10-31T00:00:00+00:00"},
  "after": {"start": "2026-11-02T00:00:00+00:00", "end": "2026-11-21T00:00:00+00:00"}
}
```
These dates are **illustrative**, not actual results. The real before/after windows must bracket the owner-approved passage's recorded publication.

```bash
python -m governedgraph.learning.improvement_effectiveness --azure-scope /secure/scope.json
```

This read-only Azure mode verifies the owner's immutable journal, approved passage and create-only published marker, plus that the exact approved version is still active in the authoritative content feed. It rederives eligible and declined counts directly from protected Azure response audits and matching non-test journey states, validates the source identities and emits a non-reversible audit digest (no member text). Missing records, owner approval, marker, or live version fail closed. **It supports owner-approved content, not catalogue strategy releases.** It must be invoked by a separately authorized operator with read access to P1 and P3 sources; no new cross-boundary runtime identity or model permissions are added.

For the previous operator-supplied **unverified** aggregate mode, use:

```bash
python -m governedgraph.learning.improvement_effectiveness /secure/closed-windows.json
```

The resulting output is a **descriptive** rate comparison and flags insufficient coverage/sample size; it does not infer causality or alter the approved catalogue. It is intentionally not a model-input, ranking-input, or automatic release gate. The official B10 effectiveness comparison remains governed by the static expert-ranking control arm and its own independent safety/fairness evidence. Store the full result privately, keyed by owner decision reference and source digests; publish only an approved summary.

## Operational integration

An operator should obtain the two closed count snapshots from an authorized Azure job or protected reporting source, with a signed/full-coverage watermark. Do not derive authoritative rates from the administration console's capped `/api/journeys` view or from the gap review's five-example sample. Until a complete source is connected, mark outcome impact **not measured** instead of showing invented gains.

See [evaluation](evaluation.md) and [governed feedback](governed-feedback-and-rollback.md).
