# Azure-only execution refactor

This branch transitions GovernedGraph to an **Azure Public-only** application runtime.
No developer workstation or generic GitHub-hosted runner is an approved application/test execution target.

## Invariants

- B1-B10 are preserved without weakening guardrails, fairness, the control arm, human escalation or approval gates.
- P1 retains its hosted journey state, APIM-backed values, approved Search, Service Bus, Blob audit/outbox, Foundry and voice.
- P2 retains its separately authorized managed identity, audited aggregates, fairness veto, signed snapshots and native schedule.
- Azure integrations fail closed when required configuration is absent. No fallback to files, in-process APIs, offline agents or local signing.
- Synthetic Azure sample adapters remain available behind production-facing contracts. They are **not** permission to enable local runtime.
- Existing Azure stores, schemas, identities and deployment names are preserved unless an independently reviewed migration is necessary.

## Verification contract

1. Pure logic and governance-boundary unit tests run in the isolated Azure Container Apps CI Job. A GitHub-hosted runner only authenticates using protected OIDC, submits ACR remote builds and starts/observes the Azure job; it never executes test code.
2. Application integration and synthetic channel probes run in the deployed Azure environment, using the existing scoped identity model.
3. Curated/adversarial scenario probes run inside the Azure dev-issuer job. Paid Foundry model comparisons and judge calibration need their own Azure-hosted evaluation jobs and pinned agent versions.
4. Bicep compilation runs on Azure-hosted infrastructure. Deployment remains an explicit separate action.
5. Missing CI bootstrap resources, GitHub environment approval, OIDC credentials, job executions or test evidence are blockers; they are never treated as a successful test.

## Work items

- [x] Replace the conflicting repository instruction that mandates offline runtime.
- [x] Prevent Azure API clients and Search from silently falling back to local adapters when endpoints are missing.
- [x] Replace unavailable Azure self-hosted GitHub runner requirements with CI-only Azure Container Apps Jobs, a dedicated registry and protected OIDC orchestration (one-time operator bootstrap remains external).
- [x] Add a curated/adversarial synthetic evaluation probe that runs inside the existing Azure dev-issuer job and keeps tokens in memory.
- [ ] Migrate the paired Foundry model-comparison and judge-calibration harness off its legacy in-process adapters.
- [ ] Consolidate the duplicate learning-cycle execution paths behind the audited Azure pipeline.
- [x] Remove local-versus-Azure dispatch in member-facing services and the admin backend.
- [ ] Remove remaining unused file adapters, offline demo entry points and fixture wiring.
- [ ] Migrate tests to Azure execution and unit-level injected fakes; keep all B1-B10 regression gates.
- [x] Remove local mode from deployment manifests, runtime images and application configuration; split P1 and P2 dependencies.
- [ ] Complete the remaining evaluation entry-point migration and documentation cleanup.
- [ ] Collect Azure-hosted test evidence and deployment readiness without automatically deploying.

**Status:** implementation continuing without test execution at the user's request. See [Azure CI bootstrap](azure-ci.md); this PR adds the job configuration but has not provisioned or executed it. The presence of a checked item is not evidence of Azure test execution; no tests may be reported as passed until Azure Container Apps returns a successful job execution.
