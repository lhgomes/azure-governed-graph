"use strict";

const $ = (id) => document.getElementById(id);
const session = document.querySelector('meta[name="admin-session"]').content;
const pageSize = 25;
let data = null;
let journeys = [];
let journeysTruncated = false;
let strategyUsage = {};
let improvements = { proposals: [], batches: [], stages: [], truncated: false, error: "" };
let section = "strategies";
let page = 0;
let actionRevision = "";
let loading = false;
let applying = false;
let ownerDraft = null;
let activeTest = null;
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
  return {
    title: `${humanize(parts[0])} - ${humanize(parts[2])} - ${humanize(parts[3])}`,
    note: `Topic: ${humanize(parts[1]).toLowerCase()}`,
  };
}
function sameOrder(left, right) {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}
function rankingComparison(row) {
  if (row.source === "owner override") return { changed: !sameOrder(row.effective_control, row.effective_treatment),
    label: "Owner order", note: "A human owner chose this signed order. This does not show better outcomes." };
  const published = row.source === "signed snapshot";
  const changed = !sameOrder(row.effective_control, row.effective_treatment);
  if (!row.effective_treatment.length) return { changed, ...G.ranking.unavailable };
  if (!published) return { changed, ...G.ranking.fallback };
  return { changed, ...(changed ? G.ranking.changed : G.ranking.unchanged) };
}
function strategyState(row) {
  if (data.always_available.includes(row.strategy_id)) return { label: "Always available", tone: "good" };
  if (row.status !== "active") return { label: humanize(row.status), tone: "warn" };
  const pauses = effectivePauses(row.strategy_id);
  if (!pauses.length) return { label: "Active", tone: "good" };
  const stages = new Set(pauses.map((item) => item.journey_state));
  const allPaused = stages.has("*") || row.eligible_journey_states.every((state) => stages.has(state));
  return { label: allPaused ? "Paused" : "Partly paused", tone: "bad" };
}
const stageNames = (states) => states.includes("*") ? "All stages" : states.map(humanize).join(", ");
const explanation = (row) => data.always_available.includes(row.strategy_id) ? "A person, always available"
  : data.style_patterns?.find((pattern) => pattern.template_id === row.template_id)?.name
    || (row.teach_back ? "Step by step, then a check-back question" : "Explains in one go");
const effectivePauses = (id) => (data.effective_pauses || data.state.suspensions).filter((item) => item.strategy_id === id);
const ownerOverrides = (id) => (data.state.owner_overrides || []).filter((item) => item.strategy_id === id || item.order.includes(id));
function strategyRows() {
  return data.strategies.filter((row) => row.status !== "retired").map((row) => ({
    ...row, availability: strategyState(row), recorded_uses: strategyUsage[row.strategy_id] || 0,
    pauses: effectivePauses(row.strategy_id), overrides: ownerOverrides(row.strategy_id),
  }));
}
function actionState(row) {
  const label = G.labels.status[row.status] || humanize(row.status);
  const tone = row.status === "applied" ? "good" : row.status === "failed" ? "bad" : "warn";
  if (row.status === "pending_publication" && row.proposal) {
    const idea = improvements.proposals.find((item) => item.status.decision_ref === row.action_id);
    return idea?.status.code === "published" ? { label: "Published to approved content", tone: "good" } : { label, tone };
  }
  if (row.action.startsWith("owner_catalogue_") && ["applied", "pending_signature"].includes(row.status)) {
    if (row.action === "owner_catalogue_cancel") return { label: "Publication cancelled", tone: "good" };
    if (data.state.catalogue_release?.decision_ref === row.action_id) return { label: "Published", tone: "good" };
    return data.state.catalogue_pending?.decision_ref === row.action_id
      ? { label: "Publication queued", tone: "warn" } : { label: "Superseded or cancelled", tone: "" };
  }
  if (!row.action.startsWith("owner_") || !["applied", "pending_signature"].includes(row.status)) return { label, tone };
  if (row.action === "owner_revoke") return { label: "Override revoked", tone: "good" };
  const current = (data.state.owner_overrides || []).find((item) => item.override_id === row.action_id);
  if (!current) return { label: "No longer in force", tone: "" };
  return current.kind === "order" && !current.snapshot ? { label: "Pending signature", tone: "warn" }
    : { label: "Override active", tone: "good" };
}
function activityRows() {
  const actions = new Map();
  for (const record of data.actions) {
    const records = actions.get(record.action_id) || [];
    records.push(record); actions.set(record.action_id, records);
  }
  const rows = [...actions.values()].map((records) => {
    const row = records.find((item) => item.status !== "requested") || records[0];
    const state = actionState(row);
    return {
      kind: "action", reference: row.action_id, title: G.actions[row.action]?.label || G.ownerActions[row.action] || humanize(row.action),
      status: state.label, tone: state.tone,
      note: row.reason, created_at: row.completed_at || row.created_at, record: row, records,
    };
  });
  rows.push(...data.cases.map((row) => ({
    kind: "case", reference: row.case_id, title: "Fairness review",
    status: G.labels.levels[row.tier] || humanize(row.tier), tone: "warn",
    note: row.finding?.strategy_id ? styleName(row.finding.strategy_id) : "See review",
    created_at: row.created_at, record: row,
  })), ...data.decisions.map((row) => ({
    kind: "decision", reference: row.reference, title: G.labels.kinds[row.kind] || humanize(row.kind),
    status: G.labels.outcome[row.outcome] || humanize(row.outcome),
    tone: ["approved", "continue"].includes(row.outcome) ? "good" : row.outcome === "keep_learning" ? "warn" : "bad",
    note: G.labels.levels[row.tier] || humanize(row.tier), created_at: row.created_at, record: row,
  })));
  return rows.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
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
  if (activeTest) { activeTest.stop(); activeTest = null; $("detail-dialog").close(); }
  if ($("style-dialog").open) $("style-dialog").close();
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
  activeTest?.stop(); activeTest = null;
  $("detail-dialog").classList.remove("testing-dialog", "idea-dialog");
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
  const comparison = rankingComparison(row);
  showDetails(`Situation: ${place.title}`,
    "This is the order in which explanation styles are tried in this situation. The first style is tried first.",
    [
      ["Situation", `${place.title}. ${place.note}`.trim()],
      ["Expert order", styleList(row.effective_control)],
      ["Learned group starting order", styleList(row.effective_treatment)],
      ["Compared with expert order", `${comparison.label}. ${comparison.note}`],
      ["Paused here", paused || "Nothing is paused in this situation"],
      ["Where the learned order comes from", row.source === "signed snapshot" ? "A published order whose signature was checked" : "No learned order yet, so the expert order is used"],
      ["Good to know", "Both orders already skip paused styles. The system sometimes tries a different style so it can keep learning, so a member's actual answer can differ a little. The Conversations page shows what was really used."],
      ["Always available", "The human advisor"],
    ],
    { ...row, always_available: data.always_available, current_snapshot: data.state.current, last_good_snapshot: data.state.last_good });
}
function strategyDetail(row) {
  const pausedHere = effectivePauses(row.strategy_id);
  const availability = strategyState(row);
  showDetails(row.name,
    data.always_available.includes(row.strategy_id) ? G.styleDetail.advisor : G.styleDetail.intro,
    [
      ["Reference", row.strategy_id],
      ["Version", row.version],
      ["Status", availability.label],
      ["How it explains", explanation(row)],
      ["Where it can be used", stageNames(row.eligible_journey_states)],
      ["Channels", (row.channels || []).join(", ")],
      ["Recorded uses", `${strategyUsage[row.strategy_id] || 0}${journeysTruncated ? " in the loaded sample, not a lifetime total" : " in the saved answer records"}`],
      ["Paused now", pausedHere.length ? stageNames(pausedHere.map((item) => item.journey_state)) : "Not paused anywhere"],
    ],
    { ...row, active_suspensions: pausedHere });
  const body = $("detail-body");
  const summary = node("section");
  summary.append(...body.children, strategyActions(row));
  if (!data.always_available.includes(row.strategy_id)) {
    const editing = node("div", null, "strategy-actions");
    const edit = button("Edit style", () => openStyleEditor("edit", row), "quiet");
    const remove = button("Remove style", () => openStyleEditor("remove", row), "quiet");
    edit.disabled = remove.disabled = !data.access.can_own || Boolean(data.state.catalogue_pending);
    editing.append(edit, remove);
    summary.append(editing);
  }
  const overrides = ownerOverrides(row.strategy_id);
  if (overrides.length) {
    summary.append(node("h3", "Owner overrides"));
    for (const item of overrides) {
      const line = node("div", null, "override-entry");
      const scope = item.kind === "order" ? situation(item.request_key).title : stageNames([item.journey_state]);
      line.append(node("strong", `${humanize(item.kind)}: ${scope}`),
        node("span", `${item.kind === "order" && !item.snapshot ? "Pending signature" : "Active"} (${item.override_id})`, "cell-note"));
      const revoke = button("Revoke override...", () => openOwnerDialog("revoke", row, item), "quiet");
      revoke.disabled = !data.access.can_own || loading || applying;
      line.append(revoke); summary.append(line);
    }
  }
  const orders = data.rankings.filter((item) => item.expert_priors.includes(row.strategy_id) || item.snapshot_order.includes(row.strategy_id));
  const position = (ids) => ids.includes(row.strategy_id) ? `${ids.indexOf(row.strategy_id) + 1} of ${ids.length}` : "Not available here";
  const orderPanel = node("section");
  if (orders.length) orderPanel.append(table(G.styleDetail.orderColumns, orders, (item) => [
    reference(situation(item.request_key).title, situation(item.request_key).note, () => rankingDetail(item)),
    position(item.effective_control), position(item.effective_treatment), rankingComparison(item).label,
  ]));
  else orderPanel.append(node("p", data.always_available.includes(row.strategy_id) ? G.styleDetail.advisor : G.styleDetail.noOrders, "cell-note"));
  if (pausedHere.length) {
    summary.append(node("h3", "Pause details"), table(G.styleDetail.pauseColumns, pausedHere, (item) => [
      reference(item.journey_state === "*" ? "All stages" : humanize(item.journey_state), item.audit_id, () => pauseDetail(item)),
      item.governance_case_id, date(item.created_at),
    ]));
  }
  const results = data.learning.aggregates.filter((item) => item.strategy_id === row.strategy_id);
  const learningPanel = node("section");
  learningPanel.append(node("p", G.styleDetail.learningNote, "cell-note"));
  if (results.length) learningPanel.append(table(G.styleDetail.learningColumns, results, (item) => [
    reference(situation(item.request_key).title, situation(item.request_key).note, () => learningDetail(item)),
    item.interactions, `${item.resolved} (${percent(item.resolved, item.interactions)})`,
    `${item.repeat_contacts} (${percent(item.repeat_contacts, item.interactions)})`,
  ]));
  else learningPanel.append(node("p", "No published learning results for this style.", "cell-note"));
  detailTabs([["Summary", summary], ["Order by situation", orderPanel], ["Learning results", learningPanel]]);
}
function detailTabs(items) {
  const tablist = node("div", null, "detail-tabs");
  tablist.setAttribute("role", "tablist");
  tablist.setAttribute("aria-label", "Explanation style details");
  const tabs = [];
  function select(index, focus = false) {
    items.forEach(([, panel], item) => {
      panel.hidden = item !== index;
      tabs[item].setAttribute("aria-selected", String(item === index));
      tabs[item].tabIndex = item === index ? 0 : -1;
    });
    if (focus) tabs[index].focus();
  }
  items.forEach(([label, panel], index) => {
    const tab = button(label, () => select(index), "detail-tab");
    tab.id = `style-tab-${index}`; panel.id = `style-panel-${index}`;
    tab.setAttribute("role", "tab"); tab.setAttribute("aria-controls", panel.id);
    panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", tab.id);
    panel.tabIndex = 0;
    tab.addEventListener("keydown", (event) => {
      const positions = { ArrowRight: (index + 1) % items.length, ArrowLeft: (index + items.length - 1) % items.length,
        Home: 0, End: items.length - 1 };
      if (event.key in positions) { event.preventDefault(); select(positions[event.key], true); }
    });
    tabs.push(tab); tablist.append(tab);
  });
  $("detail-body").replaceChildren(tablist, ...items.map(([, panel]) => panel));
  select(0);
}
function strategyActions(row) {
  const actions = node("div", null, "strategy-actions");
  const pause = button(data.access.can_own && /paused/i.test(strategyState(row).label) ? "Pause override" : "Pause", () => {
    if (data.access.can_own) { openOwnerDialog("pause", row); return; }
    $("detail-dialog").close();
    openControls();
    $("action-kind").value = "suspend";
    $("strategy-id").value = row.strategy_id;
    actionImpact();
  }, "quiet");
  pause.disabled = !data.access.can_act || loading || row.status !== "active" || data.always_available.includes(row.strategy_id);
  pause.title = pause.disabled ? "Requires operator access and an active explanation style. The human advisor cannot be paused."
    : data.access.can_own ? "Record your owner decision and choose where the pause applies." : "Choose the stage and provide the pause evidence.";
  const reorder = button("Reorder...", () => openOwnerDialog("order", row), "quiet");
  const ownerDisabled = !data.access.can_own || loading || applying || row.status !== "active"
    || data.always_available.includes(row.strategy_id);
  reorder.disabled = ownerDisabled || data.state.frozen;
  reorder.title = G.styleDetail.reorder;
  const unpause = button("Unpause...", () => openOwnerDialog("unpause", row), "quiet");
  unpause.disabled = ownerDisabled || !effectivePauses(row.strategy_id).length;
  unpause.title = G.styleDetail.unpause;
  actions.append(pause, reorder, unpause, button("Test...", () => testDetail(row), "quiet"),
    node("p", G.styleDetail.changeLimits, "cell-note"));
  return actions;
}
function testDetail(row) {
  showDetails(`Test: ${row.name}`, "", []);
  $("detail-dialog").classList.add("testing-dialog");
  activeTest = new window.AdminStyleTest(row, (path, body) => api(`../admin-testing${path}`, body));
  $("detail-body").replaceChildren(activeTest.element);
}
function pauseDetail(row) {
  if (row.source === "owner") {
    showDetails("Owner pause", "A human owner paused this style. Revoking this override removes only this owner's pause.",
      [["Style", styleName(row.strategy_id)], ["Stage", stageNames([row.journey_state])],
        ["Decision", row.override_id], ["Since", date(row.created_at)]], row);
    const revoke = button("Revoke override...", () => openOwnerDialog("revoke",
      data.strategies.find((item) => item.strategy_id === row.strategy_id), row), "quiet");
    revoke.disabled = !data.access.can_own;
    $("detail-body").append(revoke); return;
  }
  showDetails(`${styleName(row.strategy_id)}: paused`,
    "This style is switched off for everyone at this stage of the journey, in both the expert order and the learned order.",
    [
      ["Style", `${styleName(row.strategy_id)} (${row.strategy_id}, version ${row.strategy_version})`],
      ["Stage of the journey", humanize(row.journey_state)],
      ["Paused since", date(row.created_at)],
      ["Gap found", `${row.gap_metric} percentage points. The limit is ${data.thresholds.max_cohort_gap_pts}.`],
      ["Check reference", row.audit_id],
      ["Fairness review", row.governance_case_id],
      ["Bringing it back", "An owner can approve an unpause override in this style's Details. New findings can pause it again."],
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
function actionDetail(row, records = [row]) {
  const owner = row.action.startsWith("owner_") || Boolean(row.proposal);
  showDetails(`${owner ? "Owner decision" : "Safety action"}: ${(G.actions[row.action]?.label || G.ownerActions[row.action] || humanize(row.action))}`,
    "Every decision is written down before execution, and again when its outcome is known. Current override status is checked against the live state.",
    [
      ["What was done", G.actions[row.action]?.label || G.ownerActions[row.action] || humanize(row.action)],
      ["Status", actionState(row).label],
      ["Situation", row.owner_decision?.request_key ? situation(row.owner_decision.request_key).title : undefined],
      ["Style", row.owner_decision?.strategy_id ? styleName(row.owner_decision.strategy_id) : undefined],
      ["Stage", row.owner_decision?.journey_state ? stageNames([row.owner_decision.journey_state]) : undefined],
      ["Revoked override", row.owner_decision?.override_id],
      ["Improvement idea", row.proposal?.proposal_id],
      ["Content version", row.document_id],
      ["Who", row.actor],
      ["Why", row.reason],
      ["When", date(row.created_at)],
      ["Reference", row.action_id],
      ["If it says Requested", row.status === "requested" ? "It was written down but never marked as finished. Ask a technical operator to check what happened." : undefined],
    ], { ...row, journal_steps: records });
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
    target.replaceChildren(node("h3", "Example conversation"),
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

function renderStyleManagement() {
  const target = $("style-management");
  target.hidden = section !== "strategies";
  target.replaceChildren();
  if (target.hidden) return;
  const add = button("Add style", () => openStyleEditor("add"), "quiet");
  add.disabled = !data.access.can_own || Boolean(data.state.catalogue_pending);
  target.append(add);
  const pending = data.state.catalogue_pending;
  if (pending) {
    target.append(node("p", data.state.frozen
      ? "Catalogue publication is waiting for protective freeze recovery. The current styles remain active."
      : "Catalogue publication is queued for the next successful signed learning job. The current styles remain active.",
    "cell-note"));
    const cancel = button("Cancel publication", () => openStyleEditor("cancel"), "quiet");
    cancel.disabled = !data.access.can_own;
    target.append(cancel);
  }
}

function openStyleEditor(action, style) {
  if (!data.access.can_own) {
    showError(new Error("An owner must approve catalogue changes."));
    return;
  }
  $("style-editor-title").textContent = { add: "Add explanation style", edit: "Edit explanation style",
    remove: "Remove explanation style", cancel: "Cancel catalogue publication" }[action];
  window.AdminStyleEditor.mount($("style-editor"), { action, style, data, api,
    saved: async (result) => {
      $("style-dialog").close();
      if ($("detail-dialog").open) $("detail-dialog").close();
      $("notification").textContent = result.status === "pending_signature"
        ? `Publication queued (${result.decision_ref}). The current styles stay active until the signed release is published.`
        : `Publication cancelled (${result.decision_ref}).`;
      $("notification").hidden = false;
      await refresh();
    },
  });
  $("style-dialog").showModal();
}

function improvementNote() {
  if (improvements.error) return improvements.error;
  const last = improvements.batches[0];
  const base = last ? `Last review ${date(last.created_at)}: ${last.declined_signals} declined answers from ${last.records_read} saved records.`
    : "No review has run yet.";
  return base + (improvements.truncated ? " There are more ideas than this page can hold." : "");
}
async function improvementDetail(row) {
  activeTest?.stop(); activeTest = null;
  $("detail-dialog").classList.remove("testing-dialog");
  $("detail-dialog").classList.add("idea-dialog");
  $("detail-title").textContent = window.AdminImprovements.kinds[row.kind];
  const target = node("div");
  target.append(node("p", "Loading the idea...", "lead idea-loading"));
  $("detail-body").replaceChildren(target);
  $("detail-dialog").showModal();
  try {
    const extra = await api(`/api/proposals/${row.proposal_id}`);
    window.AdminImprovements.mount(target, { row: extra.proposal, extra, data, api,
      openJourney: (journeyId) => { $("detail-dialog").classList.remove("idea-dialog"); journeyDetail({ journey_id: journeyId }); },
      saved: async (result, decision) => {
        $("detail-dialog").close();
        $("notification").textContent = decision === "approve"
          ? `Approved and queued (${result.decision_ref}). Nothing changes until it is ${row.kind === "strategy_gap" ? "signed and released" : "published"}.`
          : `Decision recorded (${result.decision_ref}).`;
        $("notification").hidden = false;
        await refresh();
      } });
  } catch (error) { target.replaceChildren(node("p", error.message, "notice error")); }
}

function render() {
  if (!data) return;
  const [title, heading, description] = sections[section];
  $("page-title").textContent = title; $("section-title").textContent = heading;
  $("section-description").textContent = description;
  $("state-banner").hidden = section !== "strategies" && !data.state.frozen;
  if (section === "strategies") $("section-description").textContent = journeysTruncated
    ? "Recorded uses cover the loaded answer-record sample only, not lifetime usage."
    : "Recorded uses count saved answer records. They do not prove delivery to a member.";
  if (section === "journeys" && journeysTruncated) {
    $("section-description").textContent += " There are more records than this page can hold, so only a sample is shown.";
  }
  if (section === "improvements") $("section-description").textContent = improvementNote();
  $("trace-lookup").hidden = section !== "journeys";
  renderStyleManagement();
  $("page-description").textContent = G.sections[section].description;
  renderGuide();
  document.querySelectorAll("nav button").forEach((item) => {
    if (item.dataset.section === section) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  const sources = {
    strategies: strategyRows(), journeys, improvements: improvements.proposals, activity: activityRows(),
  };
  const query = $("search").value.trim().toLowerCase();
  const filtered = sources[section].filter((row) => JSON.stringify(row).toLowerCase().includes(query));
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  page = Math.min(page, totalPages - 1);
  const rows = filtered.slice(page * pageSize, (page + 1) * pageSize);
  const unit = section === "strategies" ? "styles" : section === "journeys" ? "conversations"
    : section === "improvements" ? "ideas" : "entries";
  $("result-count").textContent = `${filtered.length} of ${sources[section].length} ${unit}${query ? " match your search" : ""}`;
  $("pagination").hidden = totalPages <= 1;
  $("page-number").textContent = `Page ${page + 1} of ${totalPages}`;
  $("previous").disabled = page === 0; $("next").disabled = page >= totalPages - 1;
  if (!rows.length) {
    $("content").replaceChildren(node("p", query ? "Nothing matches your search. Clear the search box to see everything." : G.sections[section].empty || "Nothing to show here yet.", "empty"));
    return;
  }
  const columns = G.sections[section].columns;
  let content;
  if (section === "strategies") {
    content = table(columns, rows, (row) => {
      const actions = node("div", null, "row-actions");
      actions.append(button("Details", () => strategyDetail(row), "quiet"), button("Test...", () => testDetail(row), "quiet"));
      const where = node("div", stageNames(row.eligible_journey_states));
      where.append(node("span", (row.channels || []).map(humanize).join(", "), "cell-note"));
      const status = node("div");
      status.append(badge(row.availability.label, row.availability.tone));
      if (row.overrides.length) {
        status.append(node("span", row.overrides.some((item) => item.kind === "order" && !item.snapshot)
          ? "Owner order pending signature" : "Owner override active", "cell-note"));
      }
      if (row.pauses.length) status.append(node("span", `Paused: ${stageNames(row.pauses.map((item) => item.journey_state))}`, "cell-note"));
      return [
        reference(row.name, row.strategy_id, () => strategyDetail(row)), status, explanation(row), where,
        `${row.recorded_uses}${journeysTruncated ? " (sample)" : ""}`, actions,
      ];
    });
  } else if (section === "journeys") {
    content = table(columns, rows, (row) => [
      reference(row.journey_id, "Select to see each answer", () => journeyDetail(row)), row.member_ref,
      row.responses, row.strategies.map(styleName).join(", "), date(row.last_activity),
    ]);
  } else if (section === "improvements") {
    content = table(columns, rows, (row) => [
      reference(window.AdminImprovements.label(row), window.AdminImprovements.kinds[row.kind], () => improvementDetail(row)),
      badge(row.status.label, window.AdminImprovements.tones[row.status.code] || ""),
      `${row.evidence.declined} from ${row.evidence.journeys} journeys`,
      date(row.created_at),
    ]);
  } else {
    content = table(columns, rows, (row) => {
      const inspect = () => row.kind === "case" ? reviewDetail(row.record)
        : row.kind === "decision" ? outcomeDetail(row.record) : actionDetail(row.record, row.records);
      return [
        reference(row.title, row.reference, inspect),
        row.kind === "case" ? "Fairness review" : row.kind === "decision" ? "Order check" : "Console action",
        badge(row.status, row.tone), row.note, date(row.created_at),
      ];
    });
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
    const [overview, evidence, ideas] = await Promise.all([api("/api/overview"), api("/api/journeys"),
      api("/api/proposals").catch((error) => {
        if (error instanceof AccessError && [401, 403].includes(error.status)) throw error;
        return { proposals: [], batches: [], stages: [], truncated: false, error: error.message };
      })]);
    if (!evidence.strategy_usage || typeof evidence.strategy_usage !== "object" || Array.isArray(evidence.strategy_usage)
        || Object.values(evidence.strategy_usage).some((value) => !Number.isInteger(value) || value < 0)) {
      throw new Error("Recorded usage is unavailable or invalid. Refresh after checking the administration service.");
    }
    if (overview.state.owner_overrides?.length && !Array.isArray(overview.effective_pauses)) {
      throw new Error("Owner override availability is missing. Refresh after checking the administration service.");
    }
    data = overview; journeys = evidence.journeys; journeysTruncated = Boolean(evidence.truncated);
    strategyUsage = evidence.strategy_usage;
    improvements = { error: "", ...ideas };
    const banner = $("state-banner");
    banner.classList.toggle("frozen", data.state.frozen);
    banner.replaceChildren(node("strong", data.state.frozen ? "Learning is paused" : "Learning is on"),
      node("span", data.state.frozen
        ? "No new learned order will be published. Paused styles stay paused."
        : data.state.current ? `Using a published order whose signature was checked (${data.state.current.snapshot_id}). Paused styles stay paused.`
          : "No learned order is in use yet, so everyone gets the expert order."));
    $("updated").textContent = `Updated at ${new Date().toLocaleTimeString()}`;
    const signedIn = data.access.mode === "enabled";
    const viewOnly = !data.access.can_act;
    $("user").hidden = !(signedIn || viewOnly); $("sign-out").hidden = !signedIn;
    $("user").textContent = signedIn ? `${data.access.user}${data.access.can_own ? " (owner)" : viewOnly ? " (can look, cannot act)" : ""}` : "Look only: the safety controls are off";
    $("controls").disabled = viewOnly;
    $("action-scope").textContent = "This changes the live system, through its protected service. Each action is saved in a record that cannot be altered, before it runs.";
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
    $("notification").textContent = `Done. The safety action was applied. Reference: ${result.action_id}. It is on the Activity page.`;
    $("notification").hidden = false;
    await refresh();
  } catch (error) {
    if (error instanceof AccessError && error.status === 401 && gateFor(error)) { $("action-dialog").close(); return; }
    showError(error, "action-error");
  }
  finally { applying = false; $("apply-action").disabled = false; $("close-action").disabled = false; }
}
function openOwnerDialog(action, row, override = null) {
  if (!data?.access.can_own || loading || applying) return;
  $("detail-dialog").close();
  $("owner-form").reset(); $("owner-error").hidden = true;
  const options = [];
  if (action === "order") {
    for (const item of data.rankings.filter((item) => item.snapshot_order.includes(row.strategy_id))) {
      options.push({ value: item.request_key, label: situation(item.request_key).title, order: [...item.snapshot_order] });
    }
  } else if (action === "pause") {
    for (const stage of ["*", ...row.eligible_journey_states.filter((stage) => stage !== "*")]) {
      options.push({ value: stage, label: stageNames([stage]) });
    }
  } else if (action === "unpause") {
    for (const item of effectivePauses(row.strategy_id)) {
      options.push({ value: item.override_id || item.journey_state,
        label: `${item.source === "owner" ? "Owner pause" : "Automatic pause"}: ${stageNames([item.journey_state])}`,
        override: item.source === "owner" ? item : null, stage: item.journey_state });
    }
  } else {
    options.push({ value: override.override_id, label: `${humanize(override.kind)}: ${override.kind === "order"
      ? situation(override.request_key).title : stageNames([override.journey_state])}`, override });
  }
  ownerDraft = { action, row, override, options, revision: data.revision, order: [] };
  $("owner-title").textContent = action === "revoke" ? "Revoke owner override" : `${humanize(action)} override`;
  $("owner-style").textContent = row.name;
  $("owner-scope").replaceChildren(...options.map((item) => {
    const option = node("option", item.label); option.value = item.value; return option;
  }));
  $("owner-scope").value = options[0]?.value || "";
  $("owner-scope").disabled = action === "revoke";
  $("owner-order").hidden = action !== "order";
  $("owner-fields").disabled = false;
  updateOwnerScope();
  $("owner-dialog").showModal();
}
function updateOwnerScope() {
  const chosen = ownerDraft.options.find((item) => item.value === $("owner-scope").value);
  if (!chosen) {
    $("owner-impact").textContent = "No matching situation or pause remains. Refresh and review the current state.";
    $("save-owner").disabled = true; return;
  }
  const revoking = ownerDraft.action === "revoke" || Boolean(chosen.override);
  const kind = chosen.override?.kind;
  $("owner-impact").textContent = revoking
    ? kind === "order" ? "Revoking returns this situation to the current governed order, not an old captured order."
      : kind === "unpause" ? "Revoking restores the original automatic pause if its finding is still current."
        : "Revoking removes only this owner's pause. Any other pause remains in force."
    : ownerDraft.action === "order" ? "This replaces the learned starting order for this situation only. It stays pending until the next successful scheduled publication signs it. The expert comparison group stays unchanged."
      : ownerDraft.action === "unpause" ? "You approve lifting the selected existing finding. Its evidence is kept. A new finding can pause this style again."
        : "This immediately pauses the style for everyone in the selected stages. Revoking removes only this owner's pause.";
  const duplicate = !revoking && (data.state.owner_overrides || []).some((item) => item.kind === ownerDraft.action
    && (item.kind === "order" ? item.request_key === chosen.value
      : item.strategy_id === ownerDraft.row.strategy_id && item.journey_state === (chosen.stage || chosen.value)));
  if (duplicate) $("owner-impact").textContent += " An override already exists here. Revoke it from Details before replacing it.";
  const frozen = data.state.frozen && !revoking && ["order", "unpause"].includes(ownerDraft.action);
  if (frozen) $("owner-impact").textContent += " Learning is protectively frozen. Complete human recovery before widening availability.";
  $("save-owner").disabled = duplicate || frozen;
  $("save-owner").textContent = revoking ? "Revoke override" : "Save override";
  if (ownerDraft.action === "order") { ownerDraft.order = [...chosen.order]; renderOwnerOrder(); }
}
function renderOwnerOrder() {
  const list = node("ol", null, "owner-order-list");
  ownerDraft.order.forEach((id, index) => {
    const item = node("li");
    item.append(node("span", `${styleName(id)} (${id})`));
    for (const [label, change] of [["Move up", -1], ["Move down", 1]]) {
      const move = button(label, () => {
        const target = index + change;
        [ownerDraft.order[index], ownerDraft.order[target]] = [ownerDraft.order[target], ownerDraft.order[index]];
        renderOwnerOrder();
        const moves = [...$("owner-order").querySelectorAll("li")[target].querySelectorAll("button")];
        (moves.find((move) => !move.disabled) || moves[0]).focus();
      }, "quiet");
      move.disabled = index + change < 0 || index + change >= ownerDraft.order.length;
      move.setAttribute("aria-label", `${label}: ${styleName(id)}`); item.append(move);
    }
    list.append(item);
  });
  $("owner-order").replaceChildren(list);
}
async function applyOwnerAction(event) {
  event.preventDefault();
  if (applying || !ownerDraft || $("save-owner").disabled || !$("owner-confirm").checked) return;
  const chosen = ownerDraft.options.find((item) => item.value === $("owner-scope").value);
  if (!chosen) { showError(new Error("No matching scope remains. Cancel and refresh."), "owner-error"); return; }
  const revoking = ownerDraft.action === "revoke" || Boolean(chosen.override);
  const payload = { action: revoking ? "revoke" : ownerDraft.action, expected_revision: ownerDraft.revision,
    reason: $("owner-reason").value, confirmed: $("owner-confirm").checked };
  if (revoking) payload.override_id = chosen.override.override_id;
  else if (payload.action === "order") { payload.request_key = chosen.value; payload.order = [...ownerDraft.order]; }
  else { payload.strategy_id = ownerDraft.row.strategy_id; payload.journey_state = chosen.stage || chosen.value; }
  applying = true; $("save-owner").disabled = true; $("close-owner").disabled = true;
  $("owner-fields").disabled = true; $("owner-error").hidden = true;
  try {
    const result = await api("/api/owner-actions", payload);
    $("owner-dialog").close();
    $("notification").textContent = result.status === "pending_signature"
      ? `Order recorded, not active yet. It awaits the next successful signed publication. Reference: ${result.decision_ref}.`
      : `${revoking ? "Override revoked" : "Override applied"}. Reference: ${result.decision_ref}.`;
    $("notification").hidden = false;
    await refresh();
  } catch (error) {
    if (error instanceof AccessError && error.status === 401 && gateFor(error)) { $("owner-dialog").close(); return; }
    showError(error, "owner-error");
    if (error instanceof AccessError && error.status === 409) {
      $("owner-error").textContent += " Cancel, refresh, then review and confirm again.";
    }
  } finally {
    applying = false; $("close-owner").disabled = false; $("owner-fields").disabled = false; updateOwnerScope();
  }
}
document.querySelectorAll("nav button").forEach((item) => item.addEventListener("click", () => {
  section = item.dataset.section; page = 0; $("search").value = ""; render();
}));
$("search").addEventListener("input", () => { page = 0; render(); });
$("previous").addEventListener("click", () => { page -= 1; render(); });
$("next").addEventListener("click", () => { page += 1; render(); });
$("refresh").addEventListener("click", refresh);
$("controls").addEventListener("click", () => openControls());
$("help-open").addEventListener("click", () => { closeHelp(); renderHelp(); $("help-dialog").showModal(); });
$("close-help").addEventListener("click", () => $("help-dialog").close());
$("guide").open = store.get("gg-admin-guide") === "open";
$("guide").addEventListener("toggle", () => store.set("gg-admin-guide", $("guide").open ? "open" : "closed"));
$("close-detail").addEventListener("click", () => $("detail-dialog").close());
$("detail-dialog").addEventListener("close", () => {
  activeTest?.stop(); activeTest = null; $("detail-dialog").classList.remove("idea-dialog");
});
$("close-action").addEventListener("click", () => $("action-dialog").close());
$("action-dialog").addEventListener("cancel", (event) => { if (applying) event.preventDefault(); });
$("action-kind").addEventListener("change", actionImpact);
$("action-form").addEventListener("submit", applyAction);
$("owner-form").addEventListener("submit", applyOwnerAction);
$("owner-scope").addEventListener("change", updateOwnerScope);
$("close-owner").addEventListener("click", () => $("owner-dialog").close());
$("close-style").addEventListener("click", () => $("style-dialog").close());
$("owner-dialog").addEventListener("cancel", (event) => { if (applying) event.preventDefault(); });
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
