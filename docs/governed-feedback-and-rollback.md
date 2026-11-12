# Governed response evidence and safe fallback

This enhancement intentionally does **not** replace the Azure-only execution architecture or
the P1/P2 isolation contract.

## Feedback evidence

The orchestrator records optional per-response outcome choices (yes, no, skip),
response-linked follow-ups and client-confirmed voice playback, keyed by the
authenticated member and host-generated opaque response identifiers. Retries use
idempotency keys; cross-member answer references and undocumented feedback are rejected.

The learning projection exposes the following coverage counts independently:
`reported_yes`, `reported_no`, `reported_skip`, `unknown_after_window`,
`attributed_repeats`, and `ambiguous_contacts`. Unknown/skipped contacts do
**not** become successes. The bounded P2 aggregate remains a **conditional
resolution proxy** for consented, audited responses, not a population-wide,
verified first-contact-resolution KPI. Response-selection bias, missing
follow-up links and attribution uncertainty must be assessed before treating
observed rates as actionable. Do not infer actual hearing, comprehension, or
resolution from a voice playback acknowledgment.

The optional outcome question is **disabled by default**.
`GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK=true` must **not** be configured until
the approved domain pack supplies the localized `outcome_check` vocabulary,
the exact updated Foundry writer definitions are separately reviewed and
approved under B8, and Azure-hosted acceptance has passed. The change
does not update `release-approval.json`, invent approval records, or enable
automated publication. The API/evidence contracts can be reviewed beforehand.

## Signed static rollback

A protective `revert-to-static` action explicitly sets a frozen rollback flag.
An Azure treatment request may use the expert baseline only when it is a
verified signed, approved snapshot, persisted in immutable Blob objects.
An ordinary missing/invalid state or an unauthorized fallback remains
unusable and should escalate through the existing B9 path. The control arm
retains its original approved expert ranking behavior.

The P2 signing stage now signs the exact expert baseline alongside the
learned candidate and publishes both under the same conditional operation.
Existing deployments with no stored `static` snapshot require a successful
approved P2 cycle or explicit governed migration before signed rollback
can serve treatment requests. Until then, fail closed rather than
silently authorizing unsigned expert priors.

## Required Azure validation before merge and rollout

- On the approved Azure-hosted test runner: full `pytest` suite, Bicep
  compilation, schema and compatibility checks; no GitHub-hosted fallback.
- In `gg-dev`: verify immutable static Blob snapshot reads, restart behavior,
  freeze/recovery semantics, and rollback after signer/key failures.
- In `gg-dev`: test cross-member references, duplicate feedback,
  missed playback, stale/delayed events, empty/ambiguous outcomes and
  governed P2 projections.
- Validate that all existing B1–B10, P1/P2, immutable Foundry
  writer pins, fairness vetoes, voice, APIM and audit gates still pass.
- Secure new domain-language and model-writer approval independently
  before enabling optional feedback prompts.

No Azure validation or live deployment is claimed in this PR.
