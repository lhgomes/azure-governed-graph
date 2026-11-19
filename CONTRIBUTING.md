# Contributing to GovernedGraph

GovernedGraph is an Azure Public reference framework with a synthetic example. Contributions should preserve deterministic facts, approved strategy choices, bounded learning, independent fairness veto, human escalation and the B1–B10 contracts described in [architecture](docs/architecture.md).

## Before opening a pull request

1. Review [getting started](docs/getting-started.md), [architecture](docs/architecture.md) and [adopting a domain](docs/adopting.md).
2. Work in a feature branch. Keep changes scoped and document the reason for any security, identity, prompt, release or domain-contract change.
3. Never add personal data, real member records, credentials, deployment-specific endpoints or subscription/tenant IDs to code, examples or tests.
4. Use sample-domain files under `examples/pension_wtp` only as **synthetic examples**. Framework code must remain independent of the sample. Changes that introduce a new domain belong in an explicit domain composition, not as implicit defaults.
5. Respect third-party asset and employer ownership rules. Contributors must have permission to submit their work under this repository's license; posting code here does not establish that permission.

## Execution and tests

- **Azure Public only** is supported for application execution and the project-specific Azure test/evaluation workflow. There is no supported local application runtime, local auth/signing fallback or generic GitHub-hosted application-test runner.
- Tests and validation scripts in the tree express intended regression and evidence contracts; **they are not proof that the latest commit passed**. No mandatory GitHub Actions CI workflow is configured because the tests and infrastructure checks require a separately authorized Azure environment.
- Run B1–B10 checks, infrastructure verification, synthetic journey probes and model/voice evaluations **only when authorized in the target Azure environment**. Keep evidence outside Git if it includes scoped identifiers or nonpublic values. Record the exact commit, immutable model/agent pins, test configuration, commands, date, gate results and any unrun checks in your PR.
- A test that cannot run without a deployment-specific resource is not a reason to hardcode those values or weaken its safety assertion. An unrun or unavailable check must be reported as such.

## Pull request expectations

Explain the purpose, affected boundaries, security and privacy implications, compatibility with the deployed state, and evidence actually collected. Document known limitations and explicitly identify changes requiring Tier-2/human release approval. Do not claim that source review, a deployment-ready result or a mocked test constitutes live acceptance.

For vulnerabilities, follow [SECURITY.md](SECURITY.md) rather than opening a public issue. 
