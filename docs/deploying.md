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
| Private input | Create `.azure/private-delivery.json` from the placeholder example below; provide subscription, operator ID, on-call destination and composition. Keep actual identities and all receipts outside Git. The script selects/generates resource-group and naming-seed information as described below. |
| Budget and cleanup | Estimate recurring charges and set a budget. The sample can provision Foundry model deployments, AI Search, Container Apps, ACR, APIM, Key Vault, Storage, Service Bus, Azure ML, networking, Content Safety and monitoring. Live inference, voice, evaluations, retained telemetry and idle SKUs may all cost money. Read the teardown section before provisioning. |

Prices are deployment-dependent: **no cost or latency figure in the repository should be treated as an Azure quote**. Use the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) and your subscription's actual regional quotas/SKUs. The default reference example and its sample member data are synthetic; do not substitute real members or production records without a separate security, privacy and governance assessment.

For an externally visible release, also complete the [publication checklist](public-release-checklist.md). Deployment readiness alone never authorizes production use.

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

After successful readiness checks, the final JSON result is followed by
`Demo URL: https://<gateway>/demo/` and `Admin URL: https://<gateway>/admin/`
for convenient access. When `voice_demo` is false, the summary prints
`Demo URL: disabled` instead.

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
the archive remains available. Subscription deployment names include a stable
group-derived suffix so new deployments do not overwrite another target's
seed-bearing deployment record. This is target identity, not a per-step ledger.

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

Foundry agent bootstrap, standalone agent provisioning and writer verification
resolve that subscription's tenant explicitly and bind every
`DefaultAzureCredential` token request to it, including refreshes. The environment
variable alone does not scope SDK authentication. Existing operator Foundry
permissions are reused; delivery never grants access to an unrelated CLI identity
or changes the global account. Standalone agent provisioning requires
`AZURE_SUBSCRIPTION_ID`; `scripts.azd_env export --task agents` includes it.

Delivery binds all six agent names explicitly to the workload contract,
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
command exits are not retried by this policy. The existing bounded
managed-identity image-pull propagation retry remains separate.

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
  "operator_id": "00000000-0000-0000-0000-000000000003",
  "on_call_email": "synthetic@example.invalid",
  "composition": "examples/pension_wtp/deployment.json",
  "location": "swedencentral",
  "search_location": "swedencentral",
  "voice_demo": true
}
```

Subscription, operator, on-call destination and composition are required.
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
   Foundry project/models, APIM parent and dedicated Azure ML dependencies/RBAC.
   No hello-world images, member routes or prompt-agent ARM resources.
2. **Immutable images.** `Dockerfile` and `Dockerfile.p2` build framework
   runtime and P2 respectively. The extension's matching Dockerfiles build
   `runtime`/`probe` and `p2`/`fixtures` from their own framework digest references.
   Separate dependency families keep runtime and learning SDKs isolated. Identical
   extension runtime images serve all five applications. P2/fixtures use the
   ML registry; runtime/probe use the application registry. Each distinct target
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
   ML datastore names are read without secrets; Bicep grants only verified
   dedicated-container access, including the scheduler's `azureml` container.
   ML-owned dependencies and their automatic permissions are retained.
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
