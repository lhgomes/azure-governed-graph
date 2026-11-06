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
const G = window.GUIDE;
const sections = Object.fromEntries(Object.entries(G.sections).map(([key, item]) => [key, [item.title, item.heading, ""]]));
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage may be blocked; the guide still works */ } },
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
// Everyday wording helpers: turn stored names such as first_contact into text a person would write.
const humanize = (value) => {
  const text = String(value ?? "").replaceAll("_", " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "Not recorded";
};
const percent = (part, whole) => (whole ? `${Math.round((part / whole) * 100)}%` : "n/a");
const styleName = (id) => data?.strategies.find((row) => row.strategy_id === id)?.name || id;
const styleList = (ids) => (ids.length ? ids.map((id, index) => `${index + 1}. ${styleName(id)}`).join("\n") : "None available");
function situation(requestKey) {
  const parts = String(requestKey).split("|");
  if (parts.length !== 4) return { title: humanize(requestKey), note: "" };
  return { title: humanize(parts[0]), note: `Topic: ${humanize(parts[1]).toLowerCase()} · Channel: ${parts[2]} · Accessibility: ${parts[3]}` };
}

// A small "?" button that opens a short explanation next to it. Works with keyboard and touch.
const helpPopover = $("help-popover");
let helpAnchor = null;
function closeHelp() {
  helpPopover.hidden = true;
  if (helpAnchor) { helpAnchor.setAttribute("aria-expanded", "false"); helpAnchor = null; }
}
function openHelp(anchor, heading, text, also) {
  if (helpAnchor === anchor) { closeHelp(); anchor.focus(); return; }
  closeHelp();
  helpPopover.replaceChildren(node("strong", heading), node("p", text), ...(also ? [node("p", also, "also")] : []));
  helpPopover.hidden = false; helpAnchor = anchor; anchor.setAttribute("aria-expanded", "true");
  const box = anchor.getBoundingClientRect();
  const width = Math.min(340, window.innerWidth - 24);
  helpPopover.style.width = `${width}px`;
  helpPopover.style.left = `${Math.max(12, Math.min(window.scrollX + box.left, window.scrollX + window.innerWidth - width - 12))}px`;
  helpPopover.style.top = `${window.scrollY + box.bottom + 8}px`;
}
function helpButton(heading, text, also) {
  const element = node("button", "?", "help");
  element.type = "button";
  element.setAttribute("aria-label", `What does "${heading}" mean?`);
  element.setAttribute("aria-expanded", "false");
  element.setAttribute("aria-controls", "help-popover");
  element.addEventListener("click", (event) => { event.stopPropagation(); openHelp(element, heading, text, also); });
  return element;
}
function termChip(id) {
  const term = G.terms[id];
  const element = node("button", term.name, "term");
  element.type = "button";
  element.setAttribute("aria-expanded", "false");
  element.setAttribute("aria-controls", "help-popover");
  element.addEventListener("click", (event) => { event.stopPropagation(); openHelp(element, term.name, term.plain, term.also); });
  return element;
}
document.addEventListener("click", (event) => { if (!helpPopover.hidden && !helpPopover.contains(event.target)) closeHelp(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !helpPopover.hidden) { const anchor = helpAnchor; closeHelp(); anchor?.focus(); } });
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
// Plain-language detail view: a short explanation and a list of facts first, the raw record only on request.
function showDetails(title, intro, facts, record) {
  $("detail-title").textContent = title;
  const body = node("div");
  if (intro) body.append(node("p", intro, "lead"));
  const list = node("dl", null, "facts");
  facts.filter(([, value]) => value !== undefined && value !== null && value !== "").forEach(([key, value]) => {
    const dd = node("dd", value);
    list.append(node("dt", key), dd);
  });
  if (facts.length) body.append(list);
  if (record) {
    const technical = node("details", null, "technical");
    technical.append(node("summary", "Technical details (for support)"), node("pre", JSON.stringify(record, null, 2)));
    body.append(technical);
  }
  $("detail-body").replaceChildren(body);
  $("detail-dialog").showModal();
}
function lane(ids) {
  const element = node("div", null, "rank-lane");
  if (!ids.length) return node("span", "No eligible style", "cell-note");
  ids.forEach((id, index) => {
    const chip = node("span", null, "rank-chip");
    chip.title = id;
    chip.append(node("span", index + 1, "rank-index"), node("span", styleName(id)));
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
// Columns are [label, help text]; a "?" button explains each one.
function table(columns, rows, cells) {
  const wrap = node("div", null, "table-wrap");
  const element = node("table");
  const head = node("tr");
  columns.forEach(([label, help]) => {
    const th = node("th"); th.scope = "col";
    th.append(node("span", label), ...(help ? [helpButton(label, help)] : []));
    head.append(th);
  });
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
  const place = situation(row.request_key);
  const paused = row.suspended.map(styleName).join(", ");
  showDetails(`Situation: ${place.title}`,
    "This is the order in which explanation styles are tried in this situation. The first style is tried first.",
    [
      ["Situation", `${place.title}. ${place.note}`.trim()],
      ["Expert order", styleList(row.effective_control)],
      ["Learned order", styleList(row.effective_treatment)],
      ["Paused here", paused || "Nothing is paused in this situation"],
      ["Where the learned order comes from", row.source === "signed snapshot" ? "A published order whose signature was checked" : "No learned order yet, so the expert order is used"],
      ["Good to know", "Both orders already skip paused styles. The system sometimes tries a different style so it can keep learning, so a member's actual answer can differ a little. The Member journeys page shows what was really used."],
      ["Always available", "The human advisor"],
    ],
    { ...row, always_available: data.always_available, current_snapshot: data.state.current, last_good_snapshot: data.state.last_good });
}
function strategyDetail(row) {
  const pausedHere = data.state.suspensions.filter((item) => item.strategy_id === row.strategy_id);
  showDetails(row.name,
    row.strategy_id === "S6" ? "This is the human advisor option. It is always available, is never ranked, and can never be paused."
      : "An approved way of explaining. People approved it, and it only changes after a person approves a new version.",
    [
      ["Reference", row.strategy_id],
      ["Version", row.version],
      ["Status", humanize(row.status)],
      ["How it explains", row.strategy_id === "S6" ? "Offers a call with a person" : row.teach_back ? "Explains step by step, then asks a check-back question" : "Explains in one go"],
      ["Where it can be used", row.eligible_journey_states.map(humanize).join(", ")],
      ["Channels", (row.channels || []).join(", ")],
      ["Paused now", pausedHere.length ? pausedHere.map((item) => humanize(item.journey_state)).join(", ") : "Not paused anywhere"],
      ["Used in these situations", data.rankings.filter((item) => item.snapshot_order.includes(row.strategy_id)).length || "None yet"],
    ],
    { ...row, active_suspensions: pausedHere });
}
function pauseDetail(row) {
  showDetails(`${styleName(row.strategy_id)}: paused`,
    "This style is switched off for everyone at this stage of the journey, in both the expert order and the learned order.",
    [
      ["Style", `${styleName(row.strategy_id)} (${row.strategy_id}, version ${row.strategy_version})`],
      ["Stage of the journey", humanize(row.journey_state)],
      ["Paused since", date(row.created_at)],
      ["Gap found", `${row.gap_metric} percentage points. The limit is ${data.thresholds.max_cohort_gap_pts}.`],
      ["Check reference", row.audit_id],
      ["Fairness review", row.governance_case_id],
      ["Bringing it back", "Needs a person with approval rights. It cannot be done from this console."],
    ], row);
}
function reviewDetail(row) {
  const finding = row.finding || {};
  showDetails(`Fairness review ${row.case_id}`,
    "A fairness check found that a style worked noticeably worse for some group, so the style was paused automatically. This review keeps the evidence. No group is named.",
    [
      ["Review", row.case_id],
      ["What happened", G.labels.caseType[row.type] || humanize(row.type)],
      ["Style", finding.strategy_id ? `${styleName(finding.strategy_id)} (${finding.strategy_id})` : undefined],
      ["Situation", finding.request_key ? `${situation(finding.request_key).title}. ${situation(finding.request_key).note}`.trim() : undefined],
      ["Check reference", row.audit_id],
      ["Who can bring it back", G.labels.levels[row.tier] || humanize(row.tier)],
      ["Created", date(row.created_at)],
    ], row);
}
function outcomeDetail(row) {
  const uplift = row.kind === "uplift_window";
  const checks = (row.verdicts || []).map((item) => `${item.passed ? "Passed" : "Did not pass"}: ${item.note || humanize(item.agent)}`).join("\n");
  showDetails(uplift ? "Is learning beating the expert order?" : `Checks on ${row.reference}`,
    uplift ? "This compares the learned order with the expert order over a period of time. Learning is only kept if it does better."
      : "Before a new order is used, automatic checks look at it. This shows each check and whether it passed. It is not a human approval.",
    [
      ["Reference", row.reference],
      ["What was checked", G.labels.kinds[row.kind] || humanize(row.kind)],
      ["Result", G.labels.outcome[row.outcome] || humanize(row.outcome)],
      ["Level", G.labels.levels[row.tier] || humanize(row.tier)],
      ["Checks", checks],
      ["When", date(row.created_at)],
    ], row);
}
function learningDetail(row) {
  const enough = row.interactions >= data.thresholds.min_sample_per_strategy;
  const place = situation(row.request_key);
  showDetails(`${styleName(row.strategy_id)}: results so far`,
    "Totals only. They hold no names and no conversation text. The system uses them to decide whether to change the order.",
    [
      ["Style", `${styleName(row.strategy_id)} (${row.strategy_id})`],
      ["Situation", `${place.title}. ${place.note}`.trim()],
      ["Times used", row.interactions],
      ["Settled first time", `${row.resolved} (${percent(row.resolved, row.interactions)})`],
      ["Came back within 14 days", `${row.repeat_contacts} (${percent(row.repeat_contacts, row.interactions)})`],
      ["Enough data to learn?", enough ? `Yes. It has been used at least ${data.thresholds.min_sample_per_strategy} times.` : `Not yet. The system needs at least ${data.thresholds.min_sample_per_strategy} uses before it can change the order, so the expert order stays.`],
    ], { ...row, manifest: data.learning.manifest });
}
function actionDetail(row) {
  showDetails(`Safety action: ${(G.actions[row.action]?.label || humanize(row.action))}`,
    "Every use of the safety controls is written down before it happens, and again when it finishes.",
    [
      ["What was done", G.actions[row.action]?.label || humanize(row.action)],
      ["Status", G.labels.status[row.status] || humanize(row.status)],
      ["Who", row.actor],
      ["Why", row.reason],
      ["When", date(row.created_at)],
      ["Reference", row.action_id],
      ["If it says Requested", row.status === "requested" ? "It was written down but never marked as finished. Ask a technical operator to check what happened." : undefined],
    ], row);
}
async function journeyDetail(row) {
  $("detail-title").textContent = `Journey ${row.journey_id}`;
  const body = $("detail-body");
  body.replaceChildren(node("p", "Loading the answer records..."));
  $("detail-dialog").showModal();
  try {
    const result = await api(`/api/journeys/${encodeURIComponent(row.journey_id)}`);
    body.replaceChildren(node("p", "One member's conversation. Below is the saved record for each answer and, if switched on, the example conversation itself. Only made-up members appear here.", "lead"),
      node("p", "Answer records never contain conversation text. The example conversation comes from a separate source.", "notice"));
    const actions = node("div", null, "detail-actions");
    const traces = button("Show the example conversation", () => loadTraces(row.journey_id, traceArea, traces), "quiet");
    actions.append(traces);
    if (!data.trace_lookup_enabled) actions.append(node("p", "Showing the conversation is not switched on, so this may find nothing. Ask your technical team to enable it. Nothing was changed.", "cell-note"));
    const traceArea = node("div");
    body.append(actions, traceArea, node("h3", "Answer records"));
    const list = node("div", null, "evidence-list");
    result.records.forEach((record) => {
      const entry = node("article", null, "evidence-entry");
      entry.append(node("h3", `${record.strategy_id ? styleName(record.strategy_id) : "System message"} · ${humanize(record.response_kind)}`),
        node("p", `${date(record.created_at)} · ${record.arm === "control" ? "Expert order" : record.arm === "treatment" ? "Learned order" : humanize(record.arm)} · order ${record.snapshot_id}`, "cell-note"));
      const details = node("details", null, "technical"); details.append(node("summary", "Technical details for this answer"),
        node("pre", JSON.stringify(record, null, 2)));
      entry.append(details); list.append(entry);
    });
    body.append(list);
  } catch (error) { body.replaceChildren(node("p", error.message, "notice error")); }
}
async function loadTraces(journeyId, target, trigger) {
  trigger.disabled = true;
  target.replaceChildren(node("p", "Looking for the example conversation..."));
  try {
    const result = await api(`/api/traces/${encodeURIComponent(journeyId)}`);
    target.replaceChildren(node("h3", "Example conversation"), node("p", result.watermark, "watermark"),
      node("p", `${result.notice} ${result.window}.`, "notice"));
    if (!result.turns.length) target.append(node("p", "No conversation found. Capture may have been off, delayed or past its retention time. This does not mean no conversation took place.", "empty"));
    result.turns.forEach((turn) => {
      target.append(node("p", `${date(turn.timestamp)} · answer ${turn.response_id} · ${humanize(turn.capture_status)}`, "cell-note"));
      turn.messages.forEach((message) => {
        const entry = node("article", null, `evidence-entry ${message.role}`);
        entry.append(node("h3", message.role === "user" ? "Made-up member" : "AI guide"), node("p", message.text));
        target.append(entry);
      });
    });
    if (result.truncated) target.append(node("p", "There are more than 100 turns. Only the first 100 are shown.", "notice"));
  } catch (error) { target.replaceChildren(node("p", error.message, "notice error")); }
  finally { trigger.disabled = false; }
}

function renderConfiguration() {
  const element = node("div", null, "configuration");
  const azure = data.mode === "azure";
  const open = data.access.mode !== "enabled";
  const t = data.thresholds;
  const canAct = open ? data.access.open_actions : data.access.can_act;
  element.append(node("h3", "What this console can do"), node("ul", null, "checks"));
  [
    ["Show everything the system has learned and decided, in plain words", true],
    [canAct ? "Pause learning, go back to the expert order, or pause one style" : "Use the safety controls (not available to you right now)", canAct],
    ["Edit, add or remove explanation styles", false],
    ["Publish a new order or change an AI model", false],
    ["Bring a paused style back or restart learning", false],
    ["Use anything seen here for learning", false],
  ].forEach(([text, yes]) => {
    const item = node("li", null, yes ? "yes" : "no");
    item.append(node("span", yes ? "Can" : "Cannot", "mark"), node("span", text));
    element.lastChild.append(item);
  });
  element.append(node("h3", "Settings in use now"));
  const dl = node("dl");
  const pairs = [
    ["Where the data comes from", azure ? "The live deployment (read-only, except the safety controls)" : "Example data on this computer. No Azure service is touched."],
    ["Approved styles", `Version ${data.catalogue_version}`],
    ["Order in use", data.state.current ? `A published order (${data.state.current.snapshot_id}). Its signature was checked when it was loaded.` : "The expert order. No learned order is in use."],
    ["Last good order", data.state.last_good?.snapshot_id || "None recorded"],
    ["Learning", data.state.frozen ? "Paused" : "On"],
    ["Members on the expert order", `${Math.round(data.control_arm_share * 100)}% always get it, so the learned order must prove it is better`],
    ["Always available", "The human advisor"],
    ["Showing example conversations", data.trace_lookup_enabled ? "On" : "Off. Ask your technical team to switch it on."],
    ["Who can use this console", !open ? "Only people in the approved groups. They can look, and some can use the safety controls."
      : data.access.open_actions ? "Anyone who can open this page can look and use the safety controls (sign-in is off)."
        : "Anyone who can open this page can look. The safety controls are switched off until sign-in is turned on."],
    ["Who is recorded when you act", !open ? `${data.access.user} (signed in)` : "Nobody can be named, because sign-in is off"],
    ["What learning uses", "Only outcome counts. Never this console's activity, and never conversation text."],
    ["Limits learning must stay within", [
      `A style needs at least ${t.min_sample_per_strategy} uses before the order can change.`,
      `The order can move a style by at most ${t.max_rank_shift_per_cycle} places per update.`,
      `A group needs at least ${t.min_cell_size} people before fairness is checked for it.`,
      `A style is paused if it works more than ${t.max_cohort_gap_pts} points worse for a group.`,
      `About ${Math.round(t.exploration_rate * 100)}% of answers in the learned group try a different order, so the system can keep learning.`,
    ].join("\n")],
    ["Learning data published", data.learning.manifest
      ? `Real observations so far: ${data.learning.manifest.observed_decisions}. Counted: ${data.learning.manifest.eligible_decisions}. Still waiting: ${data.learning.manifest.pending_decisions}.`
      : "None yet. Example numbers are never shown as real results."],
  ];
  pairs.forEach(([key, value]) => { const dd = node("dd", value); dl.append(node("dt", key), dd); });
  element.append(dl);
  const technical = node("details", null, "technical");
  technical.append(node("summary", "Technical settings (for support)"), node("pre", JSON.stringify({
    source_profile: data.source_profile, thresholds: data.thresholds, learning_manifest: data.learning.manifest,
    scope: azure ? "Strategy Service state, active suspensions as governance cases, committee and uplift evidence, response audit records (sampled), learning aggregates and the immutable admin journal"
      : "Local strategy state, governance cases, committee evidence packs, explainability records and console action journal",
  }, null, 2)));
  element.append(technical);
  return element;
}

function render() {
  if (!data) return;
  const [title, heading, description] = sections[section];
  $("page-title").textContent = title; $("section-title").textContent = heading;
  $("section-description").textContent = description;
  if (section === "learning" && data.learning.manifest) {
    const manifest = data.learning.manifest;
    $("section-description").textContent += ` The totals include the approved example numbers. Real observations so far: ${manifest.observed_decisions}. Counted: ${manifest.eligible_decisions}. Still waiting: ${manifest.pending_decisions}.`;
  }
  if (section === "journeys" && journeysTruncated) {
    $("section-description").textContent += " There are more records than this page can hold, so only a sample is shown.";
  }
  $("trace-lookup").hidden = section !== "journeys";
  $("welcome").hidden = section !== "overview" || store.get("gg-admin-welcomed") === "yes";
  $("page-description").textContent = G.sections[section].description;
  renderGuide();
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
  $("result-count").textContent = `${filtered.length} ${filtered.length === 1 ? "item" : "items"}${query ? " match your search" : ""}`;
  $("pagination").hidden = totalPages <= 1;
  $("page-number").textContent = `Page ${page + 1} of ${totalPages}`;
  $("previous").disabled = page === 0; $("next").disabled = page >= totalPages - 1;
  if (!rows.length) {
    $("content").replaceChildren(node("p", query ? "Nothing matches your search. Clear the search box to see everything." : G.sections[section].empty || "Nothing to show here yet.", "empty"));
    return;
  }
  const columns = G.sections[section].columns;
  let content;
  if (section === "overview" || section === "rankings") {
    content = table(columns, rows, (row) => {
      const place = situation(row.request_key);
      return [
        reference(place.title, place.note, () => rankingDetail(row)),
        lane(row.effective_control), lane(row.effective_treatment),
        row.suspended.length ? badge(`Paused: ${row.suspended.map(styleName).join(", ")}`, "bad")
          : row.source === "signed snapshot" ? badge("Using learned order", "good") : badge("Using expert order", "warn"),
      ];
    });
  } else if (section === "strategies") {
    content = table(columns, rows, (row) => [
      reference(row.name, row.strategy_id, () => strategyDetail(row)),
      row.version, badge(humanize(row.status), row.status === "active" ? "good" : "warn"),
      row.strategy_id === "S6" ? "A person, always available" : row.teach_back ? "Step by step, then a check-back question" : "Explains in one go",
      row.eligible_journey_states.map(humanize).join(", "),
    ]);
  } else if (section === "suspensions") {
    content = table(columns, rows, (row) => [
      reference(styleName(row.strategy_id), `${humanize(row.journey_state)} · everyone`, () => pauseDetail(row)),
      row.audit_id, `${row.gap_metric} points`, row.governance_case_id, date(row.created_at),
    ]);
  } else if (section === "cases") {
    content = table(columns, rows, (row) => [
      reference(row.case_id, G.labels.caseType[row.type] || humanize(row.type), () => reviewDetail(row)),
      `${styleName(row.finding.strategy_id)} · ${situation(row.finding.request_key).title}`, row.audit_id,
      G.labels.levels[row.tier] || humanize(row.tier), date(row.created_at),
    ]);
  } else if (section === "decisions") {
    content = table(columns, rows, (row) => [
      reference(row.reference, row.kind === "uplift_window" ? "Learned order against expert order" : `${row.verdicts?.length || 0} checks`, () => outcomeDetail(row)),
      G.labels.kinds[row.kind] || humanize(row.kind), G.labels.levels[row.tier] || humanize(row.tier),
      badge(G.labels.outcome[row.outcome] || humanize(row.outcome), ["approved", "continue"].includes(row.outcome) ? "good" : row.outcome === "keep_learning" ? "warn" : "bad"),
      date(row.created_at),
    ]);
  } else if (section === "learning") {
    content = table(columns, rows, (row) => {
      const place = situation(row.request_key);
      const enough = row.interactions >= data.thresholds.min_sample_per_strategy;
      return [
        reference(styleName(row.strategy_id), `${place.title}${place.note ? ` · ${place.note}` : ""}`, () => learningDetail(row)),
        row.interactions, `${row.resolved} (${percent(row.resolved, row.interactions)})`, `${row.repeat_contacts} (${percent(row.repeat_contacts, row.interactions)})`,
        badge(enough ? "Enough data to learn" : "Not enough yet (expert order stays)", enough ? "good" : "warn"),
      ];
    });
  } else if (section === "journeys") {
    content = table(columns, rows, (row) => [
      reference(row.journey_id, "Select to see each answer", () => journeyDetail(row)), row.member_ref,
      row.responses, row.strategies.map(styleName).join(", "), date(row.last_activity),
    ]);
  } else {
    content = table(columns, rows, (row) => [
      reference(G.actions[row.action]?.label || humanize(row.action), row.action_id, () => actionDetail(row)),
      badge(G.labels.status[row.status] || humanize(row.status), row.status === "applied" ? "good" : row.status === "failed" ? "bad" : "warn"),
      row.actor, row.reason, date(row.created_at),
    ]);
  }
  $("content").replaceChildren(content);
}
// "About this page": what it shows, what to look for, what you can do, and the words used on it.
function renderGuide() {
  const guide = G.sections[section];
  const body = node("div", null, "guide-grid");
  [["What you are looking at", guide.what], ["What to look for", guide.look], ["What you can do", guide.can]].forEach(([heading, text]) => {
    const block = node("div"); block.append(node("h3", heading), node("p", text)); body.append(block);
  });
  const words = node("div", null, "guide-words");
  words.append(node("h3", "Words on this page"));
  const chips = node("div", null, "chips");
  guide.terms.forEach((id) => chips.append(termChip(id)));
  words.append(chips);
  body.append(words);
  $("guide-body").replaceChildren(body);
}
function renderHelp() {
  const body = node("div", null, "help-content");
  body.append(node("p", G.about, "lead"));
  body.append(node("h3", "Start here"));
  const steps = node("ol"); G.startHere.forEach((text) => steps.append(node("li", text))); body.append(steps);
  body.append(node("h3", "The pages"));
  const pages = node("ul", null, "page-list");
  document.querySelectorAll("nav button").forEach((item) => {
    const entry = node("li");
    entry.append(button(item.firstChild.textContent, () => { $("help-dialog").close(); item.click(); }), node("span", ` ${item.querySelector("small").textContent}`, "cell-note"));
    pages.append(entry);
  });
  body.append(pages);
  body.append(node("h3", "Common tasks"));
  const tasks = node("div", null, "tasks");
  G.tasks.forEach((task) => {
    const card = node("div", null, "task");
    card.append(node("strong", task.title));
    const list = node("ol"); task.steps.forEach((step) => list.append(node("li", step))); card.append(list);
    tasks.append(card);
  });
  body.append(tasks);
  body.append(node("h3", "What this console will never do"));
  const promises = node("ul"); G.promises.forEach((text) => promises.append(node("li", text))); body.append(promises);
  body.append(node("h3", "Words used here"));
  const glossary = node("dl", null, "glossary");
  Object.values(G.terms).sort((a, b) => a.name.localeCompare(b.name)).forEach((term) => {
    glossary.append(node("dt", term.name), node("dd", term.also ? `${term.plain} ${term.also}` : term.plain));
  });
  body.append(glossary);
  $("help-body").replaceChildren(body);
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
    banner.replaceChildren(node("strong", data.state.frozen ? "Learning is paused" : "Learning is on"),
      node("span", data.state.frozen
        ? "No new learned order will be published. Paused styles stay paused."
        : data.state.current ? `Using a published order whose signature was checked (${data.state.current.snapshot_id}). Paused styles stay paused.`
          : "No learned order is in use yet, so everyone gets the expert order."));
    $("summary").replaceChildren(...[
      [data.strategies.filter((row) => row.status === "active").length, "Explanation styles in use", "Explanation styles that are switched on."],
      [data.state.suspensions.length, "Styles paused", "Styles switched off for everyone at one or more stages of the journey."],
      [data.cases.length, "Fairness reviews on record", "Records made each time the fairness check paused a style."],
      [journeys.length, journeysTruncated ? "Member journeys (a sample)" : "Member journeys", "Conversations with made-up members that appear in the saved answer records."],
    ].map(([value, label, help]) => {
      const item = node("div", null, "metric"); const caption = node("span", label);
      caption.append(helpButton(label, help)); item.append(node("strong", value), caption); return item;
    }));
    $("updated").textContent = `Updated at ${new Date().toLocaleTimeString()}`;
    const signedIn = data.access.mode === "enabled";
    const viewOnly = !data.access.can_act;
    $("user").hidden = !(signedIn || viewOnly); $("sign-out").hidden = !signedIn;
    $("user").textContent = signedIn ? `${data.access.user}${viewOnly ? " (can look, cannot act)" : ""}` : "Look only: the safety controls are off";
    $("controls").disabled = viewOnly;
    $("action-scope").textContent = data.mode === "azure"
      ? "This changes the live system, through its protected service. Each action is saved in a record that cannot be altered, before it runs."
      : "This changes the example system on this computer only. Nothing in Azure is touched. Each action is saved before it runs.";
    render();
  } catch (error) {
    if (gateFor(error)) return;
    showError(error);
    $("state-banner").replaceChildren(node("strong", "Could not read the data"), node("span", "Do not rely on what was shown before. Press Refresh once the problem is fixed."));
    $("state-banner").classList.add("frozen");
  } finally { loading = false; $("refresh").disabled = false; $("content").setAttribute("aria-busy", "false"); }
}
function actionImpact() {
  const kind = $("action-kind").value;
  const suspend = kind === "suspend";
  $("suspension-fields").hidden = !suspend; $("suspension-fields").disabled = !suspend;
  $("action-impact").textContent = G.actions[kind].impact;
}
function openControls() {
  if (!data || loading) return;
  $("action-form").reset(); $("action-error").hidden = true;
  actionRevision = data.revision;
  $("strategy-id").replaceChildren(...data.strategies.filter((row) => row.strategy_id !== "S6" && row.status === "active")
    .map((row) => { const option = node("option", `${row.name} (${row.strategy_id})`); option.value = row.strategy_id; return option; }));
  $("journey-state").replaceChildren(...["*", ...new Set(data.rankings.map((row) => row.request_key.split("|")[0]))]
    .map((state) => { const option = node("option", state === "*" ? "All stages" : humanize(state)); option.value = state; return option; }));
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
    $("notification").textContent = `Done. The safety action was applied. Reference: ${result.action_id}. It is on the Action history page.`;
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
$("help-open").addEventListener("click", () => { closeHelp(); renderHelp(); $("help-dialog").showModal(); });
$("close-help").addEventListener("click", () => $("help-dialog").close());
$("welcome-tour").addEventListener("click", () => { store.set("gg-admin-welcomed", "yes"); $("welcome").hidden = true; $("help-open").click(); });
$("welcome-dismiss").addEventListener("click", () => { store.set("gg-admin-welcomed", "yes"); $("welcome").hidden = true; });
// The page guide starts open for new visitors and stays however the visitor last left it.
$("guide").open = store.get("gg-admin-guide") !== "closed";
$("guide").addEventListener("toggle", () => store.set("gg-admin-guide", $("guide").open ? "open" : "closed"));
$("welcome-text").textContent = G.welcome;
$("welcome").hidden = store.get("gg-admin-welcomed") === "yes";
$("close-detail").addEventListener("click", () => $("detail-dialog").close());
$("close-action").addEventListener("click", () => $("action-dialog").close());
$("action-dialog").addEventListener("cancel", (event) => { if (applying) event.preventDefault(); });
$("action-kind").addEventListener("change", actionImpact);
$("action-form").addEventListener("submit", applyAction);
$("trace-lookup").addEventListener("submit", (event) => {
  event.preventDefault();
  const journeyId = $("trace-journey").value;
  $("detail-title").textContent = `Example conversation: ${journeyId}`;
  const target = node("div");
  const trigger = button("Look again", () => loadTraces(journeyId, target, trigger), "quiet");
  $("detail-body").replaceChildren(node("p", "These example conversations are kept apart from the saved answer records. They are not a full transcript, and they do not prove an answer reached the member.", "notice"), trigger, target);
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
        ? "Anyone who can reach this page can see everything. The safety controls are off until sign-in is turned on."
        : "Anyone who can open this page can see everything and use the safety controls.";
    }
    if (access.mode === "misconfigured") { showGate(access.detail); return; }
    if (access.mode === "enabled") {
      try { await completeSignIn(); }
      catch (error) { showGate(error.message, { signInButton: true }); return; }
      if (!bearerToken) {
        showGate("Only people in the approved groups can use this console. Sign in with your work account.", { signInButton: true });
        return;
      }
    }
    hideGate();
    await refresh();
  } catch (error) { showError(error); }
}
start();
