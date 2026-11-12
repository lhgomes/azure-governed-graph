# Azure-hosted CI bootstrap and operation

The `.github/workflows/ci.yml` workflow no longer requires a self-hosted
GitHub Actions runner. GitHub-hosted `ubuntu-latest` executes only the
**control plane**: checkout, Azure OIDC authentication, an ACR Tasks remote
Docker build, a Container Apps Job start, and status polling. Neither Python
tests nor Bicep compilation executes on GitHub runners or a developer laptop.

## One-time setup by an authorized Azure/GitHub operator

The workflow is intentionally **not ready** until the following controlled
bootstrap has happened. It fails if variables, OIDC, or the CI job are absent.

1. Using an authorized Azure **operator** and the saved `gg-dev` resource
   group and original naming seed, deploy the separate CI-only Bicep module.
   This is NOT run by CI, and CI does not get deployment/Owner rights:

   ```sh
   az account set --subscription "<subscription-id>"
   az deployment group create \
     --resource-group "<gg-dev-resource-group>" \
     --name "gg-ci-bootstrap" \
     --template-file infra/ci/main.bicep \
     --parameters environmentName=gg-dev namingSeed="<saved-naming-seed>" \
     --query properties.outputs --output json
   ```

   If the original foundation used an **empty** `namingSeed`, supply
   `namingSeed=''` (do not invent a new token). Only deploy after reviewing
   the template and verifying the existing `cae-gg-<token>` environment.
   An operator needs authorization to create resources, managed identities,
   federated identity credentials, custom RBAC roles, and role assignments.

2. In repository **Settings → Environments**, create `gg-ci`. Configure
   required reviewers and restrict deployment branches to trusted references.
   This environment is an **OIDC trust boundary**: do not leave it unprotected.
   The Bicep federation subject is explicitly
   `repo:lhgomes/azure-governed-graph:environment:gg-ci`.
   No stored client secret is required.

3. In the `gg-ci` environment (or repository Actions variables if access
   is controlled), configure exactly the five output variables:

   | Variable | Source |
   | --- | --- |
   | `GG_CI_AZURE_CLIENT_ID` | Bicep output |
   | `GG_CI_AZURE_TENANT_ID` | Bicep output |
   | `GG_CI_AZURE_SUBSCRIPTION_ID` | Bicep output |
   | `GG_CI_RESOURCE_GROUP` | Bicep output |
   | `GG_CI_ACR_NAME` | Bicep output |

   Allow RBAC propagation. Do not put Azure credentials, member tokens,
   signing keys, or app connection strings into GitHub Actions.

4. Enable branch protection requiring `azure-ci-p1` and `azure-ci-p2`
   checks, but **only once this bootstrap is complete**. Fork PRs are
   explicitly rejected, not treated as an Azure validation pass.

## What the two checks actually do

Both matrix entries build their own image using **Azure Container Registry
Tasks** into an isolated `crggci...` registry. The OIDC identity has
`Container Registry Tasks Contributor` and `AcrPull` on that registry
only. The secret-free `job-gg-ci-validation` has a system identity with
`AcrPull` on that CI registry only; no member, P2, data storage or Foundry
roles. CI's custom Job Start/Observe role is bound to **that job only**.

Each workflow run identifies an immutable image **digest** and starts a
separate Container Apps Job execution with an image/command override.
No job template is updated; concurrent P1/P2 checks cannot overwrite each
other's image. The Azure image embeds the exact checked-out PR merge commit.
If Azure doesn't return a matching `Succeeded` status before timeout, the
check fails. Queued and missing executions are never accepted.

- `azure-ci-p1`: installs the pinned P1 Azure dependencies in Azure, executes
  pytest excluding only the explicit P2 partition, and compiles
  `infra/main.bicep`, `infra/workloads.bicep`,
  `infra/model-evaluation/main.bicep`, and `infra/ci/main.bicep` with a
  pinned Bicep CLI binary.
- `azure-ci-p2`: installs independent `requirements-p2.txt` pins (Azure ML
  has an incompatible Blob package ceiling), and runs the explicit P2
  component/learning/fairness/isolation pytest partition.

**Existing tests still require audit/migration.** Some tests instantiate
legacy file-backed adapters, even though those adapters are not Azure runtime.
This CI intentionally does not hide failing tests with `|| true`, skip
marks, historical simulations or invented fixtures. Complete the test migration
and collect real Azure execution results before making these checks required.

## AI and integration acceptance are separate gates

The October 27 historical `model-comparison` JSON is not proof of a current
deployed model. The former `evals/run_evals.py --comparison ...` step is
removed from CI.

Full Foundry, APIM, Voice Live, P1/P2, fairness and authorized synthetic
member-token acceptance must execute in their existing dedicated `gg-dev`
proof jobs using **deployed immutable images and constrained identities**.
The existing `scripts/run_dev_proof.py evals` procedure is the starting
point; this generic CI identity does NOT have access to the dev issuer job
because its runtime managed identity can sign member tokens. A separately
protected, explicitly approved live-evaluation workflow can be added only
after confirming its narrow permissions and release gate.

Unit/contract green is not a live evaluation pass. Production promotion must
still require independently reviewed live Azure evidence and B1–B10 / B8
human approval where applicable.

## Observability, failure and cleanup

The workflow prints the immutable ACR digest and exact Container Apps Job
execution name. Review Container Apps job executions and the `gg-dev`
Log Analytics workspace for failed test output. The job replica has a
60-minute limit, retries disabled, and is not scheduled. ACR and Container
Apps Jobs incur consumption and registry charges; retire the CI-only registry
and job with an approved teardown when no longer needed.

References:
- https://learn.microsoft.com/azure/container-apps/jobs
- https://learn.microsoft.com/cli/azure/containerapp/job
- https://learn.microsoft.com/azure/container-registry/container-registry-rbac-built-in-roles-overview
- https://learn.microsoft.com/azure/container-registry/container-registry-quickstart-task-cli
