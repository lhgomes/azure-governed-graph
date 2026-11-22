# Getting started — Azure Public

GovernedGraph is an Azure-hosted reference for member journeys that explain trusted
facts while enforcing ten governance boundaries (B1–B10). The pension example contains
**ILLUSTRATIVE — synthetic data**; it is not a production pension service.

## Prerequisites

This is an **Azure deployment guide**, not a local quickstart. It does not provide a local application runtime or a zero-cost simulation. Before provisioning, review [deployment prerequisites, permissions, regions and charges](deploying.md#public-adopter-prerequisites-and-cost-considerations) and the [validation status](validation-status.md).

- An approved Azure Public subscription, resource group, region and managed-identity
  permissions for the selected `gg-dev` reference deployment.
- Azure CLI, Azure Developer CLI and Bicep on the **approved Azure-hosted delivery environment**.
- A domain composition such as `examples/pension_wtp/deployment.json` and private
  delivery inputs (see [deployment](deploying.md)).
- Separately authorized Azure-hosted validation jobs. The repository does not
  require a deployment-dependent GitHub test workflow.
- Permission to provision subscription/resource-group resources, configure Entra applications and groups, and assign scoped roles (or an administrator who can perform those actions).
- Reviewed service and model availability, quotas, budget and network requirements for the selected Azure regions. The example reference uses Sweden Central and France Central.

## Deploy the reference environment

Review `docs/deploying.md` and the concrete subscription, naming seed, operator
and location inputs before deployment. From the approved Azure-hosted delivery environment:

```bash
python -m scripts.deploy --inputs .azure/private-delivery.json --plan
python -m scripts.deploy --inputs .azure/private-delivery.json
```

The first command reviews the scope and sequence; the second provisions the
Azure resources, publishes the agents, deploys the services and checks
readiness. It does **not** run paid evaluations or claim acceptance.

## What happens during one journey

1. A verified member starts a journey through APIM and the durable Azure Container
   Apps orchestrator. Member identity and step-up claims are host-validated.
2. Microsoft Decision 1.1 returns bounded intent and understanding decisions through the SystemOne API; advice and ambiguous intent can escalate to a human. These roles are not separate Foundry prompt agents.
3. The host requests approved strategy ranking from the Azure Strategy Service. A signed
   snapshot is verified against the active catalogue; universal suspensions
   override both treatment and control arms.
4. The **host-controlled release gate** binds official member values obtained from the protected read-only values API; approved passages are retrieved through host-authorized Azure AI Search tools. A model never determines or rounds an authoritative value.
5. Foundry agents compose explanations with placeholders. The host binds
   authoritative values, enforces numeric fidelity and records immutable audit
   evidence before exposing a response.
6. The journey and outbox are committed in Azure Blob storage and delivered via
   Service Bus. Human escalation remains available.

## How governed learning works

P2 is isolated from member-facing P1 and cannot read raw conversations or member
values. Azure-hosted scheduled jobs aggregate outcome references, audit cohorts in
the segregated fairness identity, enforce fairness vetoes and bounded rank shifts,
sign an approved snapshot with Key Vault and publish through the Strategy API.
The control arm remains on static expert rankings. Tier 2 changes require human
governance. See [architecture](architecture.md) and [evaluation](evaluation.md).

## How human-guided improvement works

The separate gap-review job (`job-gg-gap-review`, default schedule **03:30 UTC daily**) is deployed when the administration console is enabled. It selects explanation-routed declined or final-review-flagged answers from a default 30-day window, excluding selected-style tests and other nonqualifying exits. The job groups situations by stage, topic, channel, accessibility mode and language; at least five eligible declines across three journeys are required for a proposal.

A separately authorized, **tool-free Foundry `gg-gap-review` agent** can draft missing or revised content and recommend styles based on already approved patterns. Its output is untrusted. The administration console presents missing content, content revisions, style gaps and possible false refusals. Only an authorized owner may approve a material change. Approved content is verified and published by the content publisher; approved styles use the signed catalogue release. New explanation patterns not yet approved cannot be activated by this mechanism.

Short-lived question text is retained for up to 30 days for this workflow, but **not used for bounded ranking or model retraining**. Approval and publication are not proof of improved outcomes; new behavior needs evaluation against the control arm and safety/fairness gates. See the [gap-review architecture](architecture-gap-review.md) and [improvement-idea administration guide](administration.md#improvement-ideas-declined-answers-and-proposed-fixes).

## Verify after deployment

Budget for Foundry model inference and evaluation, Speech, Container Apps, APIM, Search, storage, networking and observability as applicable; a successful deployment incurs real Azure charges. There is no fixed cost estimate in the repository because usage, SKU and regional prices vary. Use the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) for a scoped estimate and perform a controlled teardown when finished.

- Inspect readiness, identity boundaries, the admin console and the synthetic
  member journey in Azure.
- Run B1–B10 regression tests from the approved Azure runner. These tests may
  use injected fakes for pure logic; no local application runtime is supported.
- Run Foundry and synthetic channel evaluations in Azure and retain
  versioned evidence. A deployment-only green status is not evaluation success.

The execution and evidence contract is described in
[Azure-only execution](azure-only-execution.md).

## Terminology

| Term | Meaning |
|---|---|
| Member | Person being guided, for example a customer or participant |
| Journey | A stateful interaction across questions and channels |
| Official value | A fact owned by a system of record, never calculated by the model |
| Approved strategy | A versioned explanation style in the governed catalogue |
| Ranking snapshot | Signed ordering of approved strategies, subject to a fairness veto |
| P1 / P2 | Member-facing Azure runtime / isolated Azure learning and assurance plane |
| Control arm | Static approved expert ranking used for governed comparison |
| Explainability record | Immutable reference-only evidence of a delivered response |
| Gap-review proposal | Inert, owner-reviewed suggestion from recurring eligible declined explanations |
| Content publisher | Separate job that validates and publishes owner-approved passages |
| Catalogue publication | Existing signed process that makes owner-approved explanation styles eligible |
