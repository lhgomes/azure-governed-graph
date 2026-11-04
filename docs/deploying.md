# Deploying GovernedGraph to a new resource group

This deploys the reference solution (framework plus the bundled pension sample) with the Azure Developer CLI. It is a **dev/reference
deployment**: it includes synthetic stand-ins for the member identity provider and the contact centre, and several features are
restricted to an environment named exactly `gg-dev`. Do not use it as a production deployment.

## Automated deployment (recommended)

One command selects the bundled sample, runs its declared acceptance checks and deploys it, resuming where it stopped
if anything fails. Separate evaluation, demo and proof commands are not required for the normal deployment:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-azure.txt
python -m scripts.deploy
```

It first asks whether to **start fresh**, **redeploy/update**, or **resume**. It then asks for the **subscription**, **region**,
**resource group name**, an **on-call email** and whether to enable the browser
voice presenter, shows what it is about to do (duration and cost included), and waits for your confirmation. Then it runs:
the sample's local tests, evaluation hard gates and offline rehearsal, then preflight checks
(tools, sign-in, permissions, resource providers, model quota, existing resource group), the azd environment, the
first provision, the Search private-link approval, the Entra app registrations, delegated recovery access for the saved operator,
the Foundry agents, the service deployment, a second
provision, the member-token key bootstrap, a final provision, the learning plane and the sample's channel and voice-text
verification.
The voice proof image is remotely built and resolved to an immutable digest automatically; no local Docker or
separate image-building command is needed. Expect 45-90 minutes plus sample verification/build time.

### Sample-owned verification

`examples/pension_wtp/deployment.json` owns the local check targets, sample evaluation/demo modules, data directory and
required deployed channel modes. The pension cases and runner live in `examples/pension_wtp/evaluation/`; its coverage
tests live in `examples/pension_wtp/tests/`. The root evaluation command remains a compatibility entry point, not a
second implementation. Framework boundary tests remain in `tests/`, unchanged and included in the full test suite.

The default command uses this definition automatically. `--plan` shows its checks; `--sample-definition
examples/pension_wtp/deployment.json` selects it explicitly. A saved deployment cannot silently switch definitions.
Sample, runtime, dependency or verification-input changes invalidate the affected saved deployment steps and previous
proof receipts. Every declared proof must report `Succeeded` before deployment completion; an old channel-only success
cannot skip the sample's voice proof. Selecting a repair step does not bypass missing local sample acceptance.

Local acceptance failures stop before fresh Azure provisioning. Do not weaken checks or call a partially accepted sample
fully available.
The bundled sample's pre-provision checks include the isolated P2 stages, sorted-key JSON hand-offs, execution-history
pagination, fairness-agent invocation and scheduled-learning contracts. A regression in those paths stops locally before
starting a cloud learning job, not only after provisioning. Voice bridge and hosted simulation checks also run here,
including paraphrased teach-back questions and the unchanged resolved-state acceptance gate.
These offline checks are not a claim of live Azure acceptance.
The in-network channel proof's authentication, idempotency, handover and audit regressions also run before provisioning.

### Learning plane (automatic)

After the final provision, the `learning` step remotely builds the immutable P2 image (and, for the sample's synthetic dev
inputs, the fixture-publisher image) into the deployment's dedicated Azure ML registry, publishes the synthetic aggregates,
runs one complete P2 cycle on the Azure ML hosting jobs and, when the sample declares it, runs the protective fairness agent
once on its isolated fairness job. Only after both succeed does it enable the four-hourly schedule and re-provision. A failed
cycle or agent proof leaves the schedule off and the step pending; `--mode resume` retries only this step. Nothing is run by
hand. The runbook's recurrence checks (two scheduled cycles and a skipped-audit freeze-and-alert proof) are observations made
over time and are not claimed by the script.

P2 job artifacts use sorted-key JSON. Candidate verification matches complete rows by request key, independent of the
order of those rows; each row's strategy order, scores, baseline and shift must still match exactly. Duplicate, missing
or modified rows fail closed. A cycle already frozen by a failed stage is not unfrozen by a code fix or resume: the
existing audited human recovery remains required, and no schedule or completed learning receipt is issued on failure.
The watchdog and recovery audit lookup read every execution-history page, rather than treating ordinary pagination as a
failure. Continuation links must remain on the same HTTPS ARM job endpoint and must not repeat; malformed pages or a
failed component on any page still fail closed.

### Recovery operator access (automatic)

The `recovery-access` step runs after Entra registration and before agent deployment or cloud learning acceptance. It
configures the existing delegated `Strategy.Recover` scope, Azure CLI preauthorization and direct enterprise-application
access for the saved deployment operator only. Assignment remains required; no new application role, credential,
workload recovery authority or broad Azure RBAC grant is created. This setup does not lift a freeze.

Existing deployments missing this step run it on resume. To repair operator access without running the learning cycle:

```bash
python -m scripts.deploy --mode resume --only recovery-access
```

`AADSTS50105` means the operator is not assigned access; signing in again does not create that assignment. The repair
requires an authorized application owner or Entra administrator. If directory permissions are insufficient, deployment
stops explicitly; it never disables assignment-required or grants access to a different human. After setup succeeds, the
operator personally runs recovery and then resumes deployment. No fresh deployment is needed.
Entra assignment propagation can briefly outlast the successful setup command. The human-run recovery helper retries
only `AADSTS50105` token-acquisition failures, at most five times with 60-second waits; other authentication failures
stop immediately. It never submits recovery without a token, changes an assignment or weakens authorization.
Strategy API re-registration preserves delegated scopes, CLI preauthorization and the configured operator's existing
Default Access assignment. It never creates human access itself, and foreign users/groups/workloads still fail the grant check.

### Release approval (one human decision per release)

The bundled initial synthetic sample is already authorized by the release owner as `HR-PENSION-WTP-INITIAL`.
Run the normal deployment command; do not create a new approval for each environment or hand-edit version IDs.
Its exact scope is recorded in [the initial sample release decision](evaluation.md#initial-synthetic-sample-release).

Generated answers are released only for writer definitions that a human has approved at Tier 2 (B8). The approval belongs to
the **release**, not to a deployment, and never mentions a version number: version numbers are project-local and a fresh
Foundry project starts at `1`. It is the sample-owned file `examples/pension_wtp/release-approval.json`, committed through
normal review:

```json
{"tier": "tier2", "approved": true, "decision_ref": "HR-<human release decision>", "evidence_ref": "<decision/evidence reference>",
 "writers": {"gg-counsellor": {"definition_sha256": "<digest>"}, "gg-counsellor-voice-compact": {"definition_sha256": "<digest>"}}}
```

The shipped file records this initial authorization and the digests of the current definitions. Each run computes the current
definitions' digests from the code and domain pack, then:

- **before provisioning**, stops with the exact digests to approve unless the file is approved and covers them (nothing is
  created, so an unapproved or stale release costs nothing);
- after the agents are created, verifies the **actual immutable Foundry definitions** against the approval, binds the approval
  to this deployment's project and new version pins, and applies it at the gateway in the second provision;
- keeps the writers suspended (HTTP 503) whenever the file is unapproved, the digests differ, or the live definitions do not
  match, so a changed prompt, schema, model or pack needs a new approval and a repin can never revive a rejected writer.

Creating agents, passing tests or a successful deployment never approve anything; only the recorded human decision does.
`HR-` identifies a human release-owner decision; `GB-` remains supported for governance-board decisions. Neither reference
is generated by deployment. Board-only strategy reinstatement and delegated recovery requirements are unchanged. The
authority and truth of the decision are the approver's responsibility: the script verifies binding, not who decided. To
revoke, set `approved` to `false` and run `python -m scripts.deploy --mode resume --only agents`, then
`--mode resume --only provision-2`; the gateway approval is cleared and generated answers are suspended again. Because the
hold is protective, revocation never needs a new approval.

The azd parameters file passes `GOVERNEDGRAPH_WRITER_APPROVAL_JSON` as a typed `writerApproval` object, not as JSON
interpolated into a string. Bicep serializes it only at the gateway module boundary. Missing or cleared approval resolves
to `null` and keeps writers suspended; existing azd environments need no manual conversion. After a parameter-parsing
failure, use `python -m scripts.deploy --mode resume`, not a new fresh deployment.

| Command | What it does |
|---|---|
| `python -m scripts.deploy --plan` | Shows the answers and the steps; runs nothing. |
| `python -m scripts.deploy --status` | Shows the saved answers and which steps are done. |
| `python -m scripts.deploy --mode fresh` | Starts a separate deployment with a new persisted random naming seed and a new resource group. Old resources remain and may incur costs. |
| `python -m scripts.deploy --mode resume` | After a failure, keeps the saved names and skips completed steps. |
| `python -m scripts.deploy --mode redeploy` | Re-runs all steps against the saved deployment, keeping its names, group and seed. |
| `python -m scripts.deploy --mode resume --recover-soft-deleted` | Explicitly opts into scoped recovery of that deployment's soft-deleted resources; never purges. |
| `python -m scripts.deploy --from-step agents` / `--only member-key` / `--force` | Re-run a step, a single step, or everything. |
| `python -m scripts.deploy --mode fresh --yes --non-interactive --subscription <id> --location <region> --resource-group <new-name> --on-call-email <addr> [--voice-demo]` | Unattended fresh deployment. Without an explicit mode, unattended runs default to fresh only when no state exists, otherwise resume. |

### Fresh versus existing deployments

**Fresh** generates one random naming seed, saved in `.run/deploy-state.json` and the azd environment as
`GOVERNEDGRAPH_NAMING_SEED`. Bicep derives resource-name suffixes from it; the three Entra registrations are seed-scoped too.
The default resource group is `rg-gg-dev-<random suffix>`, or you can supply another **new** group. An existing group is refused
even with `--ignore-warnings`, because fixed-name jobs and other group-scoped resources must not overwrite an older deployment.
Fresh does not delete, purge, recover, migrate or reuse old Azure resources or app registrations.

After you confirm a fresh start, any previous local state is saved under `.run/deploy-history/<new-seed>/state.json`.
Before initializing azd, its previous `.azure/gg-dev` directory is moved to the same history directory as `azd-environment`.
This preserves the old local configuration without carrying old images, client IDs, public keys or outputs into the new
deployment. Exported settings from an older deployment are also excluded from the new deployment's parameter environment.
Local definition plans and sample checks also override inherited agent names and model-deployment settings with the
reference defaults. After environment initialization, agent helpers use the selected azd configuration, with explicit
defaults for settings it does not export. A stale shell cannot silently rename a planned agent or change its digest.
Unexpected agent names and missing/invalid digests are reported separately; neither bypasses release approval.
Cancelling the fresh confirmation leaves existing state and configuration unchanged.

**Resume** and **redeploy** never generate a seed. They preserve the saved seed and group; legacy state with no seed keeps the
original deterministic naming formula. A seed mismatch between saved state and azd stops the run instead of renaming resources.
Use **resume** after a failed fresh attempt; repeating `--mode fresh` deliberately starts another deployment.
Redeploy re-runs every step; resume can also use `--only`, `--from-step`, or `--force`.

The history is local, ignored by Git, and is not an Azure backup. To operate an older deployment again, first preserve the
current local state/environment, then restore both that older deployment's archived state and azd directory to their original
paths. Do not mix state from one deployment with the environment of another. Old Azure resources keep incurring charges until
you separately review and delete them; fresh never performs that cleanup.

Answers are saved in `.run/deploy-state.json` (ignored by Git) and reused on the next run; `--reconfigure` asks again, but only before
the infrastructure is provisioned, because changing the resource group afterwards would create a second deployment. The script
explicitly scopes azd commands, hooks and verification to `gg-dev`, regardless of the selected environment or an inherited
`AZURE_ENV_NAME`. On resume it revalidates the environment and reapplies saved configuration even when the environment step
was previously completed. A different subscription, primary region or resource group in that environment stops the run rather
than redirecting deployment. Subscription-scoped Azure CLI operations and the proof job also use the saved subscription
explicitly. Tenant-level Entra queries, sign-in and subscription discovery do not receive an injected `--subscription`;
preflight selects the saved subscription before looking up the signed-in operator.
The synthetic contact-centre flag is set **before the first provision**, so its tagged Container App exists before
`azd deploy`. Before any remote builds, the script checks that each of the four services has exactly one tagged
Container App in the saved resource group and subscription. On resumes from older runs that omitted the contact centre,
it reapplies the infrastructure once and verifies the targets again, without rerunning Entra registration or agent creation.
Persistent missing targets, ambiguous tags, conflicting environment tags or an inventory failure stop before builds.
The final sample proofs use the existing in-network dev-issuer job. `ORCHESTRATOR_URL` remains the public APIM `/journey`
URL; the proof resolves the private orchestrator FQDN from the unique `gg-dev`/`orchestrator`-tagged Container App in the
saved resource group and subscription. Legacy direct Container App URLs are also supported. No token is minted locally,
no endpoint is guessed, and no new identity, permission or network path is created.
The channel proof retries only HTTP 409 responses with the API's documented journey-busy details:
at most six attempts with waits of 1, 2, 4, 8 and 16 seconds. Turn retries retain the exact idempotency key,
body and member authorization; advisor-queue and report reads use the same bounded handling.
Persistent contention, other conflicts, malformed errors, authentication failures and HTTP 503 still fail acceptance.
Failed turn, queue and report gates include the HTTP status, never error bodies or tokens.
Voice verification also respects B10 assignment: a control journey must use static S1 and pass all other
disclosure, suspension, numeric, resolution, audit and duration gates. It then tries another journey, up to five
in total, without changing assignment. Completion still requires a treatment journey selecting S5 and passing
every original gate. A failed gate, unknown arm or five control journeys stops verification.
Verification is complete only after every declared proof execution reports `Succeeded`; launch errors, failed jobs and timeouts stop
the script and leave `verify` pending. Resume retries verification without repeating completed deployment steps, including
older state incorrectly marked complete without a recorded successful channel-proof execution.
If a failed job no longer has replicas, retrieve its content-free PASS/FAIL diagnostics from the existing Log Analytics
workspace instead of repeatedly rerunning the proof. The Strategy Service client permits five seconds per transport phase
(previously one); timeouts and HTTP errors still propagate to the governed handover path without retries or substitute
rankings. A runtime source change requires rebuilding the services, even when the deployment checkpoint already passed.
Use `--mode resume --only deploy` followed by `--mode resume --only provision-2` to apply it without recreating agent versions.
The script
never reads, creates or prints a secret: the only key material it touches is a public key, which it refuses to store if it carries any
private parameter. One thing stays manual: **open the verification email sent to the on-call address and confirm it**.

### Managed-identity image-pull propagation

The synthetic fixture publisher has separate, bounded image-pull handling. When Azure rejects its exact configured
image using its dedicated identity, deployment verifies the image digest, registry authentication mode and existing
registry-scoped `AcrPull` assignment, then retries provisioning up to five times with a 60-second wait. ARM dependency
ordering does not guarantee a newly created grant has reached the registry data plane. Missing access, missing images,
incompatible registry settings, foreign targets, mixed errors and persistent failures stop; no grant, credential, image,
region or name is changed to bypass the failure. Resume keeps completed steps and existing resources.

### Regional capacity fallback

Search starts in the primary region, or the preferred EU region passed with `--search-location <region>`.
If provisioning returns `InsufficientResourcesAvailable`, the script checks the current ARM `data` deployment's operations
to confirm that **Search creation** failed for that reason. It checks that the target Search service does not already exist,
then retries supported EU regions in this order (skipping regions already tried):
`swedencentral`, `germanywestcentral`, `northeurope`, `westeurope`, `francecentral`, `polandcentral`, `italynorth`, `spaincentral`.
Provider metadata filters out unsupported regions; only a successful provisioning attempt proves capacity, which can change
between attempts. See [Search regional availability](https://learn.microsoft.com/azure/search/search-region-support).

Each switch is printed and saved in both the resume state and `AZURE_SEARCH_LOCATION`. Failed regions are remembered so a
resume does not cycle through them again. Repeating the original `--search-location` preference on resume keeps the saved
fallback rather than switching back. If all supported alternatives fail, the script stops; once capacity returns,
`--reconfigure` resets the attempts **before the first successful provision only**. Existing services, even ones in a failed
state, are never automatically moved or deleted. Such a service requires operator review.

Only Search has automatic regional fallback: the rest of the topology and all model deployments stay in their configured
regions. A mixed failure is not treated as a Search-only problem: other failed resources or error codes stop regional retries.
Quota, permissions, policy, name conflicts and capacity errors on other resources stop with the cause rather than
triggering an unsafe whole-deployment relocation. Diagnostic failures also stop without assuming a region is available.
Other usual causes are a resource provider still registering or the Search private link not appearing yet.

### Recoverable soft-deleted resource conflicts

`FlagMustBeSetForRestore` on an AI account and a Key Vault `ConflictError` mentioning a deleted vault are separate blockers,
not regional-capacity problems. By default the script stops and shows the blockers; recovery is **not automatic**.
Pass `--mode resume --recover-soft-deleted` to authorize recovery for that invocation. The flag is not saved or inherited
by future runs. The script then checks the exact failed targets against Azure's deleted-resource inventory.
It **recovers**, never purges, matching Key Vault and AI resources only when their original resource IDs,
`gg-dev` environment ownership tags and expected regions match the saved deployment. Key Vault is recovered first;
Content Safety stays in `francecentral`, and Foundry stays in the primary region. Recovery preserves stored data and keys.
The script verifies the recovered resource IDs before retrying provisioning; Bicep then reapplies its declared configuration
and role assignments. A repeated conflict, missing metadata, foreign ownership or insufficient recovery permissions stops
with an actionable error. Unknown names are not guessed or recreated.

Do **not** use `azd down --purge` as a generic fix for these errors: it deletes resources and data, and a purge-protected vault
cannot be purged during its retention period. Review resources from an older environment separately; the script will not
recover or modify a resource tagged for that other environment. If you want new resources instead of recovery, choose
`--mode fresh` and a new resource group.

The rest of this page documents what the script does, step by step, for review or for doing it by hand.

## The stages

The deployment is staged because some inputs exist only after the first provision (managed-identity principal IDs, the signing key
vault, the AI project). The stages are: **provision, approve, register, deploy, re-provision, bootstrap the token key, re-provision,
verify.**

## 0. Before you start

**Tools:** Azure CLI (`az`), Azure Developer CLI (`azd`), Python 3.14. Docker is not needed; images are built remotely in your registry.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-azure.txt
az login && azd auth login
```

**Permissions**
- Azure: the template deploys at subscription scope, creates the resource group and assigns roles, so you need Owner, or Contributor
  plus User Access Administrator, on the subscription.
- Entra ID: you must be able to create app registrations and assign app roles (for example Application Administrator and an
  assignment-capable role such as Cloud Application Administrator).

**Quota** in your region (default `swedencentral`): `gpt-4.1` and `gpt-4.1-mini` on DataZoneStandard (50 each) and
`text-embedding-3-large` on Standard (50). Check with `az cognitiveservices usage list -l swedencentral -o table`. Prompt Shields is
deployed as a separate keyless resource in `francecentral`.

**Providers:** make sure `Microsoft.App`, `Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.ApiManagement`,
`Microsoft.ContainerRegistry`, `Microsoft.MachineLearningServices`, `Microsoft.ServiceBus`, `Microsoft.KeyVault`,
`Microsoft.Storage`, `Microsoft.Network`, `Microsoft.Insights` and `Microsoft.OperationalInsights` are registered
(`az provider register -n <namespace>`).

**Cost.** The reference topology includes API Management (StandardV2), Azure AI Search, two Container Registries, Container Apps
(the orchestrator and Strategy service keep minimum replicas), an Azure ML workspace with private networking, Key Vaults, ZRS storage and
model deployments. It is not a free-tier deployment. Remove it with `azd down --purge` when finished.

### Clean up a previous environment (if you are replacing one)

```bash
az group delete -n <old-resource-group> --yes --no-wait   # the old resource group
# Optional: free model quota and names held by soft-deleted resources
az cognitiveservices account list-deleted -o table        # purge entries with: az cognitiveservices account purge ...
az keyvault list-deleted -o table                         # purge entries with: az keyvault purge ...
# Old Entra registrations (review the list before deleting anything)
az ad app list --all --query "[?contains(displayName,'<old-name-fragment>')].{name:displayName,appId:appId}" -o table
rm -rf .azure/<old-environment-name>                      # old local azd environment
```

The new environment uses new resource names (they derive from the subscription, resource group and environment name), so nothing from the old
one collides with it.

## 1. Create the azd environment

The environment **must be named `gg-dev`**: the dev token issuer, the voice demo, the synthetic contact centre and the proof scripts are
enabled only there. The resource group is `GOVERNEDGRAPH_RESOURCE_GROUP_NAME`, or `rg-gg-dev` if you leave it unset.

```bash
azd env new gg-dev --subscription <subscription-id> --location swedencentral
azd env set GOVERNEDGRAPH_RESOURCE_GROUP_NAME <resource-group-name>   # optional; default rg-gg-dev
azd env set ON_CALL_EMAIL <your-address>                  # alert receiver; you must confirm its verification email
azd env set GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER true   # creates the dev signing key and issuer job
azd env set MEMBER_TOKEN_ISSUER https://gg-dev.synthetic.invalid
```

For a genuinely fresh **manual** deployment, use a new resource group and set a naming seed **once**:

```bash
azd env set GOVERNEDGRAPH_NAMING_SEED "$(python -c 'from uuid import uuid4; print(uuid4().hex)')"
```

Keep that value for every retry and update. Never add or change a seed on an existing unseeded deployment: leaving it empty
preserves its legacy names. The automated script manages the seed and separate local deployment history for you.

The member-token **public** key is needed by the deployment, but the real key is created inside it. Supply a throwaway public key now
and replace it in step 7 (the private half is discarded; nothing verifies against this key until it is replaced):

```bash
azd env set MEMBER_TOKEN_PUBLIC_JWK "$(python - <<'PY'
import json
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
jwk = json.loads(RSAAlgorithm.to_jwk(rsa.generate_private_key(65537, 2048).public_key()))
jwk.update(alg="RS256", use="sig")
print(json.dumps(jwk))
PY
)"
```

## 2. First provision

```bash
azd provision --preview     # review what will be created
azd provision               # can take 30+ minutes (API Management is the slow part)
```

The postprovision hook prints "Skipping approved-content publication" at this stage; that is expected.
The APIM model endpoint deliberately returns HTTP 503 (`AgentConfigurationIncomplete`) until all five approved agent
versions have been registered and pinned. Empty pins are not created as APIM Named Values. The next provision after the
agent-registration step installs the existing caller/version checks and protective writer hold unchanged. Removing a pin
on a later redeploy reinstalls the deny policy rather than leaving a previously active model endpoint available.

## 3. Approve the Search private link

Azure AI Search reaches the content storage through a shared private link that the storage owner must approve:

```bash
STORAGE_ID=$(azd env get-value CONTENT_STORAGE_RESOURCE_ID)
az network private-endpoint-connection list --id "$STORAGE_ID" \
  --query "[?properties.privateLinkServiceConnectionState.status=='Pending'].{id:id,desc:properties.privateLinkServiceConnectionState.description}" -o table
az network private-endpoint-connection approve --id <connection-id> --description "approved-content indexer"
```

Approve only the connection whose description names the approved-content indexer.

## 4. Register the Entra applications and operator IDs

```bash
export PYTHONPATH=src
RG=$(azd env get-value AZURE_RESOURCE_GROUP)

# Strategy API (plan first, then apply). Copy the STRATEGY_API_CLIENT_ID it prints.
ARGS=(--environment gg-dev
      --pipeline "$(azd env get-value STRATEGY_PUBLISHER_PRINCIPAL_ID)"
      --fairness "$(azd env get-value STRATEGY_FAIRNESS_PRINCIPAL_ID)"
      --runtime  "$(azd env get-value STRATEGY_RUNTIME_PRINCIPAL_ID)")
python -m governedgraph.strategy_service.provision_auth "${ARGS[@]}"
python -m governedgraph.strategy_service.provision_auth "${ARGS[@]}" --apply
azd env set STRATEGY_API_CLIENT_ID <printed-client-id>

# Synthetic contact centre API (plan, then --apply). Copy client_id and audience from the JSON.
python scripts/configure_contact_centre.py --environment gg-dev \
  --runtime-principal "$(azd env get-value STRATEGY_RUNTIME_PRINCIPAL_ID)"
python scripts/configure_contact_centre.py --environment gg-dev \
  --runtime-principal "$(azd env get-value STRATEGY_RUNTIME_PRINCIPAL_ID)" --apply
azd env set CONTACT_CENTRE_CLIENT_ID <client_id>
azd env set CONTACT_CENTRE_AUDIENCE <audience>
azd env set GOVERNEDGRAPH_SYNTHETIC_CONTACT_CENTRE true

# You are the recovery and presenter operator
OPERATOR=$(az ad signed-in-user show --query id -o tsv)
azd env set STRATEGY_RECOVERY_OPERATOR_ID "$OPERATOR"
azd env set VOICE_DEMO_OPERATOR_ID "$OPERATOR"
```

Each script plans by default and changes nothing until `--apply`. Both refuse to widen existing grants.

## 5. Create the Foundry agents and pin their versions

The agent prompts come from the domain pack. Provisioning is idempotent and creates immutable versions:

```bash
eval "$(python -m scripts.azd_env export --task agents)"
for role in counsellor voice-compact intent understanding answer-review fairness; do
  python scripts/create_agents.py --role "$role"            # plan only
done
for role in counsellor voice-compact intent understanding answer-review fairness; do
  python scripts/create_agents.py --role "$role" --apply    # prints agent_version
done
azd env set AZURE_AI_AGENT_VERSION            <counsellor version>
azd env set AZURE_AI_VOICE_COMPACT_VERSION    <voice-compact version>
azd env set AZURE_AI_INTENT_AGENT_VERSION     <intent version>
azd env set AZURE_AI_UNDERSTANDING_AGENT_VERSION <understanding version>
azd env set AZURE_AI_REVIEW_AGENT_VERSION     <answer-review version>
azd env set AZURE_AI_FAIRNESS_AGENT_VERSION   <fairness version>
```

Your signed-in identity needs rights on the AI project to create agents; the deployment grants the `AZURE_PRINCIPAL_ID` operator that access.

## 6. Deploy the services, then re-provision

```bash
azd deploy        # builds the four services remotely from the Dockerfile and records SERVICE_*_IMAGE_NAME
azd provision     # applies the client IDs, agent pins and real images (including the issuer and publisher jobs)
```

This second provision now runs the postprovision hook, which publishes the approved content through the private publisher job. If it
reports that the Search private link needs owner approval, finish step 3 and run `azd hooks run postprovision`.

## 7. Bootstrap the real member-token key

The dev issuer job exports the public key of the signing key that never leaves Key Vault:

```bash
RG=$(azd env get-value AZURE_RESOURCE_GROUP); JOB=job-gg-dev-member-issuer
EXEC=$(az containerapp job start -g "$RG" -n "$JOB" --query name -o tsv)
az containerapp job execution show -g "$RG" -n "$JOB" --job-execution-name "$EXEC" --query properties.status -o tsv   # wait for Succeeded
az containerapp job logs show -g "$RG" -n "$JOB" --execution "$EXEC" --container issuer --tail 20
```

Copy the JSON line the job printed (it is a public key only) and replace the placeholder, then provision once more:

```bash
azd env set MEMBER_TOKEN_PUBLIC_JWK '<the JSON line>'
azd provision
```

If the replica has already expired, read the same line from `ContainerAppConsoleLogs` in Log Analytics. Never grant yourself Key Vault
access to fetch it.

## 8. Verify

```bash
python -m scripts.azd_env show                       # every task should read OK or list what is still optional
python -m scripts.run_dev_proof channel --logs       # optional: re-read the channel proof's PASS lines (the script already ran it)
bash scripts/reset_demo.sh                           # offline rehearsal, no Azure needed
```

Then confirm the on-call alert receiver (click the verification email) and follow [Azure acceptance](../examples/pension_wtp/docs/demo-runbook.md#azure-acceptance)
in the runbook. Every later operation (presenter, voice proof, explainability, recovery) is in the
[runbook](../examples/pension_wtp/docs/demo-runbook.md) and reads its values from the azd environment.

## 9. Optional features

- **Browser presenter (voice demo).** `azd env set DEPLOY_VOICE_DEMO true`, then `azd provision`. The preprovision hook registers the
  presenter app and writes `VOICE_DEMO_CLIENT_ID`; if it was empty during that run, run `azd provision` once more. Open the URL printed by
  `python -m scripts.azd_env show --task presenter`.
- **Learning plane (P2) and dev fixtures.** Automatic: the `learning` step builds the images, publishes the sample's labelled
  synthetic aggregates, discovers the Azure ML workspace's artifact containers (container-scoped grants only), runs the
  acceptance cycle and enables the schedule. See [Learning plane](#learning-plane-automatic).

## What has and has not been verified

The Bicep templates build with no warnings, and `scripts/deploy.py` is unit-tested end to end against a simulated Azure (ordering, resume
after failure, refusals, key handling), but the command sequence was derived from the code and templates. **A full deployment from an
empty subscription has not been run from this repository**, so expect to meet environment-specific issues (quota, region capacity, a
provider not yet registered, an approval step). When a step fails, the script names the cause; fix it and run it again.
