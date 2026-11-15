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
Azure resources, publishes pinned agents, deploys the services and checks
readiness. It does **not** run paid evaluations or claim acceptance.

## What happens during one journey

1. A verified member starts a journey through APIM and the durable Azure Container
   Apps orchestrator. Member identity and step-up claims are host-validated.
2. The intent/understanding agents identify the bounded request; advice and
   ambiguous intent can escalate to a human.
3. Approved strategy ranking comes from the Azure Strategy Service. A signed
   snapshot is verified against the active catalogue; universal suspensions
   override both treatment and control arms.
4. Official member values come from the protected values API and approved
   passages come from Azure AI Search. The model never determines or rounds a value.
5. Pinned Foundry agents compose explanations with placeholders. The host binds
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

## Verify after deployment

Budget for Foundry model inference and evaluation, Speech, Azure ML, Container Apps, APIM, Search, storage, networking and observability as applicable; a successful deployment incurs real Azure charges. There is no fixed cost estimate in the repository because usage, SKU and regional prices vary. Use the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) for a scoped estimate and perform a controlled teardown when finished.

- Inspect readiness, identity boundaries, the admin console and the synthetic
  member journey in Azure.
- Run B1–B10 regression tests from the approved Azure runner. These tests may
  use injected fakes for pure logic; no local application runtime is supported.
- Run pinned Foundry and synthetic channel evaluations in Azure and retain
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
