# Delivering GovernedGraph

The reference composition is **gg-dev**, swedencentral, with dedicated keyless
Content Safety in francecentral. All domain data is **ILLUSTRATIVE - synthetic
data**. Deployment readiness is not evaluation, conversational acceptance,
production approval or human recovery.

## Commands and explicit inputs

Use Python 3.14, Azure CLI, Azure Developer CLI, Bicep and the existing verified
`requirements-azure.txt` environment:

```bash
.venv/bin/python -m scripts.deploy --inputs .azure/private-delivery.json --plan
.venv/bin/python -m scripts.deploy --inputs .azure/private-delivery.json
.venv/bin/python -m scripts.run_foundry_evals --environment gg-dev \
  --output .run/foundry-evaluation-new.json
```

`--plan` validates the local input/composition contract and prints the sequence;
it makes no cloud calls. Obtain the required authorized Azure preview before
applying the delivery. Delivery prints each major operation and each image
build/reuse, without exposing private inputs. The evaluation command is separate and incurs model and
judge usage; it never provisions, starts channel probes or issues member tokens.
An output file must be new, preserving previous evidence.

The private delivery JSON contains exactly these fields. The identifiers below
are illustrative placeholders, not an Azure deployment:

```json
{
  "environment": "gg-dev",
  "subscription": "00000000-0000-0000-0000-000000000001",
  "resource_group": "rg-gg-dev-your-unique-name",
  "naming_seed": "00000000-0000-0000-0000-000000000002",
  "operator_id": "00000000-0000-0000-0000-000000000003",
  "on_call_email": "synthetic@example.invalid",
  "composition": "examples/pension_wtp/deployment.json",
  "location": "swedencentral",
  "search_location": "swedencentral",
  "voice_demo": true
}
```

Subscription, group, seed, operator, on-call destination and composition are
required. Optional fields default to the approved environment/regions and an
enabled presenter. Unknown fields are rejected. Store actual identifiers in
private ignored configuration, never source control. Preserve the exact seed,
group and subscription on redeploy; a conflicting azd environment is refused.
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
   Separate dependency families prevent ACR's classic builder from processing
   unrelated private-registry stages or installing the other family's SDKs. Identical
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
   No pytest, demo, paid evaluation, conversational proof or operator-run
   nine-stage learning cycle occurs during delivery.

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
claiming a pass: exit 0 passed, 1 failed, 2 unavailable. It uses local
deterministic adapters and fixed synthetic cases, **not** the real channel.
See [evaluation](evaluation.md).

For explicitly authorized live-channel checks, use `scripts.run_dev_proof`
under the existing in-network dev-issuer job; select the receipt's `probe` image
for voice. Tokens remain in probe memory and expire within fifteen minutes.
Never issue member tokens or grant developer Key Vault access locally.

Genuine operational APIs remain: `scripts.run_p2_container_jobs` for inspecting
an exact legacy operator cycle, `scripts.configure_strategy_recovery` for
existing restricted operator access, and `scripts.lift_dev_freeze` for
personally authorized audited recovery. Unknown status is never completion;
failed watchdogs are not resumed/replaced, and reinstatement still requires
the existing human board decision. These tools do not finalize deployment.

`python -m scripts.teardown --plan` inventories the exact scoped deployment.
Explicit `--subscription`, `--resource-group`, `--seed` remain supported for
historical deployments. `.run/deploy-state.json` is read only for historical
scope/archive ownership; teardown imports no deployment classes. Child-first
Search cleanup, managed-network deletion waits, protected tombstones and
azd/history archival remain intact. No legacy fresh/resume or acceptance wrapper
is retained.

## Delivery test coverage

Obsolete delivery state-machine, multi-provision, recovery-handoff and automatic
acceptance mock contracts have been replaced, not retained through shims.
Coverage is now explicit-input/closed-approval validation, immutable build reuse,
foundation/bootstrap/workload composition, unknown-status failure, signature
gates, byte-for-byte protective-state preservation and image isolation.
Existing runtime B1-B10/GNA/disclosure and operator recovery tests remain
controlling; infrastructure/image assertions follow the relocated enforcement
points with equivalent or stronger checks. Pension composition/selection/case
tests live under `examples/pension_wtp/tests`; framework tests live in `tests`.
