# Demo and operations runbook (pension sample)

**ILLUSTRATIVE - synthetic data.** Use the Azure-hosted presenter, in-network
synthetic probes, deployed console and isolated learning jobs. No local
application, file-backed reset or offline agent runtime is supported.

All URLs, resource names and IDs come from your deployed environment. Record
actual approvals, run IDs, measurements and results in private operational
evidence, not this public guide. Readiness is not evaluation or production acceptance.
See [deployment](../../../docs/deploying.md) and
[Azure-only execution](../../../docs/azure-only-execution.md).

## Get your deployment details

Explicit delivery populates the selected azd environment with nonsecret URLs,
resource names and public identifiers. Select the environment explicitly:

```bash
python -m scripts.azd_env tasks
python -m scripts.azd_env show --environment gg-dev
python -m scripts.azd_env show --environment gg-dev --task presenter
eval "$(python -m scripts.azd_env export --environment gg-dev --task reconcile)"
```

Export fails if required configuration is absent or malformed. Do not substitute
another deployment or add credentials to the environment.

| Task | Purpose |
|---|---|
| `presenter` | Authenticated browser presenter URL |
| `voice-benchmark` | In-network voice dispatch measurements |
| `voice-proof` | Hosted synthetic channel/voice proof |
| `reconcile` | Response-audit reconciliation |
| `recovery` | Separately authorized human recovery |
| `p2` | Learning-cycle operator tooling |
| `agents`, `fairness` | Pinned Foundry definitions and fairness operator |

## Demonstrating the journey

1. Deliver the AI disclosure before the member's first question.
2. Explain approved content using official display strings from the synthetic
   values source; the LLM performs no arithmetic.
3. Show the actual served strategy, snapshot, experiment arm and suspensions
   in response evidence. Do not force S5 or clear S2's veto to match a script.
4. Demonstrate a question-specific explanation and, where appropriate, teach-back.
   Unconfirmed understanding is not a resolved outcome.
5. Inspect immutable response references through the administration console.
6. Demonstrate human help and advice escalation without claiming a real callback.

The sample is synthetic. A recording must be labelled recorded, not live or
current-state evidence; [the bundled player](media/offline-player.html) is a
static media asset, not an offline application runtime.

## Browser presenter

1. Obtain `PRESENTER_URL` with the `presenter` task and open it in a current browser.
2. Sign in with the assigned Entra account. Only configured presenter users have
   the delegated `VoiceDemo.Access` scope.
3. Use **Chat**, **Voice**, or the story selector and **Play**. Wait for complete
   disclosure before speaking. Persona runs are live synthetic role play;
   **Take over as the member** continues the same journey.
4. **Talk to a person** remains available. If durable handover cannot be confirmed,
   use direct advisor contact rather than claiming success.

The browser never receives a member token. Runtime calls the private issuer with
its dedicated application role; transient member tokens and audio stay in session
memory. PKCE sign-in, Secure/HttpOnly session cookies, exact-origin checks,
independent API token validation and rate limits protect the stream.
Presenter sign-in is not production member authentication.

Raw persona profiles and cohort attributes do not reach the Counsellor. Closed
simulation scripts provide ordinary untrusted member turns. A separate tool-free
persona agent may generate role-play follow-ups; its identity and context remain
isolated from the guide. Persona failure ends the simulation without inventing
resolution or training behavior.

Microphone audio is not recorded. An explicitly requested watermarked synthetic
video download is a recording exception and never a learning input.
`voice_demo: false` in private delivery inputs disables the presenter, capture
and console conversation lookup together. The channel is development-only.

## Voice Live

An approved figure-free acknowledgement may play after input ends. Screening,
advice routing, authorized lookups, full-draft gates and durable audit still finish
before substantive speech is synthesized. Returned sentences are validated and
queued in order; unchecked or overlapping speech is never released.

The [evaluation method](../../../docs/evaluation.md#voice-measurements) and
[illustrative SLO table](../../../docs/architecture.md#voice-slo-table) distinguish
acknowledgement dispatch, first substantive dispatch and complete-audio readiness.
Measure actual sample counts, failures, warm/cold controls and stage boundaries.
Server dispatch is not acoustic or browser-playback certification.

From the approved Azure-hosted validation environment, with the assigned
presenter/operator identity and voice dependencies:

```bash
eval "$(python -m scripts.azd_env export --environment gg-dev --task voice-benchmark)"
python -m scripts.benchmark_voice --model gpt-counsellor --output counsellor-voice-results.json
python -m scripts.benchmark_voice --model gpt-compact --output compact-voice-results.json
```

Each candidate uses twenty warm turns and separate fresh SDK/Speech controls.
Use new output paths. Never filter or retry failed turns to manufacture a
percentile, change control assignment or remove suspensions.
Physical microphone/speaker behavior requires its own browser check.
Warm-up reuses connections and immutable definitions, not member values or replies,
and does not bypass per-turn checks.

### Hosted synthetic proof

The private issuer mints memory-only step-up tokens and forwards them in
Authorization to the hosted voice adapter. The runtime, not the issuer, owns
Speech sessions. The adapter uses ordinary journey endpoints and forwards the
original bearer on every authenticated request.

Use the immutable probe image from the delivery receipt:

```bash
python -m scripts.run_dev_proof voice --environment gg-dev \
  --image <immutable-probe-image-reference> --logs
```

The execution does not change the job's stored identity, template or permissions.
No local issuance, token file or additional Key Vault grant is permitted.
Output is pass/fail and IDs only; inspect timing metadata with existing
authorized runtime diagnostics. A functional pass does not certify latency.
Small samples do not certify a twenty-turn SLO window.

Missing audio, Speech errors or timeouts fail explicitly. Human help uses the
same governed endpoint; an unconfirmed handover is not success.

## Explainability acceptance

Run reconciliation inside the deployment's private network with existing
authorized storage access:

```bash
eval "$(python -m scripts.azd_env export --environment gg-dev --task reconcile)"
PYTHONPATH=src python -m governedgraph.explainability.reconcile --journey-id J-EXAMPLE
```

Repeat the selector for an explicit scope or omit it for all persisted journeys.
Success requires `complete=true`, matching record/response counts,
`coverage_percent=100` and no pending records. Empty scopes, missing or duplicate
links and conflicting data cannot certify completion. The job retries durable
outbox records before checking. Scoped success is not all-history certification.

Production requires response linkage from first delivery. An existing synthetic
development store may have a separately labelled legacy audit-reporting boundary;
this exception cannot waive current linkage, fabricate backfill or apply to production.

Member self-service uses
`GET /journey/members/{member_ref}/explainability-report`. It requires the token's
own member reference and current step-up. Missing authentication returns 401,
cross-member/non-step-up access 403, busy state 409 and unavailable/conflicting
data 503. Successful reports are `no-store`.

Immutable-store verification may create a tiny labelled synthetic object under
existing retention. Duplicate create and overwrite must fail as expected; never
weaken retention to remove a proof object.

## Human recovery after a protective freeze

**Only the configured human operator may personally recover a freeze.**
An assistant, agent or workload must not execute recovery. Recovery is separate
from strategy reinstatement and cannot change rankings or suspensions.

Delivery configures the delegated recovery registration. If setup is missing,
an authorized application owner/administrator must use the restricted configurator:

```bash
python -m scripts.configure_strategy_recovery \
  --api-client-id "$STRATEGY_API_CLIENT_ID" --human-id "$STRATEGY_RECOVERY_OPERATOR_ID" --apply
```

This is an approved configuration operation, not recovery. Missing assignment
must not be bypassed by disabling assignment-required or using a workload token.

The configured operator uses their own interactive Azure CLI sign-in and a new
recorded decision reference matching `GB-DEV-YYYY-NNN` with actual digits:

```bash
PYTHONPATH=src python scripts/lift_dev_freeze.py --decision-ref <unused-human-decision-reference>
```

- The backend validates the exact delegated human, tenant, scope, client and endpoint.
- Readiness checks the proposed reference; POST atomically claims it.
  A recorded attempt consumes the reference even if a later check fails.
- Latest isolated audit success must match the approved immutable image and command.
- Create-only, read-back-verified journals precede mutation; revision guards protect
  concurrent protective actions.
- Failed or ambiguous writes, changed audit health or failed completion journalling
  restore the freeze.

Do not recover just to make a demonstration green. Readiness alone is not recovery,
and a consumed decision cannot be reused or deleted.

## Scheduled learning

Fairness and P2 run on separate identity-isolated jobs, fairness first and ranking
after its audit margin. Parallelism/completion limits and a renewable lease prevent
overlap. A fresh same-cycle audit, unchanged aggregates, guarded signing and
conditional publication are mandatory. Missing audit or failed stages freeze
and alert; only an authorized human can recover.

Verify actual scheduled cycles and a separately authorized skipped-audit
freeze-and-alert proof before certifying recurrence. Restore ordinary scheduling
after the proof without automatically releasing its protective freeze.
An approved P2 image rollout must update both job images and the Strategy monitor
pin. Image mismatch is a closed audit gate, not permission to use an older audit.

### P2 Azure ML acceptance

The reference runtime uses scheduled Container Apps Jobs. Retained Azure ML
component packaging and manual cycle tools are operator investigation paths,
not a second automatically accepted learning runtime.

P2 images exclude member, cohort and aggregate fixtures. Governed Azure inputs
are required; only fairness reads the cohort zone. The ML platform has dedicated
dependencies separate from signing keys. Grants require the reviewed scope;
do not attach P2 to a workspace identity or broaden shared-store access.

Manual runs such as `scripts/run_p2_container_jobs.py` require explicit authorization
and an immutable image. Every stage must retain create-only verified artifacts.
Root job completion alone cannot certify key isolation, vetoes, tiered approval
or treatment/control behavior. Failure injection must remain a failed run and
leave protection in place. Synthetic fixture uplift is not live performance.

## Fairness Foundry operator

The fairness agent is pinned to an immutable version, never `@latest`, and runs
only under its dedicated fairness identity. Trusted host context supplies scope;
model tool arguments cannot select member records or override a veto.

Deterministic preflight audits and enforces protection independently of the model.
Reinstatement requests route to a refused Tier 2 governance case. Member reports
stay in the host; only receipts reach Foundry or stdout.
The agent has no signing, publication, content-editing or freeze-release authority.
Invocations must not alter the scheduled job's stored configuration.

## Azure acceptance

Run the existing in-network synthetic channel probe:

```bash
python -m scripts.run_dev_proof channel --environment gg-dev --logs
```

Confirm developer Key Vault access is absent. Tokens stay in probe memory and
expire within fifteen minutes. Use existing operator/runtime diagnostics to check
numeric fidelity, agent versions, response linkage and
[content-free native traces](../../../docs/observability.md).

Check advice escalation, injection screening, scoped values, duplicate delivery,
universal vetoes and unchanged expert control. No new probe identity, read grant
or network path is permitted.

Verify alert delivery with the on-call recipient and an approved synthetic
dead-letter event. Enabled alert configuration alone proves neither notification
delivery nor receipt. Never purge queues, settle unrelated messages or weaken
thresholds to obtain a pass.
