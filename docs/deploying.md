# Delivering GovernedGraph

The reference composition is **gg-dev**, swedencentral, with dedicated keyless
Content Safety in francecentral. All domain data is **ILLUSTRATIVE - synthetic
data**. Deployment readiness is not evaluation, conversational acceptance,
production approval or human recovery.

## Public adopter prerequisites and cost considerations

This is a reference topology for **Azure Public**, not a one-command hosted demo. Before running the delivery driver:

| Area | Review before provisioning |
|---|---|
| Tenant and identity | An authorized deploying human able to resolve their identity with Microsoft Graph `/me`; permission or assisted approval to register Entra applications and security groups, and to grant the scoped RBAC assignments declared in Bicep. The repository does not include reusable Azure credentials. |
| Subscription and authorization | An approved subscription and permissions for subscription-scope/resource-group deployments, resource creation, managed identities, custom roles and role assignments; the exact role grants must be reviewed under local policy. |
| Regions and quotas | The bundled `gg-dev` composition references `swedencentral` for the primary deployment and `francecentral` for Content Safety. Verify allowed data movement, each model's regional availability, quota and service limits before deploying or changing the template. |
| Toolchain | Python **3.14** and pinned `requirements-azure.txt` in an **approved Azure-hosted delivery environment**, plus Azure CLI, Azure Developer CLI and Bicep. `pyproject.toml`'s broader Python metadata is not a guarantee that earlier Python versions are supported by this Azure delivery path. |
| Private input | Create `.azure/private-delivery.json` from the placeholder example below; provide subscription, on-call destination and composition. The operator ID is optional and defaults to the signed-in user in the selected subscription's tenant. Keep actual identities and all receipts outside Git. The script selects/generates resource-group and naming-seed information as described below. |
| Budget and cleanup | Estimate recurring charges and set a budget. The sample can provision Foundry model deployments, AI Search, Container Apps, ACR, APIM, Key Vault, Storage, Service Bus, networking, Content Safety and monitoring. Live inference, voice, evaluations, retained telemetry and idle SKUs may all cost money. Read the teardown section before provisioning. |

Prices are deployment-dependent: **no cost or latency figure in the repository should be treated as an Azure quote**. Use the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) and your subscription's actual regional quotas/SKUs. The default reference example and its sample member data are synthetic; do not substitute real members or production records without a separate security, privacy and governance assessment.

For an externally visible release, also complete the [publication checklist](public-release-checklist.md). Deployment readiness alone never authorizes production use.

### Resource-provider registration

Before provisioning, use the approved subscription explicitly and review its
provider registrations. Registration requires the provider's
`/register/action` permission; ask an authorized subscription administrator if
the deploying identity does not have it. These commands do not grant roles or
deploy application resources.

```bash
SUBSCRIPTION_ID="<approved-subscription-id>"
az provider list --subscription "$SUBSCRIPTION_ID" \
  --query "sort_by(@, &namespace)[].{Provider:namespace,State:registrationState}" \
  --output table
```

The current topology uses `Microsoft.ApiManagement`, `Microsoft.App`,
`Microsoft.Authorization`, `Microsoft.CognitiveServices`,
`Microsoft.ContainerRegistry`, `Microsoft.Insights`, `Microsoft.KeyVault`,
`Microsoft.ManagedIdentity`,
`Microsoft.Network`, `Microsoft.OperationalInsights`, `Microsoft.Resources`,
`Microsoft.Search`, `Microsoft.ServiceBus` and `Microsoft.Storage`.

APIM **Standard v2** outbound VNet integration additionally requires
`Microsoft.Web` registration because its subnet is delegated to
`Microsoft.Web/serverFarms`, even when no App Service is deployed. An absent
registration causes `SubnetSubscriptionMustBeRegisteredWithMicrosoftWeb`.
Register it and verify completion before retrying delivery:

```bash
az provider register --subscription "$SUBSCRIPTION_ID" \
  --namespace Microsoft.Web --wait
az provider show --subscription "$SUBSCRIPTION_ID" \
  --namespace Microsoft.Web --query registrationState --output tsv
```

The deployment requires only the resource providers used by the foundation.
Register another provider only if a selected workload explicitly requires it.
Registration does not certify quota, regional availability, RBAC or readiness.

See Microsoft's guidance on [resource-provider registration](https://learn.microsoft.com/azure/azure-resource-manager/management/resource-providers-and-types)
and [APIM outbound VNet integration](https://learn.microsoft.com/azure/api-management/integrate-vnet-outbound).

#### Network feature registration failure

Provider registration does not register individual subscription features. If
the network deployment fails with `SubscriptionNotRegisteredForFeature` naming
`Microsoft.Network/AllowBringYourOwnPublicIpAddress`, inspect the failed
resource operation before making subscription changes:

```bash
az deployment operation group list --subscription "$SUBSCRIPTION_ID" \
  --resource-group "<selected-resource-group>" --name network \
  --query "[?properties.provisioningState=='Failed'].{Resource:properties.targetResource.id,Error:properties.statusMessage}" \
  --output json
```

Azure can report this error for the ML outbound public IP
(`pip-gg-ml-outbound-<token>`). The current template requests an ordinary
Azure-allocated Standard static public IP for its NAT gateway; it does not
request a custom IP prefix or supply an IP address. Do not remove the NAT
gateway or enable default outbound access to bypass the error.

If Azure requires this feature, an authorized subscription administrator can
register it under the organization's feature-approval policy:

```bash
az feature register --subscription "$SUBSCRIPTION_ID" \
  --namespace Microsoft.Network --name AllowBringYourOwnPublicIpAddress
az feature show --subscription "$SUBSCRIPTION_ID" \
  --namespace Microsoft.Network --name AllowBringYourOwnPublicIpAddress \
  --query properties.state --output tsv
```

Repeat the read-only `az feature show` command until it reports `Registered`.
Only then refresh the provider registration to propagate the feature:

```bash
az provider register --subscription "$SUBSCRIPTION_ID" \
  --namespace Microsoft.Network --wait
az provider show --subscription "$SUBSCRIPTION_ID" \
  --namespace Microsoft.Network --query registrationState --output tsv
```

After both states are `Registered`, retry the authorized delivery with the
same target. If feature registration remains `Pending`, is unavailable or
the failure persists, contact Azure Support with the failed resource ID,
deployment name and correlation ID. Do not bypass subscription policies.
See [Azure feature registration](https://learn.microsoft.com/azure/azure-resource-manager/management/preview-features)
and Microsoft's [guidance for this public-IP feature error](https://learn.microsoft.com/azure/microsoft-discovery/troubleshoot-microsoft-discovery#supercomputer-deployment-stalls-because-a-required-public-ip-feature-isn't-registered).

## Commands and explicit inputs

Use Python 3.14, Azure CLI, Azure Developer CLI, Bicep and the existing verified
`requirements-azure.txt` environment in an approved Azure-hosted delivery environment:

```bash
.venv/bin/python -m scripts.deploy --inputs .azure/private-delivery.json --plan
.venv/bin/python -m scripts.deploy --inputs .azure/private-delivery.json
# Non-interactive automation: choose one target explicitly.
.venv/bin/python -m scripts.deploy --inputs .azure/private-delivery.json --reuse rg-gg-dev-existing
.venv/bin/python -m scripts.deploy --inputs .azure/private-delivery.json --new
.venv/bin/python -m scripts.run_foundry_evals --environment gg-dev \
  --output .run/foundry-evaluation-new.json
```

`--plan` validates the local configuration/composition and prints the sequence;
it makes no cloud calls, prompts or writes and does not verify/select an Azure
target. Obtain the required authorized Azure preview before
applying the delivery. Delivery prints each major operation and each image
build/reuse, without exposing private inputs. The evaluation command is separate and incurs model and
judge usage; it never provisions, starts channel probes or issues member tokens.
An output file must be new, preserving previous evidence.

The foundation provisions Microsoft-Decision-1.1 (version `1`,
`DataZoneStandard`, capacity `10`) in the workload's Sweden Central Foundry
account and passes its deployment name to the orchestrator. The default name
is `decision-v1`; set `decision_deployment` in the private delivery inputs only
to provision it under a different name. Check subscription quota before
delivery. The orchestrator calls that account's Decision API with Entra
authentication and does not fall back to another model or the benchmark
account. Verify runtime access with the synthetic in-network acceptance probe.

Image builds upload a source archive before ACR queues a run. Keep `.azure` and
`.run` excluded in `.dockerignore`: they hold private delivery inputs, tool
environments and local evidence, not image sources. Use directory patterns
without trailing slashes so Azure CLI's archive packer honors them as well as
Docker. A large context upload is distinct from a failed cloud build; inspect
the recorded run ID with `az acr task logs --subscription <subscription>
--registry <registry> --run-id <run-id>`. The pinned OpenTelemetry API and SDK
must use the same version because the SDK requires its exact API counterpart.

After successful readiness checks, the final JSON result is followed by
`Demo URL: https://<gateway>/demo/` and `Admin URL: https://<gateway>/admin/`
for convenient access. When `voice_demo` is false, the summary prints
`Demo URL: disabled` instead.

### Delivery evidence and failure diagnosis

Every applied attempt, including the explicitly selected catalogue upgrade,
creates an owner-only `.run/deliveries/<run-id>/receipt.json` **before the first
Azure command**. It records stage timestamps, deployment-script/Bicep file
hashes, the Git commit when available, the selected subscription/group and
exact naming seed, immutable image references, agent pins and native job
execution IDs. A failure prints the stage and receipt path; it does not depend
on reaching the final readiness check. `--plan` remains cloud-free and writes
nothing.

Each ARM submission uses a unique attempt/operation deployment name. Its
compiled JSON template and exact private parameters are saved separately in
that attempt's directory before submission; their SHA-256 hashes, Azure
template hash and correlation ID bind the evidence to what was sent. Repeated
bootstrap-job submissions no longer overwrite each other's parameter files.
The deployment name identifies an operation, not a resource: existing resource
names, naming seeds, bootstrap ordering and release-approval gates are unchanged.
Failed or timed-out submissions collect read-only Azure deployment state and
operation/resource IDs when available. Ctrl+C records an unconfirmed outcome
without launching further commands.

Network observations after foundation and workload provisioning capture the
environment's reported address and VNet configuration, private DNS records and
links, gateway subnet and private-endpoint/NIC state. These are control-plane
observations, **not an in-network DNS or TCP test**. Unavailable observations
are explicitly recorded and warned about; they never become an empty-list
success or trigger automatic remediation. Readiness separately records app
revision/image/state, native schedules, HTTP status and allowlisted gateway
request/activity IDs. It does not retain HTTP bodies, cookies, tokens, command
arguments, raw command output or environment values in the receipt.

The private parameter snapshots can contain operator/contact configuration and
approved application settings: do not publish the attempt directory or commit
it. The receipt is also operational metadata, not a public report. Files are
written atomically with owner-only permissions and remain excluded from image
build contexts by `.dockerignore`. The existing `.run/delivery.json` remains a
**successful** delivery receipt and points to its attempt evidence; an earlier
success must not be interpreted as the outcome of a later failed attempt.
These records do not resume/skip steps, authorize changes, evaluate a model or
prove live conversational acceptance.

### Gateway readiness times out before reaching a healthy app

An APIM HTTP 500 is not by itself evidence that an application crashed. Inspect
the gateway request trace and app logs. A TCP connection timeout with no matching
application request is a connectivity failure; rebuilding the same image does
not repair it.

The foundation now provisions an explicit Container Apps environment private
endpoint and points the existing private DNS apex/wildcard at its allocated
address. Public network access stays disabled. This adds Private Link and
Container Apps private-endpoint charges, but no identities or role assignments.
Do not substitute a guessed private IP or enable public access.

For an existing environment, the standalone
[`container-apps-ingress.bicep`](../infra/modules/container-apps-ingress.bicep)
module permits a separately authorized, scoped repair. Preview it with the
existing location, tags, token, private-endpoints subnet and VNet IDs. The only
intended changes are the environment private endpoint and its managed NIC,
plus the existing environment DNS records. Do not apply a preview that replaces
the environment, changes access grants or touches data stores.

Verify the private endpoint is approved, DNS uses its allocated private address,
and the public `/admin/` route returns 200. Also inspect learning/fairness job
failures: a healthy container does not establish that its gateway dependencies
are reachable. Ordinary delivery can then be rerun with the same `--reuse`
target; no teardown, source-catalogue migration or special deployment mode is
part of this network repair. Readiness is still not live conversational
acceptance or an evaluation pass.

The authorized 2026-11-17 recovery passed the existing delivery readiness
function after applying the private endpoint and DNS correction. The admin
and demo pages returned 200; an unauthenticated admin API request still returned
403. Both existing native fairness and pipeline jobs completed successfully.
Full foundation compilation and Azure validation of the scoped recovery module
passed. No images, identities, access grants or data stores were replaced.
This evidence does not establish live conversation quality or Foundry continuity
acceptance.

The subsequent baseline comparison compiled committed revision
`82e57757486bdceb2f48258410541bbdb80057c9` and compared its generated template
hashes with Azure's retained records from the failed attempt. Both the entire
foundation and workload templates matched exactly, including their network,
Container Apps, DNS and APIM modules. Azure's DNS deployment parameters confirm
that the original records were supplied `172.130.156.57`, and the retained
bootstrap execution preceded workload provisioning. This is stronger evidence
than merely observing no infrastructure diff, but does not reconstruct the
overwritten initial creation request or establish the original cause. That
address is a valid public IP, not an invalid allocation. The private endpoint
is a verified recovery path, not proof of an Azure allocation defect or a
required correction to the committed internal-ingress design.

Resource Graph history for the initial creation window also returned the
expected subnet and `internal=true` with that same reported address. The retained
change records showed provisioning/platform-version changes, not a later change
to the internal flag or subnet. No historical deployment-template snapshots
were returned. These observations narrow the diagnosis but still do not recover
the initial ARM request or prove why its native ingress path was unreachable.

### Demo startup

Chat and voice share the authenticated `/demo-stream` startup path. An HTTP
101 upgrade or a healthy app does not prove that the first message was
delivered. Startup must obtain a synthetic member bearer through the internal
managed-identity issuer and create an authorized journey before delivering
the AI disclosure. Chat then enables typing; voice requires acknowledgement
of disclosure playback before enabling capture.

Issuer or journey-start failure must prevent successful session startup.

The admin console reads the Strategy Service's required `current`, `last_good`,
`frozen` and `suspensions` fields and its optional signed `static` baseline and
`static_override` marker. The marker is valid only when true, frozen and without
a current learned snapshot. Unknown fields and malformed state remain errors;
the console must not drop protective state to render an overview.

Bounded Entra bootstrap also creates a distinct platform **owners** security group
and enrolls the signed-in deploying human, resolved through Microsoft Graph `/me`
using the selected subscription's tenant. The configured delivery operator may be
a different person and is not substituted for that caller. Reusing the managed
group verifies existing membership; supplied groups are not modified.
`GOVERNEDGRAPH_ADMIN_OWNER_GROUP_ID` is persisted in the azd environment and passed
through the workload configuration to the console. Owners have protective-action
access and a separate owner authorization flag. Creating this group grants no
Azure resource roles or signing/publication rights, and does not by itself
implement revocable ranking or reinstatement overrides.

### Choose an existing or new deployment

Reusing infrastructure is not the same as migrating governed application state.
The second-contact release changes the source catalogue and strategy versions.
Its writer approval is recorded under
`HR-PENSION-WTP-SECOND-CONTACT-2026-11-17`, with human-supplied reference
`MB-2026-11-17-001`; see [evaluation](evaluation.md#second-contact-release-approval).
Ordinary initialization validates and preserves existing state rather than
overwriting it. Normal `scripts.deploy` signs and initializes expert priors
before complete workload provisioning, as usual. It never loads, validates or
signs a source-upgrade plan and never migrates an older catalogue implicitly.
Fresh and unchanged deployments do not depend on migration approvals or target
hashes; future fresh catalogue versions do not require updating an old upgrade
plan.

### Explicit source-catalogue upgrade

The existing 1.1.0 deployment requires a separate, explicitly selected operation,
not the normal deployment command:

Replace `rg-gg-dev-existing` below with your actual resource-group name; it is
only a placeholder. `--plan` prints the supplied name without checking Azure.

```bash
.venv/bin/python -m scripts.upgrade_catalogue \
  --inputs .azure/private-delivery.json --reuse rg-gg-dev-existing --plan
.venv/bin/python -m scripts.upgrade_catalogue \
  --inputs .azure/private-delivery.json --reuse rg-gg-dev-existing
```

The command requires an existing target; it has no `--new` option. Its `--plan`
prints the local sequence only, not an Azure preview or acceptance result. The
explicit upgrade shares delivery primitives but alone defers strategy
initialization until compatible workloads have rolled out. Subsequent ordinary
deployments use `scripts.deploy` again.

Foundry delivery uses a dedicated, certificate-verified P-256 TLS transport
(`IMP-FOUNDRY-DELIVERY-TLS`). This avoids an observed connection reset when
OpenSSL 3.5 offers hybrid key shares, without disabling TLS 1.3, changing the
endpoint, bypassing certificates or changing machine-wide OpenSSL settings.
Explicit CA bundles, client certificates and proxy settings remain supported.
Foundry request/response transport failures stop delivery and finalize the
per-attempt receipt as failed at the current stage. If bounded bootstrap fails,
foundation/image work may already have completed, but catalogue migration has
not yet run; inspect the receipt before retrying against the same target.
Azure ACR `dtu` reproduced eight missing-transport/error-handling regressions;
`dtv` passed 23 focused TLS, failed-receipt, tenant and writer/upgrade regressions.
Read-only Azure SDK version listings then succeeded for all six delivery agents
using the corrected client. This validation did not run a deployment or catalogue
migration and is not live channel acceptance.

The reviewed `catalogue/source_upgrade_v1.json` explicitly
describes the source 1.1.0 catalogue, its 1.2 expert priors and the exact
target 1.1.1 catalogue/1.3 priors hashes. Approval
`HR-PENSION-WTP-RANKING-COVERAGE-2026-11-18` covers all 200 supported explanation
contexts and the existing `approved_priors` upgrade policy. Strategy definitions
and versions are unchanged. The upgrade archives the old state and resets live
ordering to the approved expert baseline; it does not preserve learned ordering
in the active snapshot. Suspensions and protective pauses are retained.
The reviewed predecessor is exact: an older or owner-managed catalogue is not
silently treated as 1.1.0.

Only this explicit upgrade first rolls out compatible workloads. During that rollout, the Strategy
Service may serve the exact approved predecessor catalogue only after verifying
its existing snapshots and expert baseline; unknown or mixed versions still fail
closed. The predecessor lookup is lazy and only used for mismatched stored
versions, not empty state or snapshots matching the running source catalogue.
Then the existing P2 bootstrap identity signs both the target expert
snapshot and the exact upgrade plan, after checking the matching release
approval. The separate Strategy Service bootstrap identity verifies them,
archives the complete predecessor state and signed upgrade as an immutable
`catalogue-upgrades/<digest>.json` blob in the existing ranking container, then
conditionally replaces the state manifest using its observed ETag.

The new current and static snapshots are the approved target expert baseline.
The old last-good snapshot remains in the archived state and immutable blobs,
not an incompatible live rollback slot. Suspensions and protective owner pauses
remain unchanged. A freeze, static override, owner catalogue, pending catalogue
decision, widening owner override, invalid signature, archive failure or
concurrent update blocks migration. No unfreeze, reinstatement, deletion,
identity or permission grant is part of the upgrade.

A prepared archive alone is not activation evidence: the conditional write may
still lose a race. Successful upgrade delivery records `migrated`, the snapshot ID,
decision reference and predecessor archive in its existing receipt. Rerunning
after a completed upgrade preserves subsequent learning rather than resetting
it again. Do not delete snapshots, clear suspensions or use `--new` to bypass a
blocked upgrade. Writer approval and source changes alone are not proof that
the migration or its Azure validation has completed.

Without target flags, an interactive terminal queries resource groups in the
configured subscription and offers existing GovernedGraph deployments or a new
deployment. Enter accepts the previously selected group when it is still
available; otherwise the default is a new deployment.

Only groups tagged `workload=gg-journey-graph`, `azd-env-name=gg-dev` and
`data=synthetic` in swedencentral are selectable. Service-managed child groups,
including the Container Apps infrastructure group, are excluded. Existing
deployments retain the exact original seed and group spelling from successful
subscription deployment parameters. Failed redeployments cannot override that
identity. Saved scope may resume a first, incomplete deployment only when the
observed parameters agree exactly. Missing or conflicting identities fail closed.
Never normalize an existing seed's UUID formatting or casing: Bicep hashes its
literal text, so equivalent UUID spellings produce different Azure resource names.
The script never substitutes a fresh seed for an existing deployment.

Choosing new, or passing `--new`, generates a fresh UUID seed and a group named
`rg-gg-dev-<suffix>`. The suffix is the first 12 hexadecimal characters of SHA-256
over the UUID's 16 bytes. The script confirms that group does not already exist.
Both target fields are atomically saved in the private inputs before delivery;
the file is restricted to its owner. No seed or group needs to be supplied by hand.

When switching targets, the previous local azd environment is moved intact to
`.azure/archived/<unique-id>/gg-dev/`. Its Azure resources and data are untouched.
Selecting an older target restores its scope into a fresh active environment;
the archive remains available. Subscription deployment names include a
group-derived suffix plus the unique delivery attempt/operation suffix, retaining
seed-bearing records across targets and attempts. Discovery reads successful
deployment parameters, never parses resource identity from deployment names.
This is target identity and diagnostic evidence, not a resumable execution ledger.

For automation, use `--reuse RESOURCE_GROUP` or `--new`; they are mutually
exclusive and do not prompt. Non-interactive delivery without either flag fails
before querying Azure. Discovery failures never become an empty-list fallback.

### Command hangs and interruption

Every ACR repository lookup, image build and digest read explicitly uses the
selected delivery subscription, for both application and ML registries. The
global Azure CLI default can belong to a different tenant; it must not select
registry authentication or build scope. No `az account set` is required.

Entra bootstrap also uses the selected subscription's tenant. Microsoft Graph
requests use a token acquired with `az account get-access-token --subscription`
and keep it in process memory, never command arguments or files. Merely adding
`--subscription` to `az rest` does not reliably select a Graph tenant. All
registration helpers share this transport; Strategy identities are resolved
before any application or assignment changes. Standalone registration tools
require `PYTHONPATH=src:.`; export `AZURE_SUBSCRIPTION_ID` to select their tenant.

If bounded bootstrap reports `AADSTS53003`, Microsoft Entra Conditional Access
denied the operation. Successful ARM deployment or Foundry access does not prove
the Graph operation is allowed. Do not retry on a timer: have an authorized
tenant operator find the event in **Entra ID > Monitoring & health > Sign-in
logs** using the reported UTC timestamp and correlation ID, and check its
**Conditional Access** tab for the failed policy. Once an approved sign-in
context is available, rerun with the same inputs and `--reuse RESOURCE_GROUP`.
The printed registration plan does not confirm that an app or its assignments
were created.

Foundry agent bootstrap, standalone agent provisioning and writer verification
resolve that subscription's tenant explicitly and bind every
`DefaultAzureCredential` token request to it, including refreshes. The environment
variable alone does not scope SDK authentication. Existing operator Foundry
permissions are reused; delivery never grants access to an unrelated CLI identity
or changes the global account. Standalone agent provisioning requires
`AZURE_SUBSCRIPTION_ID`; `scripts.azd_env export --task agents` includes it.

Delivery binds all four required hosted agent names explicitly to the workload contract,
including `gg-counsellor` and `gg-fairness-explainability`. Ambient agent-name
settings in the invoking shell cannot redirect deployment planning, version
creation or fairness endpoint pinning. standalone provisioning still honors its
configured names. Writer approval checks both the exact agent name and immutable
version; old differently named agents are retained, never renamed or deleted.

CLI commands print their operation, attempt number and timeout to stderr, with
a heartbeat every 30 seconds. Arguments, environment values and captured command
output are not streamed, keeping private configuration and job logs out of progress
messages. Parsed stdout remains available to the delivery driver.

Each command attempt has a wall-clock limit: 120 seconds normally, 60 minutes
for an ACR build and 120 minutes for an ARM deployment. Only explicitly listed
read-only operations (account/group/deployment discovery, registry metadata, application/job status/logs and private-link
listing) retry timeouts: at most three attempts, with 2/4-second backoff. Nonzero
command exits are not retried by this policy. Separately, ARM delivery retries
the known managed-identity image-pull propagation error and, in the foundation
only, a `RequestConflict` reporting another operation on a Foundry account.
These retries wait 30, 60, and 120 seconds. Unclassified private endpoint
failures are not retried automatically.

**Decision `IMP-FOUNDRY-WRITE-ORDER`:** finish the Foundry model deployments
before creating or updating the project under the same account. This avoids
competing parent-account writes without a fixed delay on successful deployments.

A timeout or Ctrl+C kills and reaps the local command process group, not Azure
operations already submitted. Writes, builds, job starts and unknown commands
are never automatically retried after a timeout. Delivery fails explicitly;
Ctrl+C exits with status 130 without a traceback. Inspect the original Azure
deployment, ACR run or job execution before rerunning, because stopping the
local CLI does not establish that the remote operation failed or stopped.

The private delivery JSON accepts these fields. The identifiers below
are illustrative placeholders, not an Azure deployment:

```json
{
  "environment": "gg-dev",
  "subscription": "00000000-0000-0000-0000-000000000001",
  "on_call_email": "synthetic@example.invalid",
  "composition": "examples/pension_wtp/deployment.json",
  "location": "swedencentral",
  "search_location": "swedencentral",
  "voice_demo": true
}
```

Subscription, on-call destination and composition are required.
`operator_id` is optional: omit it or set it to `""` to resolve the signed-in
deploying user's tenant-local object ID live through Microsoft Graph `/me`.
The lookup uses the selected subscription's tenant, not the ambient CLI
subscription, and runs before environment writes or foundation provisioning.
The delivery entry point adds the repository's source paths before importing
framework helpers; no separate editable package installation is required.
`--plan` never performs this lookup. A supplied UUID remains unchanged; a stale
or incorrect supplied ID is not silently replaced. To use discovery instead,
remove that field from the private file.

The resolved ID is used consistently for the existing developer RBAC,
recovery operator and demo/admin operator configuration. It is not written back
to the private input file, so an omitted ID is resolved again on each applied
delivery. Lookup failure or missing delegated user context stops delivery
explicitly; there is no fallback to an application identity or home-tenant ID.
No new roles or permissions are introduced.

`naming_seed` and `resource_group` are optional saved target fields, populated
by deployment selection. Existing private files containing them remain supported.
Other optional fields default to the approved environment/regions and an
enabled presenter. Unknown fields are rejected. Store actual identifiers in
private ignored configuration, never source control. Reusing an existing target
preserves its seed, group and subscription; accidental scope changes are refused.
`voice_demo: false` disables the presenter, synthetic capture and console trace
lookup together. The console still uses its own reviewed sign-in.

The extension manifest contains only its name, synthetic watermark, approved
data directory, release-approval file, overlay Dockerfile, workload Bicep
composition, values application module and explicit domain environment. It
cannot declare tests, evaluation policy, learning proofs or operator recovery.

## Templates and fixed sequence

1. **`infra/main.bicep`: foundation once.** Networking, identities, registry,
   Container Apps environment, storage, monitoring, signing keys, Search,
   Foundry project/models, APIM parent and resource-scoped RBAC.
   No hello-world images, member routes or prompt-agent ARM resources.
2. **Immutable images.** `Dockerfile` and `Dockerfile.p2` build framework
   runtime and P2 respectively. The extension's matching Dockerfiles build
   `runtime`/`probe` and `p2`/`fixtures` from their own framework digest references.
   Separate dependency families keep runtime and learning SDKs isolated. Identical
   extension runtime images serve all five applications. All images use the same
   application registry with role-scoped managed identity pull access. Each distinct target
   is built once, or its source-keyed immutable image is reused.
3. **Bounded bootstrap.** Exact prompt definitions are created/reused with
   `AIProjectClient.agents.create_version`, DefaultAzureCredential and pinned
   `azure-ai-projects==2.9.0`; matching definitions reuse numeric versions.
   Entra applications/groups preserve browser self-scopes and self-preauthorization.
   Only the exact Search indexer private-link request is approved.
   `infra/bootstrap.bicep` declares the existing isolated content publisher,
   dev-issuer and dev-fixture jobs. `infra/bootstrap-job.bicep` is applied only
   for the P2 signing and Strategy initialization jobs, never the foundation.
   Private key/content/state operations run inside the existing role contracts.
   The Strategy initializer receives the existing Entra-authenticated Application
   Insights routing and Foundry project ARM ID for content-free conditional-read
   diagnostics. It uses the existing Strategy identity and telemetry permission;
   the isolated P2 signing job receives no new telemetry authority. A valid
   existing state is preserved, including learned rankings and protective state.
4. **`examples/pension_wtp/infra/workloads.bicep`: complete workloads once.**
   This explicit domain composition calls `infra/workloads.bicep` with immutable
   images, approved writer record, actual agent pins and complete domain inputs.
   Native fairness and learning schedules run at the existing four-hour cadence
   from the first release, using the guarded lease/same-cycle-audit runtime.
   Bicep creates the dedicated `p2-artifacts` container in private content storage.
   Only the two isolated job identities receive artifact-container access; the
   independent fairness audit, cohort and signing permissions are unchanged.
   The pipeline reads learning aggregates through a container-scoped reader role.
   The synthetic values adapter has its own identity with registry pull only;
   it never inherits the orchestrator's key/storage/Foundry permissions.
   Optional developer Blob access is limited to the approved-content container;
   deployment does not grant developer access to the shared storage account.
      A provider rejection specifically identifying managed-identity image pull
      propagation is retried at most three times (30/60/120 seconds), using the
      same template and parameters. Other failures are not retried.
5. **Readiness only.** Check running application provisioning, enabled native
   schedules and the console page; retain `.run/delivery.json` and print URLs/IDs.
   No regression suite, demo, paid evaluation, conversational proof or manual
   learning-cycle acceptance occurs during delivery.

## Artifacts, isolation and redeployment

Framework images contain no extension files or default pension dataset.
Extension runtime/P2 images contain approved catalogue/content/thresholds;
only runtime adds the synthetic deterministic adapter/member records.
Cohort aggregates and outcome fixtures belong solely to the fixture image;
raw persona profiles belong solely to the isolated builder/non-member-facing probe.
The sample image build uses an isolated builder to project the persona fixtures
into closed live-simulation scripts. Only that profile-free projection reaches
the runtime image; raw persona profiles remain in the builder/probe, never the
counsellor payload. Runtime and P2 dependency manifests remain isolated and are installed once in
their respective framework targets.

The demo opens with a single conversation surface. Sign in, then use **Voice**,
**Chat**, or the story selector and **Play** on one toolbar. Start controls stay
visible but disabled during a conversation; recording controls appear when needed.
**Talk to a person** stays available after sign-in. Conversation captions scroll without
moving the question composer. Personas play live in that same conversation log: an AI actor with its own voice and identity
plays each persona over voice or chat, and **Take over as the member** continues the same journey. With the voice
demo enabled the delivery process creates the pinned persona agent and then grants the dedicated persona identity
access to that one agent only; no recorded story is replayed. Video export is an optional download under the closed
**Behind the conversation** section, not an automatic download. Every guide reply carries an **i** badge: select it to
see how that reply was produced (what the guide did, the draft checks and the outcome), built from closed codes only
and never carrying conversation text (B4/B7). When a conversation ends, **How this conversation went** consolidates
every reply into one list. When no
safe, relevant answer exists the guide asks one follow-up question before offering an advisor; an
advisor is always reachable (B9). Errors appear once in the conversation area; sanitized
failure type and location remain in the browser console. The final audit report retries
only the explicit journey-busy response, with bounded waits; persistent contention and
other failures still prevent successful completion. AI disclosure, the
**Synthetic data** label, authentication and channel guardrails remain required.

To inspect the presenter after redeployment, select the environment explicitly:

```bash
.venv/bin/python -m scripts.azd_env show --environment gg-dev --task presenter
```

Without `--environment`, the helper uses azd's selected environment, which may
belong to a different project. A configured presenter URL from a previous
delivery does not establish that a failed redeployment completed.

Build metadata retained in the public delivery receipt includes immutable image
digest, target, Dockerfile, source SHA-256 and framework base references. OCI
`org.opencontainers.image.revision` carries the source SHA. These are artifact
identities, not step checkpoints. The driver never reads a per-step ledger.

Required public job outputs are the member **public RSA JWK** (never a token or
private key), `GG_BOOTSTRAP={"snapshot": ...}` from the isolated signer, and
`GG_BOOTSTRAP={"initialized": true|false, "snapshot_id": ...}` from the Strategy
initializer, plus actual successful execution IDs. Missing, unknown, timed-out
or failed job status never becomes successful bootstrap evidence.

Initial priors are signature/catalogue verified and created only if shared
Strategy state is absent. Invalid existing state fails closed. Ordinary
redeployment never replaces learned rankings, approvals, suspensions, freeze
state or existing upstream fixtures/content. Fixture initialization fills only
missing inputs; concurrent upstream publication wins. Content publication
retains the authoritative approved feed. No recovery is attempted by delivery.

## Evaluation and operator tools

Hosted evaluation validates exact deployed writer/semantic definitions, retains
incremental case/remote evidence and enforces deterministic hard gates before
claiming a pass: exit 0 passed, 1 failed, 2 unavailable. The retained harness uses in-process
deterministic adapters and fixed synthetic cases, **not** the real channel.
See [evaluation](evaluation.md).

For explicitly authorized live-channel checks, use `scripts.run_dev_proof`
under the existing in-network dev-issuer job; select the receipt's `probe` image
for voice. Tokens remain in probe memory and expire within fifteen minutes.
Never issue member tokens or grant developer Key Vault access locally.

Operational tools include `scripts.run_p2_container_jobs` for inspecting
an exact operator cycle, `scripts.configure_strategy_recovery` for
existing restricted operator access, and `scripts.lift_dev_freeze` for
personally authorized audited recovery. Unknown status is never completion;
failed watchdogs are not resumed/replaced, and reinstatement still requires
the existing human board decision. These tools do not finalize deployment.

`python -m scripts.teardown --plan` inventories the exact scoped deployment.
Explicit `--subscription`, `--resource-group`, `--seed` remain supported for
historical deployments. `.run/deploy-state.json` is read only for historical
scope/archive ownership; teardown imports no deployment classes. Child-first
Search cleanup, managed-network deletion waits, protected tombstones and
azd/history archival preserve deployment scope and evidence.

## Delivery test coverage

Coverage includes explicit-input/closed-approval validation, immutable build reuse,
foundation/bootstrap/workload composition, unknown-status failure, signature
gates, byte-for-byte protective-state preservation and image isolation.
Existing runtime B1-B10/GNA/disclosure and operator recovery tests remain
controlling. Pension composition/selection/case
tests live under `examples/pension_wtp/tests`; framework tests live in `tests`.
Run them only in the separately authorized Azure validation environment; this
description is not an executed test result.
