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

1. No required GitHub CI job depends on a deployed environment, Azure subscription, managed identity, container registry, or manually populated GitHub variables. The removed CI workflow was not a deployment-independent test.
2. Retain pure B1–B10 unit/contract test source as regression coverage, but do not claim it has executed. The repository has no automatic test runner.
3. Application integration, synthetic channel probes, and Foundry/voice acceptance can only run in an explicitly deployed Azure environment under its existing scoped identity model; they are not generic PR checks.
4. Bicep compilation and infrastructure acceptance occur in the explicitly authorized Azure delivery process, not in a workflow that must infer deployment parameters.
5. Missing test or evaluation evidence is not success; any acceptance/release decision must rely on separately captured, actual Azure results.

## Work items

- [x] Replace the conflicting repository instruction that mandates offline runtime.
- [x] Prevent Azure API clients and Search from silently falling back to local adapters when endpoints are missing.
- [x] Remove deployment-coupled GitHub CI jobs and their unused Azure CI bootstrap; the repository requires no deployment parameters for source-control actions.
- [x] Add a curated/adversarial synthetic evaluation probe that runs inside the existing Azure dev-issuer job and keeps tokens in memory.
- [ ] Migrate the paired Foundry model-comparison and judge-calibration harness off its legacy in-process adapters.
- [ ] Consolidate the duplicate learning-cycle execution paths behind the audited Azure pipeline.
- [x] Remove local-versus-Azure dispatch in member-facing services and the admin backend.
- [ ] Remove remaining unused file adapters, offline demo entry points and fixture wiring.
- [ ] Migrate tests to Azure execution and unit-level injected fakes; keep all B1-B10 regression gates.
- [x] Remove local mode from deployment manifests, runtime images and application configuration; split P1 and P2 dependencies.
- [ ] Complete the remaining evaluation entry-point migration and documentation cleanup.
- [ ] Collect Azure-hosted test evidence and deployment readiness without automatically deploying.

**Status:** no automatic repository CI workflow is configured. Azure acceptance remains an explicit activity following deployment. Existing tests remain as source for possible Azure-based regression verification; they are not executed or counted as passing by GitHub Actions.
