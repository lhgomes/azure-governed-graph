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

The optional outcome question is **enabled in the synthetic Azure `gg-dev` deployment configuration**
and disabled everywhere else. The orchestrator offers the host-authored closed `outcome_check`
prompt only when both `AZURE_ENV_NAME=gg-dev` and
`GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK=true` are present.
The domain vocabulary includes version `1.2` with exact English and Dutch wording;
the runtime validates every localized choice and applies numeric-fidelity and steering
checks before delivering the prompt. The writer prompts and immutable Foundry agent
definitions are **not** changed; the question is appended only by the governed host.

This is **not a production release authorization**. The existing `approval_ref` is a
historical reference for the previously approved vocabulary; it does not approve
the newly added wording. The new `outcome_check_approval_ref` remains `null`
until a real B8 decision is independently recorded. Do not change the production
environment gate, update `release-approval.json`, or mark this feature production
ready before that review and successful Azure-hosted acceptance. The feature is
configured in code only; no Azure deployment has been performed.

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
