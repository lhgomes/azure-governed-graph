# Azure-only effectiveness and safety evaluation

This page is the **acceptance evidence contract** for a deployed, explicitly authorized synthetic Azure Public environment. Evaluation source, diagrams and a successful deployment are **not** proof of a passing run.

## What must be verified

1. Record the exact source commit, immutable image digests, domain pack, deployed model names/versions, latest Foundry agent versions, selected Decision deployment, source catalogue and signed ranking snapshot.
2. Run the deployed synthetic journey and voice probes through the authorized gateway with the in-network dev issuer: `python -m scripts.run_dev_proof channel` and the relevant [sample runbook](../examples/pension_wtp/docs/demo-runbook.md). Tokens and private URLs stay in the Azure environment.
3. Run the B1–B10 regression suites, including prompt-injection, invalid/fresh values, numeric fidelity, advice refusal, demographic invariance, human escalation, fairness veto, published snapshot checks, recovery and continuity.
4. Test **the entire gap-review chain** on deliberately synthetic declined explanation cases: scheduled job execution, tool-free drafting, stored proposal and decision journal, rejection/deferral, stale proposal refusal, owner-approved new passage and style, distinct publishing paths, and fail-closed behavior.
5. Collect same-window client and server latency and reliability: acknowledgement dispatch, checked first substantive response, model time, retrieval time, decision calls, response gate, audio completion and channel playback as separate metrics. See [observability](observability.md).
6. Reconcile observed outcome coverage (yes/no/skip/unknown/repeats), aggregate-only learned ordering, static expert control, and independent fairness evidence. Define the denominator before calculating any effectiveness percentages.
7. Record operational cost from verified Azure receipts/rate cards; do not present token-only estimates as whole-system cost.

## Evidence is version-bound

Keep real execution IDs, transcripts, tokens, tenant identifiers, cost exports and role-approval records **outside the public repository**. In the authorized Azure evidence store retain: commit SHA, exact deployed digests, evaluated environment, timestamp, synthetic dataset hashes, per-boundary statuses (`passed`, `failed`, `unrun`, `unavailable`), aggregate counts and unknowns, trace/job/evaluation references, human sign-off, and a checksum of the protected evidence package.

The acceptance record must fail closed if any required boundary or quality requirement is `failed`, `unrun`, `unavailable` or missing. A failed hard gate cannot be compensated by improved latency or a higher average judge rating.

The publication decision is independent of the test outcome: a passing synthetic test still does not authorize real member data, public production connectivity or a Tier-2 catalogue/content change.

## Measuring an approved improvement

Use [the improvement-effectiveness reporting contract](improvement-effectiveness.md) to compare two complete, non-overlapping observation windows with the same closed situation, an exact owner decision reference, the publication timestamp and a complete input coverage watermark. Report **observed change**, not causal uplift, unless a properly designed control cohort supports that conclusion. Preserve unresolved, skipped, and unobserved outcomes as **unknown**, never as success.

## Required status

Until an authorized Azure run is attached to an approved release record, the honest project-level status is **not production-validated**. See [validation status](validation-status.md) and the [release checklist](public-release-checklist.md).
