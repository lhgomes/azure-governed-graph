# Public-release readiness checklist

**Status: owner review required; not automatically complete.** These are pre-publication controls, not claims that publishing has been authorized. Keep the repository **private** until the owner has independently completed them. Changes on a source branch cannot by themselves satisfy legal, security or operational sign-off.

## Authority, provenance and attribution

- [ ] Confirm who owns the code, prompts, samples, diagrams, audiovisual media and documentation, including any employer and third-party contributions.
- [ ] Obtain the required Microsoft/open-source release approvals **if Microsoft owns any of this work**. The [NOTICE](../NOTICE) currently names **Microsoft Corporation** as copyright holder; do not remove or replace that statement merely to facilitate publication.
- [ ] Confirm the Apache-2.0 license, NOTICE and all third-party notices are accurate and that the personal GitHub repository is an authorized publication location.
- [ ] Review any branding, product names, support claims and use of internal design decisions or customer-derived material. Distinguish independent reference work from an officially supported Microsoft product.

## Security, privacy and historical exposure

- [ ] Scan **all reachable history, tags, branches and previous PR commits** (not only current files) for secrets, tokens, private endpoints, tenant or subscription IDs, nonpublic communications, internal details and actual personal data.
- [ ] Review historical GitHub Actions runs, logs and artifacts, including runs associated with the removed CI workflow. Verify that nothing sensitive would become visible after the visibility change.
- [ ] If a secret was ever exposed, revoke/rotate it before public release. History rewriting alone does not guarantee removal from caches, forks, releases or old artifacts.
- [ ] Verify that all bundled member data and personas are synthetic, and obtain redistribution rights for diagrams, generated or recorded voice/media, sample content and evaluation transcripts.
- [ ] Review infrastructure defaults, public network exposure, synthetic token issuance, admin permissions and teardown protections. Do not treat this source review as a penetration test.
- [ ] Configure GitHub security features as available: secret scanning, push protection, dependency alerts and private vulnerability reporting; verify repository settings manually.
- [ ] Confirm a private vulnerability reporting route described in [SECURITY.md](../SECURITY.md).

## Documentation and technical evidence

- [ ] Resolve stale source/test/documentation references, inspect the PR diff and obtain a technical review.
- [ ] Verify the [current validation status](validation-status.md) and clearly identify unrun, failed or outdated evidence. In particular, the 30 October 2026 checked-in simulated model comparison does **not** pass all gates.
- [ ] Independently verify the actual human approval behind any claimed Tier-2 release, with exact immutable agent pins and accepted outcome wording. Do not infer human approval from a checked-in `approved: true` flag.
- [ ] In an authorized Azure environment, validate B1–B10 regression, Bicep/delivery, identity isolation, sample acceptance, Foundry/voice, feedback/rollback and fairness as required for the claim being made. Retain evidence with its exact commit.
- [ ] Verify regional service/model availability, quotas, privacy boundaries and the [deployment cost considerations](deploying.md#public-adopter-prerequisites-and-cost-considerations) before advertising ease of deployment.
- [ ] Add/verify repository description, topics, issue templates or support expectations as appropriate. No production support or SLA is implied.

**Publication gate:** Only an authorized repository owner can complete the manual reviews above and decide whether to change visibility. A green PR review or successful merge is not permission to publish.
