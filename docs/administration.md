# Administration console — Azure Public

The administration workspace shows strategies, rankings, vetoes, suspensions, governance
cases and assurance decisions, learning evidence, response-linked journeys and audited
protective actions. It runs exclusively as an internal Azure Container App through APIM
at `/admin/`, reads deployed Blob and Strategy stores and uses the role-protected
Strategy API. It cannot reinstate, publish or widen a strategy.

See [Deploying](deploying.md) for the Azure entry point, registered Entra roles,
and the scoped operator/reader identities. There is no loopback, anonymous or file-backed
administration mode. Missing Entra group configuration disables the console.

| Page | Governed Azure source |
|---|---|
| Explanation styles | Approved versioned catalogue and eligibility |
| Style order | Signed Strategy snapshot, retained last-good snapshot and static priors |
| Paused styles | Global suspensions, vetoes and threshold references |
| Fairness reviews | Segregated fairness audit case references only, no demographic labels |
| Review outcomes | Immutable Tier 1 and control/uplift evidence blobs |
| Learning data | Digest-verified aggregate snapshots |
| Member journeys | Immutable reference-only explainability records |
| Action history | Append-only Azure administrative action journal |
| Rules & settings | Approved versions, control-arm share and governance bounds |

### Built-in help for new users

The console explains itself, so a first-time user needs no other document:

- **About this page** (top of every page): what you are looking at, what to look for, what you can do, and the
  words used on that page. It starts open and remembers if you close it.
- **? buttons** on every column heading and at-a-glance number, and blue word buttons in the guide. They open a short
  definition that names the technical equivalent ("Called a strategy in the technical documents").
- **Help & glossary** (top bar): the pages at a glance, five common tasks with steps, what the console will never do,
  and every term in plain words. A one-time welcome note on the Overview offers it.
- **Plain-language details.** Selecting a row opens a short explanation and a list of facts. The raw record sits
  behind **Technical details (for support)**.
- **Safety controls** say when to use them, what each one does, and where to find each reference to type.

All wording lives in `src/governedgraph/admin/static/guide.js`, so it can be reviewed and changed without touching
the logic. `tests/test_admin_guidance.py` keeps it honest: every page has guidance, every word used is defined, no
technical terms appear in the plain text, and sentences stay short. The required `ILLUSTRATIVE - synthetic data`
label stays on every view.

Search filters the selected view; results are paginated. Select a linked strategy, context, case,
decision or journey to inspect its evidence. Empty evidence is labelled absent, not approved or healthy.
Ranking lanes show baseline effective order after eligibility and universal suspensions. Actual
per-journey bounded exploration can differ; this page does not assign members to experiment arms.
The current state is not a full historical snapshot archive. Published learning counts may include
the approved synthetic baseline; the view distinguishes the manifest's observed, eligible and pending
live decisions rather than claiming that every aggregate interaction was observed live.

## Protective controls

- **Freeze** uses the existing Strategy Service protection: retain/restore the last good snapshot
  when available and prevent new publication.
- **Revert to static** clears the current learned ranking and freezes updates. Both arms use eligible,
  unsuspended expert priors.
- **Suspend** excludes an active strategy for every member in a selected state or all states. Supply
  the recorded audit reference, governance case reference, current threshold version and gap metric.
  This is an operator-entered protective measure, not a new fairness audit or a verified board decision.
- S6 human escalation cannot be suspended here.
- No publish, re-rank, train, catalogue-edit, reinstate or freeze-release action is exposed.
  Reinstatement/recovery remain in the existing identity-protected governance workflows.

Actions require a reason and explicit confirmation. The page submits the revision it displayed;
a changed state returns HTTP 409 and requires refresh/review. The journal is flushed before mutation
and records completion or failure separately. A completion-write failure is an **uncertain outcome**,
not success: refresh and investigate the action ID before retrying. Azure Blob state uses conditional writes; the UI handles conflicting revisions as HTTP 409.

## Existing synthetic conversations from Foundry traces

Foundry traces backed by Application Insights can contain messages **only if they were captured**.
The existing framework exporter normally strips content. Its opt-in `gg-dev` diagnostic path records
only allowlisted synthetic members, with a synthetic watermark, journey ID and response ID.
This console does not enable capture, change retention or create a transcript archive.
Uncaptured, redacted, expired or un-ingested turns cannot be reconstructed.

For optional read-only lookup, the values come from the deployment. After `azd provision`, the azd
environment holds the workspace GUID (`LOG_ANALYTICS_WORKSPACE_ID`), the Foundry project ID
(`AZURE_AI_PROJECT_RESOURCE_ID`) and the environment name, so a local console only needs to opt in:

```bash
set -a; eval "$(azd env get-values --environment gg-dev)"; set +a
export GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP=true
```

`GOVERNEDGRAPH_ADMIN_TRACE_WORKSPACE`, `_PROJECT` and `_ENVIRONMENT` still override these if you need to
point at a different deployment. The deployed console needs no setup beyond `GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP=true`
in the azd environment, because the infrastructure passes the same values to it.

Restart the console after changing configuration. From a journey's details, select
**Retrieve synthetic conversation captures**. Retrieval runs
`az monitor log-analytics query` under the operator's **existing Azure CLI sign-in**;
install the CLI's `log-analytics` extension separately if needed. No credentials enter the browser,
and no identities, role assignments, capture flags or resources are created.
The operator must already have authorized read access to that workspace. Lookup is opt-in and
never executed at startup or during an ordinary page refresh.
For a deployed journey that is not present in the local audit store, enter its existing journey ID
under **Journeys & evidence** and select **Inspect captured journey**. This performs the same bounded
synthetic-only lookup, without importing remote records or pretending the local and remote stores match.

Queries use workspace `AppDependencies`, exact project, exact journey, exact capture span,
synthetic flag and watermark, a seven-day window, a 30-second timeout and at most 100 displayed
captures. A 101st row signals truncation. Messages are validated, credential-screened again, displayed
as text (never HTML), and not written to local files or fed to learning. Capture status and response
IDs are preserved. Missing configuration, denied access, timeouts and malformed data are explicit
errors. An empty successful query means **no captures found**, not no conversation.
This integration is contract-tested offline; live workspace retrieval requires deployment validation.

References: [Microsoft tracing guidance](https://learn.microsoft.com/azure/foundry-classic/how-to/develop/trace-agents-sdk#instrument-tracing-in-your-code),
[Azure CLI log query](https://learn.microsoft.com/cli/azure/monitor/log-analytics?view=azure-cli-latest#az-monitor-log-analytics-query).

## Access control (Microsoft Entra groups)

On Azure in `gg-dev` access control is set up by the deployment (see [Access control on Azure](#access-control-on-azure)).
Locally it is **off by default**, which suits a demo. While it is off, the page shows a banner:
anyone who can open the console can view all evidence and apply protective actions.

To turn it on yourself, set one environment variable per access level to an Entra **group object ID**:

| Variable | Level |
|---|---|
| `GOVERNEDGRAPH_ADMIN_READER_GROUP_ID` | View evidence |
| `GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID` | View evidence and apply protective actions |

Leave both blank, or unset, to keep access control off. Setting either one turns it on.

| Configuration | Who can view | Who can act |
|---|---|---|
| Neither set | Anyone who opens the console (banner shown) | Anyone who opens the console |
| Both set | Reader or operator group members | Operator group members |
| Reader only | Reader group members | Nobody (read-only console) |
| Operator only | Any signed-in user of your tenant | Operator group members |

Operators can always view, so they do not also need the reader group. A blank group never grants
action rights, so a partly configured console cannot silently open up protective actions.

Sign-in itself needs an Entra app registration, which the two group IDs cannot replace. Locally, set:

```bash
export GOVERNEDGRAPH_ADMIN_CLIENT_ID='<app-registration-client-id>'
export GOVERNEDGRAPH_ADMIN_TENANT_ID='<tenant-id>'   # falls back to STRATEGY_TENANT_ID
```

Register it once (the Azure deployment does this for you):

1. Add a **Single-page application** platform with redirect URI `http://localhost:8090/`
   (Entra accepts plain `http` only for `localhost`; browse to `http://localhost:8090`, not `127.0.0.1`).
2. Under **Expose an API**, set the application ID URI to `api://<client-id>`, add a delegated scope named
   `Admin.Access`, and pre-authorize the app itself for it. Set the access token version to 2.
   This is what makes sign-in consent-free: tenants that block user consent otherwise demand admin approval for the
   standard `openid profile` permissions.
3. Under **Token configuration**, add the **groups** claim for access tokens. Prefer
   **Groups assigned to the application** and assign your reader and operator groups to it.
   If a user belongs to too many groups to fit in the token, the console denies access and says why.
4. No client secret, API permission or admin consent is needed.

Behaviour:

- The browser signs in with the authorization code flow and PKCE, requests only `api://<client-id>/Admin.Access`, and
  holds the resulting access token in page memory only. A page reload asks you to sign in again.
- The server verifies every data and action request: RS256 signature, tenant issuer, this app as audience, expiry,
  the `Admin.Access` delegated scope, and group membership. ID tokens, app-only tokens and tokens for other scopes
  are rejected.
- With access control on, the journal records the signed-in user as `entra:<object-id>`.
- Any misconfiguration (a group or client ID that is not a GUID, or a missing client or tenant ID)
  blocks the console with an explanation instead of falling back to open access.
- The top bar shows who is signed in and whether the account is view-only.

Access control restricts who may use the console, locally and on Azure.

## Local protection

- Disabled unless `GOVERNEDGRAPH_ADMIN_ENABLED=true`.
- Locally it requires a loopback peer and hostname, same-origin requests, and a per-process session
  capability. DNS-rebinding hosts and cross-origin requests are rejected. There is no CORS allowlist.
- Responses and conversation views are `no-store`; CSP excludes external assets, framing and inline
  scripts, and allows `login.microsoftonline.com` only when access control is on. The session
  capability is kept in page memory. Only the temporary sign-in proof (PKCE verifier and state) uses
  session storage, and it is removed when the sign-in completes.
- With access control off locally, the actor is labelled `local-loopback-operator`, **not** a verified
  human identity. Do not expose the local port through a proxy, tunnel or remote port share.

## Deploy to Azure

The console is part of the same deployment as the rest of the solution and the voice presenter, not a separate
product. `python -m scripts.deploy --inputs .azure/private-delivery.json` composes the reference gg-dev without
console-specific manual steps:

- the five services, including the console behind the gateway at `/admin/`;
- Entra sign-in: the reader and operator groups, you as a member of both, and the sign-in app;
- the voice presenter, **synthetic-conversation capture**, and the console's **conversation view** (Log Analytics Reader
  for the console identity on the one workspace);
- lightweight readiness of the running applications, native schedules and console page. Sign-in,
  route authorization and synthetic conversation behavior remain controlled by their independent tests/probes.

Set `voice_demo: false` in the private delivery inputs to disable the presenter, capture and conversation view together.
The reference composition retains its own sign-in and does not opt into open protective actions.

```bash
python -m scripts.deploy --inputs .azure/private-delivery.json
```

Foundation, immutable builds, bounded registrations and complete workloads are separate explicit contracts,
not repeated full provisions or service-by-service builds. The console is at `ADMIN_CONSOLE_URL`, the
gateway URL followed by `/admin/`.

What is created, and why it is narrow:

| Resource | Purpose and limits |
|---|---|
| Identity `id-gg-admin-*` | Its own identity. Not shared with the runtime, Strategy Service, P2 or fairness roles. |
| Container App `ca-gg-admin-*` | Internal ingress, one replica (the session capability is per process), minimal environment. No member-token keys, Service Bus, Key Vault or model settings. |
| Gateway route `/admin/` | Eight explicit routes (page, assets, five reads, one action). Everything else is a 404 at the gateway. |
| Reader on `explainability-records`, `learning-aggregates`, `learning-evidence` | Container scope only. These hold immutable audit references, aggregates and committee/uplift evidence. |
| Contributor on `admin-actions` | Container scope. The container is a 30-day immutable store: each action step is a separate create-only blob. |
| `Strategy.Freeze` and `Strategy.Suspend` app roles | Granted to the admin identity only. It has no `Strategy.Publish`, so it cannot publish a snapshot, and reinstatement and recovery stay in the existing human-governed workflow. |

It cannot read the cohort zone, approved content, journey state, member values, signing keys or
conversation stores. Governance cases on Azure are projected from the active suspensions (case ID,
audit ID, gap and threshold), because the full case files live beside the segregated cohort audit.
A reinstated suspension no longer appears as a case. Response audit records are read as a bounded
sample (1,000 blobs by default, `GOVERNEDGRAPH_ADMIN_MAX_AUDIT_BLOBS`) and the page says when it is truncated.
Snapshots are shown as verified by the Strategy Service when it loaded them; the console does not re-verify
signatures, so it holds no Key Vault permission.

### Access control on Azure

Sign-in is set up by bounded delivery bootstrap, the same way as the voice presenter, using `scripts/configure_admin_console.py`,
which is idempotent:

1. creates the **reader** and **operator** security groups (`GovernedGraph gg-dev admin readers-…` and
   `… admin operators-…`);
2. adds **the user running the deployment** to both groups, and nobody else;
3. registers a single-tenant single-page app (PKCE, no secret, no API permissions) with `…/admin/` as its redirect URI;
4. puts the groups in sign-in tokens, assigns both groups to the app and requires assignment, so only group members can
   sign in at all;
5. records `GOVERNEDGRAPH_ADMIN_CLIENT_ID`, `…_READER_GROUP_ID` and `…_OPERATOR_GROUP_ID` in the azd environment,
   from where provisioning passes them to the console.

The console then shows **Sign in with Microsoft**. Add more people by adding them to the groups in Entra; remove access the
same way. These groups only gate the console. They do not enable reinstatement or recovery (those stay in the existing
human-governed workflow, and no governance group is configured).

| Setting | Meaning | Default |
|---|---|---|
| `GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL` | Set to `false` to skip the registration and leave the console open | `true` in gg-dev |
| `GOVERNEDGRAPH_ADMIN_READER_GROUP_ID` / `…_OPERATOR_GROUP_ID` | Set before provisioning to use your own groups. They are used as given and their membership is never changed | created |
| `GOVERNEDGRAPH_ADMIN_GROUP_CLAIM` | `SecurityGroup` if the tenant lacks Entra ID P1/P2 (group assignment is skipped; the console still checks membership on every request) | `ApplicationGroup` |
| `GOVERNEDGRAPH_ADMIN_ALLOW_OPEN_ACTIONS` | Allow protective actions while access control is off | `false` |
| `GOVERNEDGRAPH_ADMIN_MEMBER_ID` | Object ID to add instead of the deploying user | the deploying user |

If you opt out, the console shows the "Sign-in is off" banner, evidence is viewable by anyone who can reach the URL,
and **protective actions are disabled** unless `GOVERNEDGRAPH_ADMIN_ALLOW_OPEN_ACTIONS=true`.

Review the plan without changing anything: `PYTHONPATH=src:. python -m scripts.configure_admin_console` after exporting the azd
environment (`set -a; eval "$(azd env get-values)"; set +a`). Sign-in asks for no consent and needs no admin approval,
because the app exposes and pre-authorizes its own `Admin.Access` scope, as the voice presenter does.

### Conversation captures on Azure

Conversation text in the console needs **two** switches, both off by default, and both must be on:

| Switch | Effect |
|---|---|
| `GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS=true` | The orchestrator records the text of **synthetic** `gg-dev` members' turns in Application Insights (credentials redacted, size capped, tagged synthetic; effective only with the presenter deployed). It is never a learning input. Only journeys that run **after** it is enabled are recorded. |
| `GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP=true` | Gives the admin identity **Log Analytics Reader on the workspace** so the console can query those spans (`azure-monitor-query`, seven-day window, 100 turns, synthetic-only filters). That role reads the whole workspace, not only the traces. |

Set both with `azd env set <NAME> true` and `azd provision`. The guided runner sets both for you (with the presenter,
which capture needs). With only the lookup on, the console reports no captures
because nothing was recorded. The journey details show "No captured turns found" with the likely reasons.
Existing journeys that ran before capture was enabled cannot be recovered.

Azure Monitor stores the synthetic flag as `True`, so the query compares it case-insensitively. Generate a captured
journey for a check with `python -m scripts.run_dev_proof channel` (the existing in-network proof job).

### Operating notes

- If an action returns "The Strategy service refused the administration identity", the `Strategy.Freeze`
  and `Strategy.Suspend` roles have not been assigned yet: run the `provision_auth ... --admin` step above.
- The journal is write-once. A `requested` entry with no matching `applied` or `failed` entry needs
  investigation before the action is retried.
- Scaling out needs a shared session capability first; keep one replica.
- Azure behaviour is verified offline with in-memory stores and a mocked Strategy API. Verify the deployed
  console after each deployment: open the URL, refresh, and confirm every view loads.

## Validation

```bash
pytest tests/test_admin_api.py tests/test_admin_access.py tests/test_admin_azure.py \
  tests/test_admin_deployment.py tests/test_admin_data.py tests/test_admin_traces.py
```

Tests cover local authorization, optional group access control (real RS256 token validation with a
generated key, every group configuration, rejected tokens, group overage and fail-closed
misconfiguration), disabled Azure mode, closed action contracts, durable protections and journal
failures, universal overlays, S6 availability, demographic-free projections, digest-checked feedback,
journey linkage, and bounded synthetic trace response/error contracts.
