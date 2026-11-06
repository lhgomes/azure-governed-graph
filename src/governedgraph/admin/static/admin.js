"use strict";

const $ = (id) => document.getElementById(id);
const session = document.querySelector('meta[name="admin-session"]').content;
const pageSize = 25;
let data = null;
let journeys = [];
let journeysTruncated = false;
let section = "overview";
let page = 0;
let actionRevision = "";
let loading = false;
let applying = false;
const sections = {
  overview: ["Governance overview", "Ranking oversight", "Effective rankings after universal suspension overlays. Leftmost strategy is first."],
  strategies: ["Strategy catalogue", "Approved strategies", "Versions, eligibility and explanation structure. Catalogue changes require human approval."],
  rankings: ["Ranking oversight", "Control and treatment", "Control retains expert priors. Treatment uses a verified snapshot where available. S6 is always separate."],
  suspensions: ["Vetoes & suspensions", "Active suspension overlay", "Each suspension applies to every member in the affected journey state, including the control arm."],
  cases: ["Governance cases", "Recorded fairness cases", "Cohort identities and demographic dimensions are excluded from this operator projection."],
  decisions: ["Assurance decisions", "Recorded evidence packs", "Automated Tier 1 review is not human board approval. Inspect individual verdicts and notification status."],
  learning: ["Learning evidence", "Published outcome aggregates", "Digest-verified learning inputs. No cohort attributes, conversation text or telemetry are used."],
  journeys: ["Journeys & evidence", "Response-linked journeys", "Inspect audit references and closed decisions; optionally retrieve existing synthetic diagnostic captures."],
  actions: ["Admin activity", "Protective action journal", "Requested and applied entries share an action ID. A requested-only action needs operator investigation."],
  configuration: ["Boundaries & configuration", "Authority and data sources", "Administration is isolated from member channels and from learning inputs."],
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}
function badge(text, tone = "") { return node("span", text, `badge ${tone}`); }
function button(text, action, className = "row-link") {
  const element = node("button", text, className);
  element.type = "button";
  element.addEventListener("click", action);
  return element;
}
function showError(error, id = "error") {
  $(id).textContent = error.message || String(error);
  $(id).hidden = false;
}
class AccessError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
let access = { mode: "disabled" };
let bearerToken = "";
const pkceKey = "gg-admin-pkce";

async function api(path, body) {
  const response = await fetch(new URL(path.replace(/^\//, ""), document.baseURI), {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin", cache: "no-store",
    headers: {
      "X-Admin-Session": session, ...(bearerToken ? { Authorization: `Bearer ${bearerToken}` } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  let result;
  try { result = await response.json(); }
  catch { throw new Error(`Administration returned an unreadable response (${response.status}). Refresh or check operator diagnostics.`); }
  if (!response.ok) {
    const message = typeof result.detail === "string" ? result.detail : `Request failed (${response.status}).`;
    throw new AccessError(response.status, message);
  }
  return result;
}

const base64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const authority = () => `https://login.microsoftonline.com/${access.tenant_id}/oauth2/v2.0`;
const redirectUri = () => new URL(".", document.baseURI).href;
// The console's own scope is pre-authorized by the registration, so sign-in needs no consent or admin approval.
const SIGN_IN_SCOPE = () => `api://${access.client_id}/Admin.Access`;

async function signIn(switchAccount = false) {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const state = base64url(crypto.getRandomValues(new Uint8Array(16)));
  const challenge = base64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  sessionStorage.setItem(pkceKey, JSON.stringify({ verifier, state }));
  const query = new URLSearchParams({
    client_id: access.client_id, response_type: "code", redirect_uri: redirectUri(), response_mode: "fragment",
    scope: SIGN_IN_SCOPE(), state, code_challenge: challenge, code_challenge_method: "S256",
    ...(switchAccount ? { prompt: "select_account" } : {}),
  });
  location.assign(`${authority()}/authorize?${query}`);
}

async function completeSignIn() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (!params.has("code") && !params.has("error")) return;
  const saved = JSON.parse(sessionStorage.getItem(pkceKey) || "null");
  sessionStorage.removeItem(pkceKey);
  history.replaceState(null, "", location.pathname);
  if (params.has("error")) throw new Error(`Microsoft sign-in did not complete: ${(params.get("error_description") || params.get("error")).split("\n")[0]}`);
  if (!saved || params.get("state") !== saved.state) throw new Error("The sign-in response did not match this browser session. Sign in again.");
  const response = await fetch(`${authority()}/token`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: access.client_id, grant_type: "authorization_code", code: params.get("code"),
      redirect_uri: redirectUri(), code_verifier: saved.verifier, scope: SIGN_IN_SCOPE(),
    }),
  });
  const tokens = await response.json().catch(() => ({}));
  if (!response.ok || !tokens.access_token) throw new Error("Microsoft did not return a sign-in token. Check the app registration's single-page redirect URI and its Admin.Access scope, then try again.");
  bearerToken = tokens.access_token;
}

function showGate(message, { signInButton = false, switchButton = false } = {}) {
  $("workspace").classList.add("locked");
  $("access-gate").hidden = false;
  $("gate-message").textContent = message;
  $("sign-in").hidden = !signInButton;
  $("switch-account").hidden = !switchButton;
  $("controls").disabled = true;
  $("sign-out").hidden = true;
  $("user").hidden = true;
}
function hideGate() {
  $("workspace").classList.remove("locked");
  $("access-gate").hidden = true;
}
function gateFor(error) {
  if (access.mode !== "enabled" || !(error instanceof AccessError) || ![401, 403].includes(error.status)) return false;
  if (error.status === 401) bearerToken = "";
  showGate(error.message, { signInButton: error.status === 401 || !bearerToken, switchButton: error.status === 403 });
  return true;
}
function jsonDetails(title, record) {
  $("detail-title").textContent = title;
  $("detail-body").replaceChildren(node("pre", JSON.stringify(record, null, 2)));
  $("detail-dialog").showModal();
}
function lane(ids) {
  const element = node("div", null, "rank-lane");
  if (!ids.length) return node("span", "No eligible resolution strategy", "cell-note");
  ids.forEach((id, index) => {
    const chip = node("span", null, "rank-chip");
    chip.append(node("span", index + 1, "rank-index"), node("span", id));
    element.append(chip);
  });
  return element;
}
function date(value) {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
function reference(title, subtitle, action) {
  const element = node("div");
  element.append(button(title, action));
  if (subtitle) element.append(node("span", subtitle, "cell-note"));
  return element;
}
function table(headers, rows, cells) {
  const wrap = node("div", null, "table-wrap");
  const element = node("table");
  const head = node("tr");
  headers.forEach((title) => { const th = node("th", title); th.scope = "col"; head.append(th); });
  const thead = node("thead"); thead.append(head); element.append(thead);
  const tbody = node("tbody");
  rows.forEach((row) => {
    const tr = node("tr");
    cells(row).forEach((value) => {
      const td = node("td");
      td.append(value instanceof Node ? value : node("span", value ?? "Not recorded"));
      tr.append(td);
    });
    tbody.append(tr);
  });
  element.append(tbody); wrap.append(element); return wrap;
}
function rankingDetail(row) {
  jsonDetails(`Ranking: ${row.request_key}`, {
    ...row, always_available: data.always_available,
    note: "Effective lists apply eligibility and universal suspensions. Per-journey bounded exploration may vary treatment order; actual served order is evidenced by journey records.",
    current_snapshot: data.state.current,
    last_good_snapshot: data.state.last_good,
  });
}
function strategyDetail(row) {
  jsonDetails(`${row.strategy_id}: ${row.name}`, {
    ...row,
    active_suspensions: data.state.suspensions.filter((item) => item.strategy_id === row.strategy_id),
    ranking_contexts: data.rankings.filter((item) => item.snapshot_order.includes(row.strategy_id)),
    human_escalation: row.strategy_id === "S6" ? "Always available; never ranked for resolution or suspended here." : undefined,
  });
}
async function journeyDetail(row) {
  $("detail-title").textContent = `Journey ${row.journey_id}`;
  const body = $("detail-body");
  body.replaceChildren(node("p", "Loading response-linked audit evidence..."));
  $("detail-dialog").showModal();
  try {
    const result = await api(`/api/journeys/${encodeURIComponent(row.journey_id)}`);
    body.replaceChildren(node("p", result.transcript_notice, "notice"));
    const actions = node("div", null, "detail-actions");
    const traces = button("Retrieve synthetic conversation captures", () => loadTraces(row.journey_id, traceArea, traces), "quiet");
    actions.append(traces);
    if (!data.trace_lookup_enabled) actions.append(node("p", "Trace lookup is not configured. See the setup guide; no conversation capture setting has been changed.", "cell-note"));
    const traceArea = node("div");
    body.append(actions, traceArea, node("h3", "Immutable response audit evidence"));
    const list = node("div", null, "evidence-list");
    result.records.forEach((record) => {
      const entry = node("article", null, "evidence-entry");
      entry.append(node("h3", `${record.strategy_id || "System"} / ${record.response_kind}`),
        node("p", `${date(record.created_at)} | ${record.arm} | ${record.snapshot_id}`, "cell-note"));
      const details = node("details"); details.append(node("summary", `Response ${record.response_id || "legacy"}: audit metadata`),
        node("pre", JSON.stringify(record, null, 2)));
      entry.append(details); list.append(entry);
    });
    body.append(list);
  } catch (error) { body.replaceChildren(node("p", error.message, "notice error")); }
}
async function loadTraces(journeyId, target, trigger) {
  trigger.disabled = true;
  target.replaceChildren(node("p", "Querying existing synthetic diagnostic captures..."));
  try {
    const result = await api(`/api/traces/${encodeURIComponent(journeyId)}`);
    target.replaceChildren(node("h3", "Synthetic conversation captures"), node("p", result.watermark, "watermark"),
      node("p", `${result.notice} ${result.window}.`, "notice"));
    if (!result.turns.length) target.append(node("p", "No captured turns found. Capture may have been disabled, withheld, delayed or outside retention. This does not mean no conversation occurred.", "empty"));
    result.turns.forEach((turn) => {
      target.append(node("p", `${date(turn.timestamp)} | Response ${turn.response_id} | ${turn.capture_status}`, "cell-note"));
      turn.messages.forEach((message) => {
        const entry = node("article", null, `evidence-entry ${message.role}`);
        entry.append(node("h3", message.role === "user" ? "Synthetic member" : "AI guide"), node("p", message.text));
        target.append(entry);
      });
    });
    if (result.truncated) target.append(node("p", "More than 100 captures exist. This view is truncated.", "notice"));
  } catch (error) { target.replaceChildren(node("p", error.message, "notice error")); }
  finally { trigger.disabled = false; }
}

function renderConfiguration() {
  const element = node("div", null, "configuration");
  const azure = data.mode === "azure";
  element.append(node("p", azure
    ? "This console reads the deployed governance stores and applies protective actions only through the role-protected Strategy API. Snapshot publishing, catalogue edits, reinstatement and human approval are not exposed."
    : "This browser operates on local synthetic governance state. Azure mutations, transcript capture changes, snapshot publishing, catalogue edits and human approval are not exposed."));
  const dl = node("dl");
  const open = data.access.mode !== "enabled";
  const pairs = [
    ["Catalogue version", data.catalogue_version],
    ["Evidence source profile", data.source_profile],
    ["Current snapshot", data.state.current?.snapshot_id || "Expert priors fallback"],
    ["Snapshot verification", data.state.current ? "Verified by the Strategy Service when it loaded the snapshot" : "No signed snapshot loaded"],
    ["Last good snapshot", data.state.last_good?.snapshot_id || "Not recorded"],
    ["Control arm share", data.control_arm_share],
    ["Always available", "S6: human advisor"],
    ["Synthetic trace lookup", data.trace_lookup_enabled ? (azure ? "Configured; Azure Monitor, administration identity" : "Configured; existing operator Azure CLI access") : "Not configured"],
    ["Human reinstatement", "Disabled in this console; use existing governance workflow"],
    ["Access control", !open
      ? "On: Microsoft Entra group membership (reader and operator levels)"
      : data.access.open_actions ? "Off: anyone who can open this console can view and act" : "Off: anyone who can open this console can view; protective actions are disabled"],
    ["Action actor", !open ? `${data.access.user} (verified Entra identity)` : azure ? "Anonymous console user (not a verified human identity)" : "Local loopback operator (not a verified human identity)"],
    ["Learning inputs", "Immutable outcome aggregates only. Neither admin activity nor transcripts feed learning."],
    ["Threshold configuration", JSON.stringify(data.thresholds)],
    ["Learning aggregate manifest", data.learning.manifest ? JSON.stringify(data.learning.manifest) : "No live feedback snapshot published"],
    ["Source scope", azure
      ? "Strategy Service state, active suspensions as governance cases, committee and uplift evidence, response audit records (sampled), learning aggregates and the immutable admin journal"
      : "Local strategy state, governance cases, committee evidence packs, explainability records and console action journal"],
  ];
  pairs.forEach(([key, value]) => dl.append(node("dt", key), node("dd", value)));
  element.append(dl); return element;
}

function render() {
  if (!data) return;
  const [title, heading, description] = sections[section];
  $("page-title").textContent = title; $("section-title").textContent = heading;
  $("section-description").textContent = description;
  if (section === "learning" && data.learning.manifest) {
    const manifest = data.learning.manifest;
    $("section-description").textContent += ` Counts include the approved synthetic baseline. Live observations: ${manifest.observed_decisions}; eligible: ${manifest.eligible_decisions}; pending: ${manifest.pending_decisions}.`;
  }
  if (section === "journeys" && journeysTruncated) {
    $("section-description").textContent += " The audit store is larger than this view's limit, so only a sample of records is shown.";
  }
  $("trace-lookup").hidden = section !== "journeys";
  $("page-description").textContent = section === "overview" ? "Trace every approved strategy from ranking to protective decision." : "Governed evidence, with authority kept explicit.";
  document.querySelectorAll("nav button").forEach((item) => {
    if (item.dataset.section === section) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  $("search").disabled = section === "configuration";
  if (section === "configuration") {
    $("content").replaceChildren(renderConfiguration()); $("pagination").hidden = true; $("result-count").textContent = ""; return;
  }
  const sources = {
    overview: data.rankings, strategies: data.strategies, rankings: data.rankings, suspensions: data.state.suspensions,
    cases: data.cases, decisions: data.decisions, learning: data.learning.aggregates, journeys, actions: [...data.actions].reverse(),
  };
  const query = $("search").value.trim().toLowerCase();
  const filtered = sources[section].filter((row) => JSON.stringify(row).toLowerCase().includes(query));
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  page = Math.min(page, totalPages - 1);
  const rows = filtered.slice(page * pageSize, (page + 1) * pageSize);
  $("result-count").textContent = `${filtered.length} ${filtered.length === 1 ? "record" : "records"}${query ? " matching filter" : ""}`;
  $("pagination").hidden = totalPages <= 1;
  $("page-number").textContent = `Page ${page + 1} of ${totalPages}`;
  $("previous").disabled = page === 0; $("next").disabled = page >= totalPages - 1;
  if (!rows.length) {
    const messages = {
      suspensions: "No active suspensions in the current state. This is not a fairness certification.",
      cases: "No governance cases recorded. Run the synthetic learning workflow to generate evidence.",
      decisions: "No assurance evidence recorded. Tier 0 publication does not create a Tier 1 committee pack.",
      learning: "No live feedback snapshot has been published. Seed fixtures are not presented as observed runtime outcomes.",
      journeys: "No journey audit records found. Run a journey or the sample demo to generate synthetic evidence.",
      actions: "No console actions recorded. Activity from other services is not part of this journal.",
    };
    $("content").replaceChildren(node("p", query ? "No records match this filter. Clear the search to see all records." : messages[section] || "No records are available in this view.", "empty"));
    return;
  }
  let content;
  if (section === "overview" || section === "rankings") {
    content = table(["Journey context", "Effective control", "Effective treatment", "Protection"], rows, (row) => [
      reference(row.request_key.split("|")[0].replaceAll("_", " "), row.request_key.split("|").slice(1).join(" / "), () => rankingDetail(row)),
      lane(row.effective_control), lane(row.effective_treatment),
      row.suspended.length ? badge(`Suspended: ${row.suspended.join(", ")}`, "bad") : badge(row.source, row.source === "signed snapshot" ? "good" : "warn"),
    ]);
  } else if (section === "strategies") {
    content = table(["Strategy", "Version", "Status", "Structure", "Eligibility"], rows, (row) => [
      reference(`${row.strategy_id} / ${row.name}`, row.template_id, () => strategyDetail(row)),
      row.version, badge(row.status, row.status === "active" ? "good" : "warn"),
      row.strategy_id === "S6" ? "Human advisor, always available" : row.teach_back ? "Explanation with teach-back" : "Explanation",
      row.eligible_journey_states.join(", "),
    ]);
  } else if (section === "suspensions") {
    content = table(["Strategy / state", "Audit", "Recorded gap", "Case", "Since"], rows, (row) => [
      reference(`${row.strategy_id} / ${row.journey_state}`, `Version ${row.strategy_version}; all members`, () => jsonDetails("Active suspension", row)),
      row.audit_id, `${row.gap_metric} points`, row.governance_case_id, date(row.created_at),
    ]);
  } else if (section === "cases") {
    content = table(["Case", "Strategy", "Audit", "Decision authority", "Created"], rows, (row) => [
      reference(row.case_id, row.type, () => jsonDetails(`Governance case ${row.case_id}`, row)),
      `${row.finding.strategy_id} / ${row.finding.request_key}`, row.audit_id, row.tier, date(row.created_at),
    ]);
  } else if (section === "decisions") {
    content = table(["Evidence", "Kind", "Authority", "Outcome", "Created"], rows, (row) => [
      reference(row.reference, row.kind === "uplift_window" ? "Graph versus control window" : `${row.verdicts?.length || 0} recorded checks`,
        () => jsonDetails(row.kind === "uplift_window" ? "Uplift evidence" : "Assurance evidence pack", row)),
      row.kind === "uplift_window" ? "B10 uplift" : "Tier 1 review", row.tier,
      badge(row.outcome.replaceAll("_", " "), ["approved", "continue"].includes(row.outcome) ? "good" : row.outcome === "keep_learning" ? "warn" : "bad"),
      date(row.created_at),
    ]);
  } else if (section === "learning") {
    content = table(["Strategy / context", "Interactions", "First-contact resolutions", "Repeat contacts", "Sample gate"], rows, (row) => [
      reference(`${row.strategy_id} / ${row.request_key.split("|")[0]}`, row.request_key.split("|").slice(1).join(" / "),
        () => jsonDetails("Published learning evidence", { ...row, manifest: data.learning.manifest })),
      row.interactions, row.resolved, row.repeat_contacts,
      badge(row.interactions >= data.thresholds.min_sample_per_strategy ? "Sample gate met" : "Expert prior retained",
        row.interactions >= data.thresholds.min_sample_per_strategy ? "good" : "warn"),
    ]);
  } else if (section === "journeys") {
    content = table(["Journey", "Synthetic member", "Responses", "Strategies", "Last activity"], rows, (row) => [
      reference(row.journey_id, "Inspect audit and synthetic captures", () => journeyDetail(row)), row.member_ref,
      row.responses, row.strategies.join(", "), date(row.last_activity),
    ]);
  } else {
    content = table(["Action", "Status", "Actor", "Reason", "Recorded"], rows, (row) => [
      reference(row.action.replaceAll("_", " "), row.action_id, () => jsonDetails("Console action evidence", row)),
      badge(row.status, row.status === "applied" ? "good" : row.status === "failed" ? "bad" : "warn"),
      row.actor, row.reason, date(row.created_at),
    ]);
  }
  $("content").replaceChildren(content);
}

async function refresh() {
  if (loading) return;
  loading = true; $("refresh").disabled = true; $("controls").disabled = true;
  $("error").hidden = true; $("content").setAttribute("aria-busy", "true");
  try {
    const [overview, evidence] = await Promise.all([api("/api/overview"), api("/api/journeys")]);
    data = overview; journeys = evidence.journeys; journeysTruncated = Boolean(evidence.truncated);
    const banner = $("state-banner");
    banner.classList.toggle("frozen", data.state.frozen);
    banner.replaceChildren(node("strong", data.state.frozen ? "Ranking frozen" : "Ranking updates enabled"),
      node("span", data.state.current ? `Verified snapshot ${data.state.current.snapshot_id}. Universal suspensions remain enforced.` : "Expert priors fallback is active. No signed learned snapshot is loaded."));
    $("summary").replaceChildren(...[
      [data.strategies.filter((row) => row.status === "active").length, "Approved active strategies"],
      [data.state.suspensions.length, "Active suspension entries"],
      [data.cases.length, "Recorded governance cases"],
      [journeys.length, journeysTruncated ? "Audited journeys (sample)" : "Audited journeys"],
    ].map(([value, label]) => { const item = node("div", null, "metric"); item.append(node("strong", value), node("span", label)); return item; }));
    $("updated").textContent = `Read at ${new Date().toLocaleTimeString()}`;
    const signedIn = data.access.mode === "enabled";
    const viewOnly = !data.access.can_act;
    $("user").hidden = !(signedIn || viewOnly); $("sign-out").hidden = !signedIn;
    $("user").textContent = signedIn ? `${data.access.user}${viewOnly ? " (view only)" : ""}` : "View only: protective actions are disabled";
    $("controls").disabled = viewOnly;
    $("action-scope").textContent = data.mode === "azure"
      ? "Changes apply to the deployed Strategy Service through its role-protected API. Every attempt is recorded in the immutable admin journal before execution."
      : "Changes apply to the local strategy state only. Every attempt is recorded before execution. No Azure resource will be changed.";
    render();
  } catch (error) {
    if (gateFor(error)) return;
    showError(error);
    $("state-banner").replaceChildren(node("strong", "Evidence unavailable"), node("span", "Do not rely on previously displayed state. Refresh after resolving the source error."));
    $("state-banner").classList.add("frozen");
  } finally { loading = false; $("refresh").disabled = false; $("content").setAttribute("aria-busy", "false"); }
}
function actionImpact() {
  const kind = $("action-kind").value;
  const suspend = kind === "suspend";
  $("suspension-fields").hidden = !suspend; $("suspension-fields").disabled = !suspend;
  $("action-impact").textContent = {
    freeze: "Stops snapshot publication and restores the retained last good snapshot when available. It does not lift suspensions. Recovery requires the existing human-governed workflow.",
    revert_to_static: "Removes the current learned ranking and freezes updates. Every member receives eligible, unsuspended expert priors. Existing suspensions remain in effect.",
    suspend: "Excludes this strategy for every member in the selected journey state, in both arms. Human escalation remains available. Reinstatement is not available here.",
  }[kind];
}
function openControls() {
  if (!data || loading) return;
  $("action-form").reset(); $("action-error").hidden = true;
  actionRevision = data.revision;
  $("strategy-id").replaceChildren(...data.strategies.filter((row) => row.strategy_id !== "S6" && row.status === "active")
    .map((row) => { const option = node("option", `${row.strategy_id} / ${row.name}`); option.value = row.strategy_id; return option; }));
  $("journey-state").replaceChildren(...["*", ...new Set(data.rankings.map((row) => row.request_key.split("|")[0]))]
    .map((state) => { const option = node("option", state === "*" ? "All journey states" : state); option.value = state; return option; }));
  $("threshold-version").textContent = data.thresholds.threshold_version;
  actionImpact(); $("action-dialog").showModal();
}
async function applyAction(event) {
  event.preventDefault();
  if (applying) return;
  applying = true; $("apply-action").disabled = true; $("close-action").disabled = true; $("action-error").hidden = true;
  const payload = { action: $("action-kind").value, expected_revision: actionRevision, reason: $("action-reason").value };
  if (payload.action === "suspend") {
    const strategy = data.strategies.find((row) => row.strategy_id === $("strategy-id").value);
    payload.suspension = {
      strategy_id: strategy.strategy_id, strategy_version: strategy.version, journey_state: $("journey-state").value,
      audit_id: $("audit-id").value.trim(), governance_case_id: $("case-id").value.trim(),
      gap_metric: Number($("gap-metric").value), threshold_version: data.thresholds.threshold_version,
    };
  }
  try {
    const result = await api("/api/actions", payload);
    $("action-dialog").close();
    $("notification").textContent = `Protective action applied. Journal reference: ${result.action_id}.`;
    $("notification").hidden = false;
    await refresh();
  } catch (error) {
    if (error instanceof AccessError && error.status === 401 && gateFor(error)) { $("action-dialog").close(); return; }
    showError(error, "action-error");
  }
  finally { applying = false; $("apply-action").disabled = false; $("close-action").disabled = false; }
}
document.querySelectorAll("nav button").forEach((item) => item.addEventListener("click", () => {
  section = item.dataset.section; page = 0; $("search").value = ""; render();
}));
$("search").addEventListener("input", () => { page = 0; render(); });
$("previous").addEventListener("click", () => { page -= 1; render(); });
$("next").addEventListener("click", () => { page += 1; render(); });
$("refresh").addEventListener("click", refresh);
$("controls").addEventListener("click", openControls);
$("close-detail").addEventListener("click", () => $("detail-dialog").close());
$("close-action").addEventListener("click", () => $("action-dialog").close());
$("action-dialog").addEventListener("cancel", (event) => { if (applying) event.preventDefault(); });
$("action-kind").addEventListener("change", actionImpact);
$("action-form").addEventListener("submit", applyAction);
$("trace-lookup").addEventListener("submit", (event) => {
  event.preventDefault();
  const journeyId = $("trace-journey").value;
  $("detail-title").textContent = `Synthetic captures: ${journeyId}`;
  const target = node("div");
  const trigger = button("Refresh synthetic captures", () => loadTraces(journeyId, target, trigger), "quiet");
  $("detail-body").replaceChildren(node("p", "Diagnostic captures are separate from local audit records. No complete transcript or delivery proof is implied.", "notice"), trigger, target);
  $("detail-dialog").showModal();
  loadTraces(journeyId, target, trigger);
});
$("sign-in").addEventListener("click", () => signIn());
$("switch-account").addEventListener("click", () => signIn(true));
$("sign-out").addEventListener("click", () => {
  bearerToken = ""; data = null;
  showGate("You signed out of the console. Your Microsoft session may remain active.", { signInButton: true, switchButton: true });
});
async function start() {
  try {
    access = await api("/api/access");
    $("access-banner").hidden = access.mode !== "disabled";
    if (access.mode === "disabled") {
      $("access-banner-text").textContent = access.open_actions === false
        ? "Anyone who can reach this console can view all evidence. Protective actions are disabled until access control is configured."
        : "Anyone who can open this console can view all evidence and apply protective actions.";
    }
    if (access.mode === "misconfigured") { showGate(access.detail); return; }
    if (access.mode === "enabled") {
      try { await completeSignIn(); }
      catch (error) { showGate(error.message, { signInButton: true }); return; }
      if (!bearerToken) {
        showGate("Access to this console is limited to members of the configured Microsoft Entra groups.", { signInButton: true });
        return;
      }
    }
    hideGate();
    await refresh();
  } catch (error) { showError(error); }
}
start();
