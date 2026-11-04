# Demo and operations runbook (pension sample)

> **ILLUSTRATIVE - synthetic data.** Procedures for the bundled sample. Nothing here names a specific deployment: every
> URL, name and ID comes from **your** deployed environment (see [Get your deployment details](#get-your-deployment-details)).
> Acceptance results, timings and evidence from earlier releases are not recorded here; record yours in your own log.

## Get your deployment details

After `azd up` (or `azd provision`) the azd environment holds everything the commands below need: URLs, resource names
and object/client IDs. It never holds a secret, key or token. `scripts/azd_env.py` reads it, validates what each task
needs and exports it to your shell.

```bash
python -m scripts.azd_env tasks                              # list the tasks below
python -m scripts.azd_env show                               # what your deployment provides; flags anything missing
python -m scripts.azd_env show --task presenter              # one task
eval "$(python -m scripts.azd_env export --task reconcile)"  # export a task's values into this shell
```

`export` refuses (exit 1, nothing printed) if a required value is missing or malformed, so a half-configured
deployment fails early instead of running against the wrong target. Use `--env-file <dotenv>` to read a saved copy.

| Task | Used for | Section |
|---|---|---|
| `presenter` | Hosted browser presenter URL (`PRESENTER_URL`) | [Browser presenter](#browser-presenter) |
| `voice-benchmark` | Twenty-turn in-network voice measurement | [Voice Live](#voice-live) |
| `voice-proof` | Hosted synthetic voice or channel proof | [Hosted synthetic proof](#hosted-synthetic-proof), [Azure acceptance](#azure-acceptance) |
| `reconcile` | Explainability reconciliation | [Explainability acceptance](#explainability-acceptance) |
| `recovery` | Human recovery from a protective freeze | [Human recovery](#human-recovery-after-a-protective-freeze) |
| `p2` | Azure ML hosting of the learning cycle | [P2 Azure ML acceptance](#p2-azure-ml-acceptance) |
| `agents`, `fairness` | Foundry agent provisioning and the fairness operator | [Fairness Foundry operator](#fairness-foundry-operator) |

Some values are set by you rather than by Bicep, for example `P2_CONTAINER_IMAGE` and `FAIRNESS_AGENT_IMAGE` after you
build those images (`azd env set NAME value`), and `VOICE_DEMO_CLIENT_ID`, which the preprovision hook writes when
`DEPLOY_VOICE_DEMO=true`. `show` lists each as optional or missing so you know what is left to do.

## The two-minute demo

| # | What happens | What the audience sees | Code |
|---|---|---|---|
| 1 | The counsellor discloses it is an AI; the member asks "Will the transition change what I receive?" | live exchange | `Counsellor.start`, the demo persona |
| 2 | `get_member_values` | figures from the mock system, no LLM arithmetic | `CounsellorTools.get_member_values` |
| 3 | `get_ranked_strategies` | S5 selected; S2 shown as suspended | `StrategyService.get_ranked` |
| 4 | Step-by-step explanation and teach-back question | teach-back | template T-S5 |
| 5 | Member confirms understanding | semantic comprehension check passed | versioned Understanding Assessment Agent |
| 6 | `write_explainability_record` | strategy ID and version, content versions, value source, no demographics | `.run/demo/explainability/records.jsonl` (offline) |
| 7 | Switch to the fairness dashboard | veto of S2 for the 75+ audit cohort (ILLUSTRATIVE) | `.run/fairness_dashboard.html` |

Fallbacks: recorded video, text mode (`PYTHONPATH=src:examples python -m pension_wtp.demo.run_demo`), or the presenter
plays the member. Use journey `J-DEMO-0001` (treatment arm); `J-DEMO-0002` lands in the control arm and shows the static
expert ranking.

## Repeatable rehearsal and offline fallback kit

From any directory, run the launcher by its repository path:

```bash
bash scripts/reset_demo.sh
bash scripts/reset_demo.sh --presenter
```

The launcher uses `.venv/bin/python` (override with an executable absolute `GOVERNEDGRAPH_PYTHON` path), explicitly selects
**local** mode even if the shell says Azure, and never loads `.env`, calls Azure or mints a token. Each reset archives the
previous local `demo` directory under `.run/demo-history/demo-<unique-id>/`, then creates fresh journey state, runs **one**
full governed P2 cycle from the synthetic aggregate fixtures, rebuilds approved-only retrieval from the versioned corpus,
and warms the local template agent's screening, classification and retrieval before disclosure and input. It does not warm
hosted Foundry or Speech. All numeric, advice and fairness gates and the tier routing still apply. Archive history is
retained, not repaired or deleted. `GOVERNEDGRAPH_RUN_DIR` may select another local artifact directory; the paths below
assume the default `.run`.

**Azure is not reset.** No persisted journey, immutable record, queue, snapshot, suspension, schedule or protective freeze is
changed by the offline kit, and it is not evidence of live publication. Never run it to make a live environment look
healthy, and never lift a freeze merely to make a rehearsal look green.

### Seven-step rehearsal checklist

The timing is a **stage budget**, not a measured latency claim. Practise the text/presenter segment within two minutes, then
switch dashboards for twenty seconds.

| Done | Step / budget | Presenter cue and audience-visible proof | Check before taking the stage |
|---|---|---|---|
| [ ] | 1 / 0-15 s | Read the AI disclosure before the member's opening question. | First counsellor text is the approved disclosure; the video also starts with it. |
| [ ] | 2 / 15-30 s | Point to the official `display` strings and administrator/run provenance, not AI arithmetic. | `[tool get_member_values]` in scripted text; no reformatted figures. |
| [ ] | 3 / 30-45 s | Show S5 selected and S2 suspended, with snapshot and arm. | `[tool get_ranked_strategies]`; do not change control assignment or remove a veto. |
| [ ] | 4 / 45-70 s | Read the step-by-step explanation and teach-back question. | Numeric fidelity is `True`; approved content and placeholder binding remain in effect. |
| [ ] | 5 / 70-90 s | Member restates that the amount stays the same on transition and can move afterwards. | `journey state` is `resolved`; a failed teach-back is not relabelled as a pass. |
| [ ] | 6 / 90-120 s | Show strategy/version, content versions and value-source references in the audit record. | `.run/demo/explainability/records.jsonl` exists; no runtime demographic fields. |
| [ ] | 7 / +20 s | Switch to the fairness dashboard: S2 veto, 75+ aggregate audit cohort, ILLUSTRATIVE watermark. | Open `.run/fairness_dashboard.html` locally, without a portal or network dependency. |

### Verify all three fallbacks before disconnecting

- **Recorded audiovisual fallback:** keep [offline-player.html](media/offline-player.html) and
  [annelies-voice-fallback.webm](media/annelies-voice-fallback.webm) in the same local directory. Open the HTML directly in
  the laptop browser, disconnect networking and press Play; verify picture and audible speech all the way through. The page
  has no external scripts, fonts, login or media requests and is labelled **recorded**, not live or current-state evidence.
  Check laptop speaker output separately.
- **Text:** `bash scripts/reset_demo.sh` or
  `GOVERNEDGRAPH_MODE=local PYTHONPATH=src:examples .venv/bin/python -m pension_wtp.demo.run_demo`. Verify disclosure, S5/S2,
  fidelity, resolved teach-back, audit and dashboard. This is offline template composition, not a live Foundry or Voice Live
  proof.
- **Presenter as member, offline text:** `bash scripts/reset_demo.sh --presenter`. The presenter **types** the member turns and
  the normal counsellor handles them. Use "Will the transition change what I receive?" then "It stays the same on the
  transition date and can go up or down afterwards." Ask for a human or an option recommendation in a separate rehearsal and
  verify safe escalation and a nonzero exit instead of a false success. EOF, interruption or four unfinished turns requests
  local escalation and exits nonzero. Live microphone and Voice Live need the authenticated browser and Azure; do not claim
  cloud speech works offline.

If offline gates fail, retain the evidence, stop the demo and use the explicitly labelled recording; never remove a gate.
Test the projector, local file access and sound output before the audience arrives.

## Voice Live

The flow speaks the approved figure-free acknowledgement immediately after input ends. That is not the substantive answer.
Screening, advice intent, approved lookups, full-draft gates and the durable audit all finish before any answer sentence is
synthesized. Checked sentences are validated and queued in order; the browser never plays overlapping or unchecked speech.
The [evaluation method](../../../docs/evaluation.md) and the voice SLO table in [architecture](../../../docs/architecture.md)
define what is measured (acknowledgement, first substantive audio, complete audio, per-stage timings, warm/cold sample sizes)
and the approved targets. Do not treat the acknowledgement as the substantive answer or a small browser sample as acoustic
certification.

The bridge is co-hosted with the orchestrator. Before every presenter session it verifies the pinned agent and warms
safety and classification; the acknowledgement audio is prepared before input. This adds startup work before disclosure,
never a skipped per-turn gate. Client caches hold connections and immutable definitions, not member values or
conversations.

To reproduce the in-network, non-acoustic twenty-turn comparison (member-token issuance stays inside the deployment):

```bash
eval "$(python -m scripts.azd_env export --task voice-benchmark)"   # VOICE_DEMO_ORIGIN, VOICE_DEMO_CLIENT_ID
az login                                                            # the assigned presenter/operator account
python -m scripts.benchmark_voice --model gpt-counsellor --output counsellor-voice-results.json
python -m scripts.benchmark_voice --model gpt-compact --output compact-voice-results.json
```

Install `requirements-voice.txt` first (it supplies Playwright) and pass `--browser <path>` if your Chromium is not at the
default. Each run includes twenty warm Speech turns and three fresh SDK/Speech controls, labelled separately. It is a
validated-dispatch sink, not acoustic or laptop-network latency. Existing state, control assignment, strategy suspensions and
all failed turns are preserved; do not retry or filter turns to manufacture a percentile.

For a presenter channel with microphone and speaker, install `requirements-azure.txt` and `requirements-voice.txt` plus the OS
PortAudio library, then set `AZURE_VOICE_LIVE_ENDPOINT` (your AI Services endpoint) and `GOVERNEDGRAPH_ORCHESTRATOR_URL`
(`ORCHESTRATOR_URL` from azd). These are URLs, not keys. No member token belongs in settings, a file, a CLI argument or
container configuration; in the hosted topology it exists only as a request-scoped bearer in the bridge's memory.

### Hosted synthetic proof

The orchestrator hosts `POST /voice/simulations` under the **runtime identity**, which already holds the Cognitive Services
and Foundry roles it needs on the AI account; no permission is added for the proof. The dev issuer has no AI role and never
connects to Speech: it alone mints the synthetic token and forwards it in `Authorization` to the hosted adapter, which
forwards it unchanged to the normal journey endpoints. The route is disabled outside `gg-dev` and requires the step-up
verified synthetic demo member.

Do not read a developer token file or mint a synthetic token on your machine. Missing audio, a speech error or a timeout fails
explicitly and requests a human if the journey has started; a failed audio session cannot resume. If the orchestrator is also
unavailable, direct advisor contact is required; never claim a queued handover without confirmation. The synthetic persona and
the counsellor use separate speech sessions with voices defined by the domain pack (`demo.persona_voices`); only the scripted
utterance enters Speech or the orchestrator, never the persona profile.

**Launch** the proof as an execution-only run of the existing dev-issuer job, from the deployment's own values:

```bash
eval "$(python -m scripts.azd_env export --task voice-proof)"   # subscription, resource group, registry, orchestrator, gateway
az login && az account set --subscription "$AZURE_SUBSCRIPTION_ID"

# 1. Build the dev-voice image (it includes the synthetic persona) and read its immutable digest
REGISTRY="${AZURE_CONTAINER_REGISTRY_ENDPOINT%%.*}"
az acr build --registry "$REGISTRY" --target dev-voice --image governedgraph/voice-issuer:proof .
DIGEST="$(az acr repository show --name "$REGISTRY" --image governedgraph/voice-issuer:proof --query digest -o tsv)"

# 2. Start the proof; the job's stored template, identity and permissions are not changed
python -m scripts.run_dev_proof voice --logs \
  --image "$AZURE_CONTAINER_REGISTRY_ENDPOINT/governedgraph/voice-issuer@$DIGEST"
```

`run_dev_proof` reads the resource group, orchestrator and gateway from the azd environment, refuses any environment other than
`gg-dev` and any image that is not an immutable digest in the deployment's own registry, and prints the execution name. If the
replica has expired before you read it, query `ContainerAppConsoleLogs` in Log Analytics, filtering the job name and the
execution's container group; the issuer receives no diagnostic read grant. Output is PASS/FAIL, IDs and the explicitly
requested timings, never utterances, tokens or member figures. A control-arm journey or an existing protective freeze is not
reset to manufacture S5.

Show the returned journey and audit IDs, the S5/S2 checks, teach-back, total duration and p95/sample size. A functional pass
does not hide a latency target miss: the job also prints an explicit `FAIL voice_turn_slo` line when the target is missed, and
a successful exit alone does not mean the target passed. The in-process `VoiceDemoResult.metrics` states its measurement
boundary (end of captured input to complete output audio ready, including STT, HTTP and TTS); a two-turn sample is not a
twenty-turn SLO certification, and loopback is not an acoustic microphone proof.

### Browser presenter

For content-free native Foundry traces see the [trace visibility checks](../../../docs/observability.md). Host telemetry
alone does not establish that native agent spans are present; Azure mode also needs `AZURE_AI_PROJECT_RESOURCE_ID`, which
Bicep supplies to both runtime services. Do not enable prompt/response capture to populate the trace view.

1. `python -m scripts.azd_env show --task presenter` prints `PRESENTER_URL`. Open it in a current Chromium browser and allow
   the Entra sign-in popup.
2. Select **Sign in with Entra** using the assigned operator account. Only that account is assigned `VoiceDemo.Presenter` and
   the delegated `VoiceDemo.Access`. No VPN, private DNS, local Python, Speech role or Key Vault grant is needed. The
   application's client ID is `VOICE_DEMO_CLIENT_ID` in the azd environment.
3. Select **Run Annelies and save fallback video** (the sample persona) for the paced two-voice flow and allow the final WebM
   download, or select **Use my microphone (not recorded)**, allow microphone access, wait for the complete spoken disclosure
   and then use **Speak** / **Finish speaking**.
4. **Stop / ask a person** requests handover through the same orchestrator. On failure use the fallback; never estimate or
   bypass screening.

Member tokens never reach the browser. The runtime alone calls the internal issuer with `VoiceDemo.Issue`; only its principal
has that app role. The issuer uses its key-only identity and mints only the synthetic demo member, with step-up and at most
fifteen minutes' validity. Entra sign-in is not production member authentication. A Secure/HttpOnly delegated-session cookie,
exact-origin checks, independent API JWT validation and rate limits protect the WebSocket. Live audio and member tokens remain
session memory only; presenter microphone audio is not recorded, and the requested, watermarked synthetic simulator video is
an explicit exception saved locally by the browser.

**Opt-in and configuration.** `DEPLOY_VOICE_DEMO=true` is honoured only in `gg-dev`; `deployVoiceDemo` defaults to false
everywhere. `scripts/configure_voice_demo.py` idempotently maintains the owned registration, the exact redirect origin and the
two assignments from the azd preprovision hook. Setting the flag to false and deploying disables the channel and its dedicated
service principal without deleting data. The SPA and Azure CLI are preauthorized delegated clients, not extra assigned users;
no tenant-wide consent or client secret is needed. Physical laptop microphone and speaker behaviour still needs an on-stage
rehearsal.

**Fallback.** `PYTHONPATH=src:examples python -m pension_wtp.demo.run_demo` is offline text, and
`python -m governedgraph.voice.voice_live_bridge --text` selects it too. Without an authenticated host the standalone voice
module prints an explicit failure and runs that fallback, exiting nonzero even if the text succeeds. Neither path is voice
acceptance.

## Explainability acceptance

Use a persisted hosted-orchestrator journey, not the standalone demo's unpersisted in-memory state. The reconciliation needs
network access to the deployment's private storage, so run it from a host or job inside that network, with values from azd:

```bash
eval "$(python -m scripts.azd_env export --task reconcile)"
PYTHONPATH=src python -m governedgraph.explainability.reconcile --journey-id J-EXAMPLE
# Omit --journey-id for all persisted journeys; repeat it for an explicit scope.
```

Success exits 0 and requires `complete=true`, exactly matching response and record counts, `coverage_percent=100` and no pending
records; failures exit 1. The job retries the durable outbox before checking. Inspect missing or duplicate in-scope references
and withheld outputs; never present an acceptance-scope pass as an all-history linkage certification, and never manufacture old
links. The background worker also retries pending audit records.

A fresh deployment has no legacy history, so **every** delivery is in scope. Only a `gg-dev` environment that was first deployed
before response IDs were recorded may set `GOVERNEDGRAPH_LEGACY_CUTOVER_UTC` (ISO-8601 with a timezone): deliveries before that
instant are then reported on a separate `legacy, untracked` line, and any missing linkage at or after it still fails. Any other
environment ignores the setting; production launches with linkage from day one.

Member self-service: authenticated `GET /journey/members/{member_ref}/explainability-report` through the gateway (omit
`/journey` at the orchestrator). The response contains `member_ref`, `data`, `interactions`, `records`, `pending_response_ids`
and `delivered_response_ids`. Only the token's own pseudonymous reference with current step-up is admitted: missing
authentication is 401, cross-member or non-step-up is 403, a busy journey is 409 and unavailable or conflicting data is 503.
Successful reports are `no-store`. For live member requests use the hosted synthetic issuer (see
[Azure acceptance](#azure-acceptance)), never a developer token file or a new data-access grant.

The audit container is immutable. Live proof of that creates only a tiny labelled synthetic blob; a duplicate create must fail
with `BlobAlreadyExists` and an overwrite with `BlobImmutableDueToPolicy`. Keep such proofs under the existing retention and never
weaken or unlock retention to remove one.

## Human recovery after a protective freeze

A protective freeze (a failed or skipped fairness audit, a failed P2 stage) is lifted only by a human. **The operator must
personally run recovery; an assistant, agent or workload must not.** Recovery is delegated, interactive and bound to the
operator configured at deployment (`STRATEGY_RECOVERY_OPERATOR_ID`).

The deployment automatically configures delegated recovery access for its own API and saved operator in the
`recovery-access` step. For an older deployment missing that setup, use the normal deployment command:

```bash
python -m scripts.deploy --mode resume --only recovery-access
```

This needs an authorized application owner or Entra administrator. `AADSTS50105` means the assignment is missing;
logging in again alone cannot fix it. The step refuses any API or operator other than the deployment's values and any
environment other than `gg-dev`. It retains assignment-required and does not lift the freeze.
The human recovery helper waits through at most five 60-second retries if Entra still returns `AADSTS50105` after setup;
this is not a successful recovery acknowledgment. Other authentication failures stop immediately. Persistent failure
still requires the correct operator assignment, not disabling assignment-required or using a workload identity.

To recover, with your own interactive `az login`, supply a new, unused human decision reference matching
`^GB-DEV-\d{4}-\d{3}$`:

```bash
read -r -p "Unused recovery reference (GB-DEV-YYYY-NNN, with digits): " RECOVERY_REF
PYTHONPATH=src python scripts/lift_dev_freeze.py --decision-ref "$RECOVERY_REF"
```

Replace `YYYY` and `NNN` with actual digits for your recorded decision; do not enter those placeholder letters.
The command explicitly reads `gg-dev`, regardless of a stale exported `AZURE_ENV_NAME` or the default azd selection.
It still rejects inconsistent environment metadata rather than rewriting it, and does not change your shell or azd selection.
It reads the azd environment itself (`STRATEGY_TENANT_ID`, `STRATEGY_API_CLIENT_ID`, `STRATEGY_ADMIN_URL`) and
explicitly uses `AzureCliCredential`, never an environment or managed-identity credential. It never prints a bearer token. It
reports environment name, reference format or reuse, credential type, tenant and endpoint failures separately, showing detected
non-secret values; the endpoint must be the deployment's `https://apim-gg-<suffix>.azure-api.net/strategy-admin` with no port,
credentials, query or fragment.

- The client sends the proposed reference to GET readiness; the server checks existing requested and completed recovery records
  before certifying it. POST checks again and atomically claims it with a create-only, decision-keyed intent. **A recorded
  attempt consumes the decision** even if a later check fails: obtain a new human decision rather than deleting or repairing
  history. Readiness alone does not consume it.
- The backend requires the approved operator's object ID, the tenant, the delegated `Strategy.Recover` scope and the Azure CLI
  client. Workload tokens are rejected regardless of application roles.
- It refuses if the latest actual isolated audit has not succeeded using the approved immutable image and audit command, or if
  state or audit health changes during recovery. Intent and completion journals persist who, when, the decision reference and
  the reason, create-only with read-back. A failed audit or journal failure cannot yield a successful release, and a
  post-mutation failure restores the freeze. A concurrent protective action wins the state-revision check.
- Recovery changes neither rankings nor suspensions, and strategy reinstatement stays disabled. Do not recover just to start a
  normal learning cycle, and never POST recovery to test that readiness works.

**Scheduled cycles.** Native Schedule triggers run `python -m governedgraph.learning.scheduled_jobs` on the two isolated jobs,
fairness first and P2 two minutes later. Normal UTC cron is `0 */4 * * *` and `2 */4 * * *`; a five-minute proof cron exists for
dev only. Parallelism and completion count of one, plus the renewable Blob lease, prevent overlapping work. Only a protected,
fresh, same-cycle audit bound to the actual successful fairness execution and unchanged aggregates permits publication; a
missing or failed audit freezes and alerts, and recovery stays human-only. Before relying on the schedule, require two actual
scheduled cycles and a skipped-audit freeze-and-alert proof, then restore the normal cadence.

**Readiness 503 after a P2 image rollout.** Compare the approved `P2_CONTAINER_IMAGE` in azd with the fairness job's container
image and its `GOVERNEDGRAPH_P2_IMAGE`, **and the Strategy service's `GOVERNEDGRAPH_P2_IMAGE`**. The latest successful audit must
use that exact approved immutable image in both fields. `infra/main.bicep` binds both sides to `p2ContainerImage`; every scoped
P2 image rollout must update and verify the Strategy monitor pin too, not just the jobs. An image mismatch is a correctly
closed audit gate, not a reason to ignore the pin, choose an older audit, grant a role or clear the freeze.

## P2 Azure ML acceptance

`python -m scripts.deploy` performs this section's first acceptance automatically: it builds the immutable `p2` image into the
deployment's Azure ML registry, publishes the sample's synthetic dev inputs, discovers the workspace artifact containers for
the scoped grants, runs one complete cycle and the protective fairness-agent proof, and enables the schedule only afterwards.
The manual commands below are for operator investigation, re-runs and the recurrence checks that need time to elapse.

Source packaging lives in `governedgraph.learning.azure_ml`; the Azure dependencies pin `azure-ai-ml` and `fairlearn` in
`requirements-p2.txt`. Build the immutable image from Docker target `p2` into the deployment's Azure ML registry
(`AZURE_ML_REGISTRY_ID`), not the runtime target. It intentionally has no local member, cohort or aggregate fixtures.
Configure the two private compute identities and grant only the discovered Blob artifact-container access. Never attach P2 to
the workspace or its operational vault. The job-status-only monitor role, the isolated private pools, the workspace endpoint and
the scoped artifact and evidence grants are deployed by Bicep.

Temporary hosting uses the same component and domain code, reading the workspace and registry from azd:

```bash
PYTHONPATH=src python scripts/run_p2_container_jobs.py --image <immutable-P2-image>
```

Only the operator starts jobs; workloads have no start or override permission on fairness, and P2's read-only monitor role is
scoped to execution metadata on the two dedicated jobs. The parallel watchdog detects failures before component Python. Every
stage writes a create-only, read-back-verified artifact under `azureml/p2-cycles/<cycle-id>/` in the dedicated ML storage; no
cohorts are copied there. To exercise a real pre-component audit failure in `gg-dev`, add `--fail-audit`: it deliberately exits
nonzero, so inspect the watchdog execution and verify the protected Strategy state actually froze before claiming success.
Never clear that freeze or fabricate a Tier 1 move to enable recurrence. Azure ML remains the target platform; this is
temporary hosting, not new ranking logic or an alternate fairness identity.

Fixture publishing requires `DEPLOY_DEV_FIXTURES=true` and `AZURE_ENV_NAME=gg-dev`; Bicep restricts `deployDevFixtures` to
`gg-dev` and defaults it to false. Only the separate `dev-fixtures` Docker target contains the fixture files. Production uses
the telemetry pipeline and a governed cohort feed; P2 and the runtime never gain cohort or upstream input-write access. Never
present a fixture's illustrative uplift window as measured live performance.

Preview the graph with `--image <immutable-image> --preview <output.yaml>`. After scoped infrastructure validation use
`--image <immutable-image> --submit` without scheduling for the initial proof, and verify the key-isolation probe, S2 suspension
with S5 served first, stored Tier 1 evidence, the uplift report and the watchdog failure behaviour before enabling recurrence.
SDK validation and a `Completed` status alone do not establish acceptance. The source `--schedule` path requires all ten unique
child completion metrics, key-isolation proof, Tier 1 evidence read-back, positive uplift and live treatment/control checks.
After a successful initial submission use `--image <same-immutable-image> --schedule --acceptance-job <completed-job-id>` to
revalidate that evidence without running another ranking cycle, or `--submit --schedule` to validate a new run before enabling
recurrence. These are explicit operator actions, not human approval of each bounded cycle.

## Fairness Foundry operator

```bash
eval "$(python -m scripts.azd_env export --task agents)"
PYTHONPATH=src python scripts/create_agents.py --role fairness            # read-only definition plan
```

Add `--apply` to provision or reuse a separate immutable version; it never updates the counsellor definition. Do not invoke it
under the P2, runtime, developer or issuer identity. The fairness job must use its own `FAIRNESS_MANAGED_IDENTITY_CLIENT_ID`,
the project endpoint and an explicit `AZURE_AI_FAIRNESS_AGENT_VERSION`; the stable endpoint is pinned to that version, never
`@latest`.

The trusted operator binds a closed `FairnessRequest` to `azure_runtime`. Audit preflight uses the isolated Fairlearn runner and
enforces all vetoes; `suspend_strategy` is an idempotent audit/enforcement receipt, not a model-selected target. A request with
`action:reinstate` writes a refused Tier 2 governance case before any model reply. Report requests bind the authorized
pseudonymous member in the host, so member records never enter Foundry. No tool promotes, reinstates, edits content or releases a
freeze.

Permissions are exactly: a custom `agents/read` at the project, Foundry Agent Consumer on the fairness agent only, and Blob Data
Reader on the records container only. Do not grant an agent-write action or broad Foundry User.

The reusable host entrypoint is `python -m governedgraph.agents.fairness_runtime --action audit`; `--action reinstate
--strategy-id <id>` routes a refusal and `--action report --member-ref <authorized-pseudonym>` prepares a host-only report. It
prints receipts only, never report contents. Run it only under the existing fairness job, with a per-execution template that uses
the immutable `FAIRNESS_AGENT_IMAGE` and the stage `fairness-agent-proof`; bind `AZURE_AI_PROJECT_ENDPOINT`,
`AZURE_AI_FAIRNESS_AGENT_NAME`, `AZURE_AI_FAIRNESS_AGENT_VERSION` and `FAIRNESS_MANAGED_IDENTITY_CLIENT_ID` from
`python -m scripts.azd_env show --task fairness`. Do not change the scheduled job's image, command or stage for an operator
invocation. The watermarked HTML dashboard is regenerated by the offline demo.

## Azure acceptance

Live acceptance runs only as the dev-issuer job's `--probe-channel` mode, under its dedicated identity, started with:

```bash
python -m scripts.run_dev_proof channel --logs
```

It passes the deployment's in-network orchestrator endpoint and, to verify the member report's 401/403 protections, step-up,
no-cache and response linkage through the gateway as well, its gateway URL, both read from azd. Tokens stay in process memory and
output is pass/fail and IDs only. Do not issue tokens locally or add a developer Key Vault grant (confirm the developer identity
has no Key Vault assignment). Verify numeric fidelity, agent versions and content-free traces with existing operator and runtime
access.

New action-group email receivers must be verified within the verification window. If the verification email expires, remove and
re-add the same receiver and complete verification; an Enabled configuration alone proves neither verification nor delivery.
Keep its name and address aligned with Bicep and `ON_CALL_EMAIL`. Send one test notification, then confirm that a real synthetic
escalation dead-letter message fires the Sev 1 alert and obtain the on-call person's confirmation of receipt. For repeat tests,
obtain approval before manually completing the exact inspected synthetic probe and waiting for the alert to resolve. Never purge
a dead-letter queue, settle an unrelated message or weaken alert thresholds.
