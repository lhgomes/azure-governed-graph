# Governed response evidence and safe fallback

Response evidence and signed rollback preserve the
[Azure-only execution](azure-only-execution.md) and P1/P2 isolation contracts.

## Feedback evidence

The orchestrator records optional per-response outcome choices (yes, no, skip),
response-linked follow-ups and client-confirmed voice playback, keyed by the
authenticated member and host-generated opaque response identifiers. A new in-scope
question in the same journey is linked by the host to the latest delivered, audited
explanation; the member selects nothing. Retries use
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

The optional outcome question is restricted to synthetic Azure `gg-dev`
configuration and disabled everywhere else. The orchestrator offers the host-authored closed `outcome_check`
prompt only when both `AZURE_ENV_NAME=gg-dev` and
`GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK=true` are present.
The domain vocabulary supplies exact approved English and Dutch wording;
the runtime validates every localized choice and applies numeric-fidelity and steering
checks before delivering the prompt. The writer prompts and Foundry agent
definitions are **not** changed; the question is appended only by the governed host.

Production use requires an independently recorded B8 approval for the exact
outcome wording and successful Azure-hosted validation. An unrelated vocabulary
`approval_ref` is insufficient; absent `outcome_check_approval_ref` is not approval.
Synthetic configuration does not authorize production rollout or establish that
any deployment has occurred.

## Synthetic supplemental gap-review signals

The nightly gap-review job may additionally inspect **already stored closed outcome events** in the authorized `gg-dev` sample to count explicit reported-no responses and observed linked follow-ups for responses within its bounded scan. This is a diagnostic sample, not a complete denominator, causal effect, production KPI or raw-conversation learning input. It is disabled outside `gg-dev` and does not create new content changes automatically. Any production extension requires an independently approved B8 process, complete evidence coverage, and retained privacy constraints.

## Signed static rollback

A protective `revert-to-static` action explicitly sets a frozen rollback flag.
An Azure treatment request may use the expert baseline only when it is a
verified signed, approved snapshot, persisted in immutable Blob objects.
An ordinary missing/invalid state or an unauthorized fallback remains
unusable and should escalate through the existing B9 path. The control arm
retains its original approved expert ranking behavior.

The P2 signing stage signs the exact expert baseline alongside the
learned candidate and publishes both under the same conditional operation.
Existing deployments with no stored `static` snapshot require a successful
approved P2 cycle or explicit governed migration before signed rollback
can serve treatment requests. Until then, fail closed rather than
silently authorizing unsigned expert priors.

## Azure validation

- On the approved Azure-hosted test runner: full `pytest` suite, Bicep
  compilation, schema and compatibility checks; no GitHub-hosted fallback.
- In `gg-dev`: verify immutable static Blob snapshot reads, restart behavior,
  freeze/recovery semantics, and rollback after signer/key failures.
- In `gg-dev`: test cross-member references, duplicate feedback,
  missed playback, stale/delayed events, empty/ambiguous outcomes and
  governed P2 projections.
- Validate that all existing B1–B10, P1/P2, Foundry
  writer agents, fairness vetoes, voice, APIM and audit gates still pass.
- Secure new domain-language and model-writer prompt review independently
  before enabling optional feedback prompts.

This document describes required checks, not their execution or results.
