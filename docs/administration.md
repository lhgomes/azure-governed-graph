# Administration console — Azure Public

The administration workspace shows strategies, rankings, vetoes, suspensions, governance
cases and assurance decisions, learning evidence, response-linked journeys and audited
protective actions. It runs exclusively as an internal Azure Container App through APIM
at `/admin/`, reads deployed Blob and Strategy stores and uses the role-protected
Strategy API. Owners can approve and revoke order, pause and unpause overrides for existing
approved strategies. Owners can also publish catalogue changes without redeployment.
The console cannot sign a release or release a protective freeze.

See [Deploying](deploying.md) for the Azure entry point, registered Entra roles,
and the scoped operator/reader identities. There is no loopback, anonymous or file-backed
administration mode. Missing Entra group configuration disables the console.

## Add, edit and remove explanation styles

Owners use **Add style** above the list, or **Edit style** / **Remove style** in a style's
Summary tab. The editor changes the name, approved explanation pattern, journey stages,
channels and accessibility modes. It reuses the deployed, approved pattern library;
it is not a free-text prompt editor. The human advisor cannot be edited or removed.

**Publish changes** records a confirmed owner decision and queues an exact, versioned
catalogue proposal. Clicking **Publish changes** is the owner's approval; there is no
additional approval checkbox in the style editor. The existing isolated P2 job signs and activates it after its normal
checks; no application redeployment is needed. The current styles remain active while
publication is pending. A protective freeze blocks activation, not recording the decision.
The list shows the pending change and offers **Cancel publication** before activation.
Only one proposal can be pending; stale state requires refreshing and confirming again.

Publication atomically installs the signed catalogue and its signed expert baseline.
New styles start after existing eligible styles in expert order. Existing order and
unpause overrides are cleared; protective findings and owner pauses remain in force.
Changed styles receive a new version and do not inherit their old version's learning
results. Removed styles disappear from future selection and the main list; historical
answer and audit evidence remains. Subsequent changes use another approved publication.
The active runtime, embedded tester and learning readers use the published catalogue.

| Page | Governed Azure source |
|---|---|
| Explanation styles | One row per approved style: availability, explanation, eligibility and recorded uses; orders, pauses and digest-verified learning counts in details |
| Conversations | Immutable reference-only explainability records and optional captured synthetic turns |
| Activity | Fairness case references, immutable Tier 1/control evidence and the append-only administrative action journal |

### Built-in help for new users

The console explains itself, so a first-time user needs no other document:

- **About this page** (top of every page): what you are looking at, what to look for, what you can do, and the
  words used on that page. It starts closed and remembers your preference.
- **? buttons** on every column heading, and blue word buttons in the guide. They open a short
  definition that names the technical equivalent ("Called a strategy in the technical documents").
- **Help & glossary** (top bar): the pages at a glance, five common tasks with steps, what the console will never do,
  and every term in plain words.
- **Plain-language details.** Selecting a row opens a short explanation and a list of facts. The raw record sits
  behind **Technical details (for support)**.
- **Safety controls** say when to use them, what each one does, and where to find each reference to type.

All wording lives in `src/governedgraph/admin/static/guide.js`, so it can be reviewed and changed without touching
the logic. `tests/test_admin_guidance.py` keeps it honest: every page has guidance, every word used is defined, no
technical terms appear in the plain text, and sentences stay short. A single quiet **Synthetic data**
label stays in the shared header, without repeated warnings in details or the tester.
Dataset, API and export watermarks retain their full wording.

Search filters the selected view; results are paginated. Select a linked strategy, context, case,
decision or journey to inspect its evidence. Empty evidence is labelled absent, not approved or healthy.
Each style appears once. Status distinguishes active, partly paused, fully paused and the
always-available human advisor. Details use three tabs: **Summary**, **Order by situation** and
**Learning results**. Tabs support arrow keys, Home and End. Summary includes controls and pause evidence.
**Recorded uses** count saved answer records naming that strategy, using the same bounded read as
Conversations; a truncated source is labelled **sample**, including zero counts. These are not lifetime
totals or proof of member delivery. Published learning counts are shown separately in details and
may include the approved synthetic baseline. Search matches the selected consolidated view.
Activity groups each console action's requested/completed steps into one entry while preserving all
steps in its detail record; a requested-only action remains explicitly incomplete.
The **Test** action opens a selected-style tester inside the portal, without opening the demo portal.
Choose **Chat**, **Voice** or an approved **Persona**. Voice asks for microphone permission and sends
bounded recordings; persona tests use the synthetic behaviour cards. Choose **First contact** or
**Second contact** for a style eligible at that stage. Second-contact personas bring a fictional
recap and a specific unresolved concern; the guide gives continued support rather than restarting
the whole introduction. The contact selector also applies to manual chat and voice style tests.
The opening AI
disclosure, authoritative value binding, content checks, numeric fidelity and human handover remain in use.
Each response shows the actual style used; an unavailable style is not silently replaced with another
explanation style. Pauses, eligibility and advice escalation remain enforced.
The tester identifies the style in its title rather than repeating introductory labels.
The portal's short synthetic-data label remains in the main header. The conversation scrolls
independently above a question composer with a separate **Send question** row. These controls
stay visible as answers accumulate; after a test ends they remain visible but disabled
until another test starts.

The persona demo's **Contact** selector offers the same first- and second-contact
stories for each persona, including Dutch-language support, a returning member
requesting a person, and an adversarial claim of earlier authorization.
It selects the story played by **Play**, not a stored conversation or proof of a
previous handover. The standalone **Voice** and **Chat** buttons still start an
ordinary first contact. The guide must not claim to remember a previous session;
official facts are checked again and every session includes the AI disclosure.
Changing contact type alone does not create repeat-contact outcome evidence.

The tester uses the existing Azure-hosted conversation bridge and internal synthetic issuer. It requires
the configured `gg-dev` issuer/voice channel and operator or owner access, not a separate demo-portal sign-in.
The orchestrator validates the delegated `Admin.Access` token, same origin and a scoped HttpOnly stream
cookie. Every test turn requires the admin token as well as a server-memory-only member bearer. Member
requests cannot set the test selection or resume a test as an ordinary member journey.
Closed test requests allow only a catalogue ID, fixed mode and optional approved simulation ID.
Test journeys persist only a catalogue ID marker; no test transcript is stored for learning. The feedback
projection excludes those journeys and their answer records from all counts, repeat-contact attribution
and source digests, including expert-control comparisons. Test answers cannot be linked into member
learning evidence. Closing the tester releases the microphone, audio context and stream.
Order details distinguish a changed published order, a matching published order after eligibility
and pauses, and fallback when no published order covers that situation. This does not establish
why an order stayed unchanged or claim improved outcomes. Actual
per-journey bounded exploration can differ; this page does not assign members to experiment arms.
The current state is not a full historical snapshot archive. Published learning counts may include
the approved synthetic baseline; the view distinguishes the manifest's observed, eligible and pending
live decisions rather than claiming that every aggregate interaction was observed live.

## Improvement ideas: declined answers and proposed fixes

A scheduled review (`job-gg-gap-review`, daily) looks at every explanation question whose answer was declined: it ended
in an advisor offer, or the answer review flagged it. Some of these answers were legitimate. The review groups them by
journey stage, topic, channel, accessibility mode and language, and proposes a fix when a situation has at least five
declined answers from three journeys. **Improvement ideas** lists the proposals; see
[Architecture](architecture.md#gap-review-declined-interactions-to-owner-reviewed-proposals) for the rules.

| Idea | What an owner decides |
|---|---|
| Missing approved content | Write the passage from an approved source, choose its stages, approve. |
| Approved content to revise | Edit a copy of the approved passage, approve to publish a new version. |
| Missing explanation style | Read the AI's reasoning, then edit and approve its recommended name, pattern and eligibility to queue it. If the AI says no approved pattern fits, acknowledge, defer or reject the described idea. |
| Answers refused that may have been fine | Open example journeys in **Conversations**, then acknowledge, defer or reject. No check is changed here. |

Readers can open every idea. Only owners decide. A decision needs a reason and explicit confirmation, shows the
proposal revision it was made on (a changed proposal returns HTTP 409), and is journalled before it takes effect, like
every other owner decision. **Approve** never makes a change live by itself:

- a style joins the single pending catalogue publication, which P2 signs and activates (see above);
- a passage is queued for the content publisher. Its status reads *Approved: waiting for the content publisher* until
  the publisher has indexed it, then *Published to approved content*. An operator starts the publisher with
  `python -m governedgraph.search.run_publisher_job --approved-proposals`.

An idea lists up to ten declined answers, one per journey, each with a link to its journey in **Conversations**, the outcome,
the reason, the member's question (owners only) and the approved passages found. *What was available* names the styles that
could explain and the passages used. A badge says whether the AI proposed the fix. An idea shows counts, labels and up to five of the questions members asked (kept for 30 days, shown to owners only) with example journey IDs;
open the journeys in **Conversations** to judge an answer. For missing or revisable content, an AI agent adds an analysis
(what members are asking, what the approved passages do not cover) and a draft passage written from the model's own
knowledge, labelled *Not checked by a person*. The draft can be wrong or out of date. The editor lists every claim it makes.
Verify each one against an authoritative source, edit the passage, and record that **source** (required) before approving.
Facts the model was unsure of are listed for you to supply. For a missing style, the AI reads the same questions and says
why the current styles do not suit them. It then either recommends an approved explanation pattern, which you can edit and
approve into the next signed release, or describes a new way of explaining that does not exist yet. You can only acknowledge,
defer or reject that second kind, because it has to be built. Without an AI answer no style idea is shown. A deferred idea returns to *Open* when its evidence changes; a rejected or
acknowledged idea stays closed. A decision whose journal was never completed counts only if its effect exists: an approval that
did land is shown as queued and cannot be rejected, and one that did not land leaves the idea open. Investigate any
*Requested* entry in **Activity**. Idea status is derived from the whole action journal (up to 5,000 blobs); a larger
journal fails closed with an explicit error instead of hiding a decision.

## Protective controls

- **Freeze** uses the existing Strategy Service protection: retain/restore the last good snapshot
  when available and prevent new publication.
- **Revert to static** clears the current learned ranking and freezes updates. Both arms use eligible,
  unsuspended expert priors.
- **Suspend** excludes an active strategy for every member in a selected state or all states. Supply
  the recorded audit reference, governance case reference, current threshold version and gap metric.
  This is an operator-entered protective measure, not a new fairness audit or a verified board decision.
- S6 human escalation cannot be suspended here.
- No signing, training or freeze-release action is exposed.
  Existing governance reinstatement/recovery routes remain separately protected.

Actions require a reason and explicit confirmation. The page submits the revision it displayed;
a changed state returns HTTP 409 and requires refresh/review. The journal is flushed before mutation
and records completion or failure separately. A completion-write failure is an **uncertain outcome**,
not success: refresh and investigate the action ID before retrying. Azure Blob state uses conditional writes; the UI handles conflicting revisions as HTTP 409.

## Owner overrides

Open an explanation style's **Details** while signed in as a platform owner:

| Action | Effect | Revocation |
|---|---|---|
| Reorder | Move approved styles up/down for one existing situation. The decision is **pending signature**, not active, until the next successful scheduled P2 publication signs it. | Returns to the **current** governed order, not a stale captured order. Pending decisions can also be revoked. |
| Pause / Pause override | Immediately pauses a style for everyone in a selected journey stage, or all stages. **Pause override** is shown only when there is already an effective pause. No invented fairness metric is required. | Removes only this owner's pause; independent automatic findings remain. |
| Unpause | Human Tier 2 approval to lift selected **existing** automatic findings without deleting them. An owner pause is unpaused by revoking it. | Restores the original finding if it still exists; newer findings are never suppressed. |

Every action requires a reason, explicit human confirmation and the displayed state revision. A `GB-`
decision reference is recorded in the existing immutable admin journal **before** execution. Activity keeps
requested, pending-signature, applied, failed and uncertain outcomes distinct. Style details and the status
column show active/pending overrides, with **Revoke override** beside each one. An existing override for the
same scope must be revoked before replacement. The journal's pending status records acceptance at that time.
Activity checks the live override state to show whether the order is still pending, active or no longer in force;
the original acceptance record is retained in details.

The Strategy API verifies **both** the delegated owner's `Admin.Access` token and a separate application token
from the pinned, journal-writing admin identity carrying its existing `Strategy.Suspend` role. An owner token
alone, an operator token, an app-only token or the fairness identity cannot submit these owner decisions.
The owner's token is forwarded only in memory and is never journalled. The proxy
uses its own managed identity, not an OBO secret. P2 retains exclusive signing and publication rights and can activate only an order matching
the recorded decision, catalogue versions and situation. The normal isolated audit and signing-isolation checks
must succeed first. Revocation or freeze racing with signing returns a conflict rather than reactivating a decision.

Signed owner orders affect the treatment arm only, without exploratory reordering; expert control remains unchanged.
Eligibility and effective pauses still filter both arms. New fairness findings invalidate overlapping unpause
overrides. Freeze and static revert revoke pending/active owner orders and unpause overrides; owner pauses remain.
They do not reactivate automatically after recovery. Missing fresh state cannot apply an unpause.
S6 human escalation remains always available and cannot be ranked or paused. Owner reasons stay in the
administrative journal; P2 reads only the closed, demographic-free decision scope, never reasons or conversations.

## Existing synthetic conversations from Foundry traces

Foundry traces backed by Application Insights can contain messages **only if they were captured**.
The existing framework exporter normally strips content. Its opt-in `gg-dev` diagnostic path records
only allowlisted synthetic members, with a synthetic watermark, journey ID and response ID.
This console does not enable capture, change retention or create a transcript archive.
Uncaptured, redacted, expired or un-ingested turns cannot be reconstructed.

Optional lookup uses the deployment's workspace GUID, Foundry project ARM ID
and environment name. `GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP=true` enables the deployed
console's read-only query under its own managed identity and existing authorized
workspace access. Enabling or widening that access requires a reviewed deployment.
No credentials enter the browser. Lookup runs only on an explicit inspection
request, not at startup or during an ordinary refresh.

Queries use workspace `AppDependencies`, exact project, exact journey, exact capture span,
synthetic flag and watermark, a seven-day window, a 30-second timeout and at most 100 displayed
captures. A 101st row signals truncation. Messages are validated, credential-screened again, displayed
as text (never HTML), and not written to files or fed to learning. Capture status and response
IDs are preserved. Missing configuration, denied access, timeouts and malformed data are explicit
errors. An empty successful query means **no captures found**, not no conversation.
Test source does not certify live workspace retrieval; validate the deployed integration.

References: [Microsoft tracing guidance](https://learn.microsoft.com/azure/foundry-classic/how-to/develop/trace-agents-sdk#instrument-tracing-in-your-code),
[Azure CLI log query](https://learn.microsoft.com/cli/azure/monitor/log-analytics?view=azure-cli-latest#az-monitor-log-analytics-query).

## Access control (Microsoft Entra groups)

Access control is required and set up by deployment
(see [Access control on Azure](#access-control-on-azure)). Missing group configuration
disables the console; there is no anonymous or local-loopback administration mode.
Groups define the three access levels:

| Variable | Level |
|---|---|
| `GOVERNEDGRAPH_ADMIN_READER_GROUP_ID` | View evidence |
| `GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID` | View evidence and apply protective actions |
| `GOVERNEDGRAPH_ADMIN_OWNER_GROUP_ID` | View evidence, apply protection and approve owner decisions |

Operators and owners can view without separate reader membership. Blank groups
never grant access. Sign-in also requires the configured Entra client and tenant,
the exact deployed redirect URI, the self-preauthorized `Admin.Access` scope and
group claims. No client secret is used.

Behaviour:

- The browser signs in with the authorization code flow and PKCE, requests only `api://<client-id>/Admin.Access`, and
  holds the resulting access token in page memory only. A page reload asks you to sign in again.
- The server verifies every data and action request: RS256 signature, tenant issuer, this app as audience, expiry,
  the `Admin.Access` delegated scope, and group membership. ID tokens, app-only tokens and tokens for other scopes
  are rejected.
- The journal records the signed-in user as `entra:<object-id>`.
- Any misconfiguration (a group or client ID that is not a GUID, or a missing client or tenant ID)
  blocks the console with an explanation instead of falling back to open access.
- The top bar shows who is signed in and whether the account is view-only.

Responses and conversation views are `no-store`; content security policy excludes
external assets, framing and inline scripts. Only temporary PKCE proof uses session
storage and is removed after sign-in. Tokens remain in page memory.

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
| Gateway route `/admin/` | Explicit routes only: page, assets, reads, the protective action, owner actions and the improvement-idea decision. Everything else is a 404 at the gateway. |
| Reader on `explainability-records`, `learning-aggregates`, `learning-evidence` | Container scope only. These hold immutable audit references, aggregates and committee/uplift evidence. |
| Contributor on `admin-actions` | Container scope. The container is a 30-day immutable store: each action step is a separate create-only blob. |
| Reader on `improvement-proposals`, contributor on `content-approvals` | Container scope. Reads gap-review proposals and hands owner-approved passages to the content publisher, create-only (30-day immutable). It has no access to Search or the approved-content container. |
| `Strategy.Freeze` and `Strategy.Suspend` app roles | Admin protective rights only; `Strategy.Suspend` also authenticates the journal-writing proxy on owner requests. It has no `Strategy.Publish` or signing rights. A separate verified human owner token is mandatory for overrides. |

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

1. creates distinct **reader**, **operator** and **owner** security groups (`GovernedGraph gg-dev admin readers-…`,
   `… admin operators-…` and `GovernedGraph gg-dev platform owners-…`);
2. adds the configured operator to the managed reader/operator groups and the **signed-in human running
   deployment** to the managed owner group. The owner is resolved through Graph `/me` in the selected
   subscription's tenant, not inferred from the configured operator or another tenant's CLI defaults;
3. registers a single-tenant single-page app (PKCE, no secret, no API permissions) with `…/admin/` as its redirect URI;
4. puts the groups in sign-in tokens, assigns all three groups to the app and requires assignment, so only group members can
   sign in at all;
5. records `GOVERNEDGRAPH_ADMIN_CLIENT_ID`, `…_READER_GROUP_ID`, `…_OPERATOR_GROUP_ID` and `…_OWNER_GROUP_ID` in the azd environment,
   from where provisioning passes them to the console.

The console then shows **Sign in with Microsoft**. Add more people by adding them to the groups in Entra; remove access the
same way. Owner membership grants console read/protective-action access and is shown beside the signed-in user.
Only owners receive the separate `can_own` authorization flag; operator membership alone does not grant it.
An owner-only configuration admits owners, not arbitrary signed-in tenant users. Workload configuration also passes
the owner group and admin audience to the Strategy Service for independent owner verification.
This does not configure the separate reinstatement group or freeze-recovery authorization, or grant the console
publication/signing rights. Owner decisions use the explicit dual-token workflow above.
Group membership does not grant Azure resource, directory, key or publication permissions.

| Setting | Meaning | Default |
|---|---|---|
| `GOVERNEDGRAPH_ADMIN_READER_GROUP_ID` / `…_OPERATOR_GROUP_ID` / `…_OWNER_GROUP_ID` | Supplied groups are used as given; their membership is never changed | created |
| `GOVERNEDGRAPH_ADMIN_GROUP_CLAIM` | `SecurityGroup` if the tenant lacks Entra ID P1/P2 (group assignment is skipped; the console still checks membership on every request) | `ApplicationGroup` |
| `GOVERNEDGRAPH_ADMIN_MEMBER_ID` | Configured reader/operator enrollment for standalone registration; owner enrollment still resolves the signed-in deploying human | the deploying user |

Open-console flags are not a supported execution mode. Required Entra authorization
must not be disabled to troubleshoot a deployment.

Review the plan without changing anything: `PYTHONPATH=src:. python -m scripts.configure_admin_console` after exporting the azd
environment (`set -a; eval "$(azd env get-values)"; set +a`). Sign-in asks for no consent and needs no admin approval,
because the app exposes and pre-authorizes its own `Admin.Access` scope, as the voice presenter does.

### Conversation captures on Azure

Conversation text in the console needs **two** switches, both off by default, and both must be on:

| Switch | Effect |
|---|---|
| `GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS=true` | The orchestrator records the text of **synthetic** `gg-dev` members' turns in Application Insights (credentials redacted, size capped, tagged synthetic; effective only with the presenter deployed). It is never a learning input. Only journeys that run **after** it is enabled are recorded. |
| `GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP=true` | Gives the admin identity **Log Analytics Reader on the workspace** so the console can query those spans (`azure-monitor-query`, seven-day window, 100 turns, synthetic-only filters). That role reads the whole workspace, not only the traces. |

Configure capture and lookup through the reviewed
[delivery process](deploying.md); new workspace-wide access requires explicit approval.
The reference presenter composition configures both together. With only lookup on, the console reports no captures
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
- Test doubles are not live Azure evidence. Verify the deployed
  console after each deployment: open the URL, refresh, and confirm every view loads.

## Validation

Run regression and live verification only in the authorized Azure environment.
Coverage includes Entra token/group validation, closed action contracts, durable
protection and journal failures, universal overlays, S6 availability, demographic-free
projections, digest-checked feedback, journey linkage and bounded synthetic trace
responses. Source coverage is not evidence that these checks have passed.
