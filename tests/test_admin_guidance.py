"""The console explains itself in everyday words: each page has guidance, each term is defined, the wording stays plain."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from governedgraph.admin import api

STATIC = Path(api.__file__).with_name("static")
# Words that belong in the technical documents, not in what a new user reads. `also` fields are exempt on purpose:
# they are where each plain term names its technical equivalent.
JARGON = ("snapshot", "overlay", "veto", "aggregate", "tier ", "strategy", "strategies", "ranking", "suspension",
          "governance", "assurance", "cohort", "telemetry", "uplift", "audit", "evidence pack", "synthetic")
MAX_WORDS_PER_SENTENCE = 28


@pytest.fixture(scope="module")
def guide():
    if not shutil.which("node"):
        pytest.skip("Node is needed to read the guidance file")
    script = (STATIC / "guide.js").read_text()
    run = subprocess.run(["node", "-e", f"global.window = {{}}; {script}; console.log(JSON.stringify(window.GUIDE))"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def texts(node, path=""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from texts(item, f"{path}[{index}]")
    elif isinstance(node, dict):
        for key, item in node.items():
            if key not in {"also", "terms", "go"}:  # `terms` and `go` hold identifiers, not text a user reads
                yield from texts(item, f"{path}.{key}")


def menu_sections():
    page = (STATIC / "index.html").read_text()
    return re.findall(r'<button data-section="([a-z]+)"', page), page


def test_every_page_in_the_menu_has_guidance_and_defined_words(guide):
    sections, page = menu_sections()
    assert sections and set(sections) == set(guide["sections"])
    for name, item in guide["sections"].items():
        for key in ("title", "heading", "description", "what", "look", "can"):
            assert item[key].strip(), f"{name}.{key} is empty"
        assert item["terms"], f"{name} lists no words to explain"
        assert set(item["terms"]) <= set(guide["terms"]), f"{name} uses an undefined word"
        assert item["empty"].strip(), f"{name} has no message for an empty list"
        assert item["columns"], f"{name} has no columns"
        for label, help_text in item["columns"]:
            assert label.strip() and help_text.strip(), f"{name} has a column without help"
    for term in guide["terms"].values():
        assert term["name"].strip() and term["plain"].strip()
    for button in re.findall(r'<button data-section="[a-z]+"[^>]*>(.*?)</button>', page):
        assert "<small>" in button, f"menu item without a one-line hint: {button}"


def test_guidance_uses_everyday_words_and_short_sentences(guide):
    problems = []
    for path, text in texts(guide):
        lowered = text.lower()
        problems += [f"{path}: '{word.strip()}'" for word in JARGON if word in lowered]
        for sentence in re.split(r"(?<=[.?!])\s+", text):
            if len(sentence.split()) > MAX_WORDS_PER_SENTENCE:
                problems.append(f"{path}: a sentence of {len(sentence.split())} words")
    assert problems == []


def test_tasks_and_actions_point_at_things_that_exist(guide):
    for task in guide["tasks"]:
        assert task["go"] in set(guide["sections"]) | {"controls"}
        assert len(task["steps"]) >= 2
    assert set(guide["actions"]) == {"freeze", "revert_to_static", "suspend"}
    assert all(item["impact"].strip() for item in guide["actions"].values())
    assert len(guide["promises"]) >= 5


def test_the_page_loads_the_wording_before_the_script_and_keeps_the_required_label():
    page = (STATIC / "index.html").read_text()
    assert page.index("assets/guide.js") < page.index("assets/admin.js")
    assert page.count('<span class="watermark">Synthetic data</span>') == 1, "every view shares one synthetic-data label"


def test_detail_views_lead_with_plain_words_and_keep_raw_records_behind_a_disclosure():
    script = (STATIC / "admin.js").read_text()
    assert "jsonDetails" not in script
    assert 'node("summary", "Technical details (for support)")' in script
    assert script.count("showDetails(") >= 8


@pytest.fixture
def admin_browser():
    if not shutil.which("node"):
        pytest.skip("Node is needed to exercise the console presentation")
    setup = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.value = ""; this.style = {};
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(" "); }
  append(...children) { children.forEach((child) => { child.parentElement = this; }); this.children.push(...children); }
  replaceChildren(...children) { this.text = ""; this.children = children; }
  addEventListener(event, listener) { (this.listeners ||= {})[event] = listener; }
  setAttribute(name, value) { (this.attributes ||= {})[name] = value; }
  removeAttribute(name) { delete (this.attributes ||= {})[name]; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  reset() {}
  reportValidity() { return true; }
  focus() { this.focused = true; }
  querySelectorAll(tag) {
    return this.children.flatMap((child) => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]);
  }
}
const elements = new Map();
const document = {
  baseURI: "https://admin.example/",
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  },
  querySelector() { return { content: "test-session" }; },
  querySelectorAll() { return []; },
  createElement(tag) { return new Element(tag); },
  addEventListener() {},
};
const context = vm.createContext({
  window: {}, document, Node: Element, URL,
  setTimeout, clearTimeout, crypto: require("node:crypto").webcrypto,
  localStorage: { getItem() { return null; }, setItem() {} },
  fetch() { return new Promise(() => {}); },
});
vm.runInContext(fs.readFileSync(process.argv[1] + "/guide.js", "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[1] + "/testing.js", "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[1] + "/catalogue.js", "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[1] + "/improvements.js", "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[1] + "/admin.js", "utf8"), context);
const run = (script) => vm.runInContext(script, context);
const plain = (value) => JSON.parse(JSON.stringify(value));
run(`
data = {
  strategies: Array.from({length: 6}, (_, i) => ({
    strategy_id: "S" + (i + 1), name: "Style " + (i + 1), status: "active", version: "1.0",
    eligible_journey_states: i === 5 ? ["*"] : ["first_contact", "values_reviewed"], channels: ["chat", "voice"]
  })),
  access: {can_act: true, can_own: false}, thresholds: {min_sample_per_strategy: 10},
  always_available: ["S6"], learning: {aggregates: [
    {strategy_id: "S1", request_key: "first_contact|topic|chat|standard", interactions: 900, resolved: 800, repeat_contacts: 10}
  ]}, state: { frozen: false, suspensions: [
    {strategy_id: "S2", journey_state: "first_contact"}
  ]}, cases: [], decisions: [], actions: [],
  rankings: [
    ["first_contact|topic|chat|accessible_english", ["S1", "S3"], ["S1", "S3"], ["S2"]],
    ["first_contact|topic|chat|standard", ["S1", "S3"], ["S1", "S3"], ["S2"]],
    ["first_contact|topic|voice|standard", ["S1", "S3"], ["S3", "S1"], ["S2"]],
    ["values_reviewed|topic|voice|standard", ["S2", "S1"], ["S2", "S1"], []]
  ].map(([request_key, effective_control, effective_treatment, suspended]) => ({
    request_key, effective_control, effective_treatment, suspended, source: "signed snapshot",
    expert_priors: [...effective_control, ...suspended], snapshot_order: [...effective_treatment, ...suspended]
  }))
};
strategyUsage = {S1: 2, S2: 1, S6: 1};
`);
"""

    def execute(script):
        run = subprocess.run(["node", "-e", setup + script, str(STATIC)],
                             capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stderr

    return execute


def test_conversation_details_do_not_repeat_the_shared_synthetic_data_label(admin_browser):
    admin_browser(r"""
(async () => {
  run(`
    api = async () => ({watermark: "ILLUSTRATIVE - synthetic data", notice: "Example turns",
      window: "Recent activity", turns: [], truncated: false});
    window.traceArea = node("div"); window.traceTrigger = node("button");
  `);
  await run('loadTraces("J-synthetic", window.traceArea, window.traceTrigger)');
  assert.match(context.window.traceArea.textContent, /Example conversation/);
  assert.doesNotMatch(context.window.traceArea.textContent, /ILLUSTRATIVE|synthetic data/i);
  assert.equal(context.window.traceTrigger.disabled, false);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""")


def test_style_editor_submits_owner_publication_and_pending_cancellation_without_navigation(admin_browser):
    admin_browser(r"""
(async () => {
  run(`
    data.access.can_own = true; data.revision = "a".repeat(64);
    data.style_patterns = [{strategy_id: "S3", name: "Visual explanation", template_id: "T-S3", template_version: "1.0"}];
    data.style_stages = ["first_contact"]; data.style_channels = ["voice"];
    data.style_accessibility_modes = ["standard"];
    window.posts = [];
    api = async (path, payload) => {
      window.posts.push({path, payload});
      return {decision_ref: "GB-" + "b".repeat(32), status: payload.change.action === "cancel" ? "applied" : "pending_signature"};
    };
    refresh = async () => {};
    openStyleEditor("add");
  `);
  let form = document.getElementById("style-editor").children[0];
  form.querySelectorAll("input").find((input) => input.type !== "checkbox").value = "Illustrative visual explanation";
  form.querySelectorAll("textarea")[0].value = "ILLUSTRATIVE - owner approves this explanation.";
  assert.doesNotMatch(form.textContent, /I approve this human owner decision/);
  await form.listeners.submit({preventDefault() {}});
  const first = plain(context.window.posts[0]);
  assert.equal(first.path, "/api/catalogue");
  assert.deepEqual(first.payload.change, {action: "add", name: "Illustrative visual explanation",
    pattern_id: "S3", eligible_journey_states: ["first_contact"], channels: ["voice"], accessibility_modes: ["standard"]});
  assert.equal(first.payload.confirmed, true);
  assert.match(document.getElementById("notification").textContent, /Publication queued/);
  run('data.state.catalogue_pending = {decision_ref: "GB-pending"}; renderStyleManagement()');
  const actions = document.getElementById("style-management").querySelectorAll("button");
  assert.equal(actions[0].disabled, true);
  actions.find((button) => button.textContent === "Cancel publication").listeners.click();
  form = document.getElementById("style-editor").children[0];
  form.querySelectorAll("textarea")[0].value = "ILLUSTRATIVE - owner cancels the queued change.";
  assert.equal(form.querySelectorAll("input").length, 0);
  await form.listeners.submit({preventDefault() {}});
  assert.deepEqual(plain(context.window.posts[1]).payload.change, {action: "cancel"});
  assert.match(document.getElementById("notification").textContent, /Publication cancelled/);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""")


def test_edit_style_button_opens_prefilled_editor_and_publishes_without_approval_checkbox(admin_browser):
    admin_browser(r"""
(async () => {
  run(`
    data.access.can_own = true; data.revision = "a".repeat(64);
    data.style_patterns = [{strategy_id: "S3", name: "Visual explanation", template_id: "T-S3", template_version: "1.0"}];
    data.style_stages = ["first_contact", "values_reviewed"];
    data.style_channels = ["chat", "voice"]; data.style_accessibility_modes = ["standard"];
    Object.assign(data.strategies[0], {template_id: "T-S3", template_version: "1.0", accessibility_modes: ["standard"]});
    window.posts = [];
    api = async (path, payload) => {
      window.posts.push({path, payload});
      return {decision_ref: "GB-" + "b".repeat(32), status: "pending_signature"};
    };
    refresh = async () => {};
    render();
    strategyDetail(data.strategies[0]);
  `);
  const edit = document.getElementById("detail-body").querySelectorAll("button")
    .find((button) => button.textContent === "Edit style");
  assert.equal(edit.disabled, false);
  edit.listeners.click();
  assert.equal(document.getElementById("style-dialog").open, true);
  const form = document.getElementById("style-editor").children[0];
  const name = form.querySelectorAll("input").find((input) => input.type !== "checkbox");
  assert.equal(name.value, "Style 1");
  assert.equal(form.querySelectorAll("select")[0].value, "S3");
  assert.doesNotMatch(form.textContent, /I approve this human owner decision/);
  name.value = "Updated explanation";
  form.querySelectorAll("textarea")[0].value = "ILLUSTRATIVE - owner changes the style name.";
  await form.listeners.submit({preventDefault() {}});
  const posted = plain(context.window.posts[0]);
  assert.equal(posted.path, "/api/catalogue");
  assert.equal(posted.payload.change.action, "edit");
  assert.equal(posted.payload.change.strategy_id, "S1");
  assert.equal(posted.payload.change.name, "Updated explanation");
  assert.equal(posted.payload.confirmed, true);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""")


def test_retired_styles_are_not_repeated_in_the_active_style_list(admin_browser):
    admin_browser(r"""
run('data.strategies[0].status = "retired"; render()');
assert.equal(document.getElementById("result-count").textContent, "5 of 5 styles");
assert.doesNotMatch(document.getElementById("content").textContent, /Style 1/);
""")


def test_style_list_is_unique_and_distinguishes_recorded_uses_from_learning_counts(admin_browser):
    admin_browser(r"""
run("render()");
const content = document.getElementById("content");
const rows = content.children[0].children[0].children[1].children;
assert.equal(rows.length, 6);
assert.equal(new Set(rows.map((row) => row.children[0].textContent)).size, 6);
assert.equal(rows[0].children[4].textContent, "2");
assert.equal(rows[2].children[4].textContent, "0");
assert.match(rows[1].children[1].textContent, /Partly paused.*First contact/);
assert.match(rows[5].children[1].textContent, /Always available/);
assert.match(rows[5].children[3].textContent, /All stages/);
assert.equal(document.getElementById("result-count").textContent, "6 of 6 styles");
document.getElementById("search").value = "partly paused";
run("render()");
assert.equal(document.getElementById("result-count").textContent, "1 of 6 styles match your search");
run('data.state.suspensions.push({strategy_id: "S2", journey_state: "values_reviewed"})');
assert.equal(run("strategyState(data.strategies[1]).label"), "Paused");
document.getElementById("search").value = "S2";
run("journeysTruncated = true; render()");
const sampled = content.children[0].children[0].children[1].children;
assert.equal(sampled[0].children[4].textContent, "1 (sample)");
""")


def test_order_comparison_distinguishes_matching_publication_fallback_and_no_eligible_styles(admin_browser):
    admin_browser(r"""
assert.equal(run("rankingComparison(data.rankings[0]).label"), "Same as expert order");
assert.match(run("rankingComparison(data.rankings[0]).note"), /verified published order.*does not record why/);
run('data.rankings[0].source = "expert priors fallback"');
assert.equal(run("rankingComparison(data.rankings[0]).label"), "Expert order used");
assert.match(run("rankingComparison(data.rankings[0]).note"), /No published learned order covers this situation/);
run("data.rankings[0].effective_control = []; data.rankings[0].effective_treatment = []");
assert.match(run("rankingComparison(data.rankings[0]).note"), /human advisor is still available/);
const places = plain(run("data.rankings.slice(0, 3).map((row) => situation(row.request_key).title)"));
assert.equal(new Set(places).size, 3);
""")


def test_style_details_use_keyboard_accessible_tabs_and_embedded_testing(admin_browser):
    admin_browser(r"""
run("strategyDetail(data.strategies[0])");
const body = document.getElementById("detail-body");
assert.match(body.textContent, /Recorded uses.*2 in the saved answer records/);
assert.match(body.textContent, /Order by situation/);
assert.match(body.textContent, /Learning results.*Published uses.*900/s);
const tabs = body.children[0].children;
assert.deepEqual(tabs.map((tab) => tab.textContent), ["Summary", "Order by situation", "Learning results"]);
assert.equal(body.children[1].hidden, false);
assert.equal(body.children[2].hidden, true);
tabs[0].listeners.keydown({key: "ArrowRight", preventDefault() {}});
assert.equal(body.children[1].hidden, true);
assert.equal(body.children[2].hidden, false);
assert.equal(tabs[1].attributes["aria-selected"], "true");
assert.equal(tabs[1].focused, true);
tabs[1].listeners.keydown({key: "End", preventDefault() {}});
assert.equal(body.children[3].hidden, false);
run("testDetail(data.strategies[1])");
assert.equal(document.getElementById("detail-title").textContent, "Test: Style 2");
assert.doesNotMatch(body.textContent, /Test a selected style|Selected style:|ILLUSTRATIVE -/);
assert.match(body.textContent, /Chat.*Voice.*Persona/);
assert.match(body.textContent, /Start test/);
assert.equal(body.querySelectorAll("a").length, 0);
assert.equal(run("activeTest.style.strategy_id"), "S2");
""")


def test_owner_pause_button_distinguishes_existing_pauses(admin_browser):
    admin_browser(r"""
run("data.access.can_own = true");
const active = run("strategyActions(data.strategies[0])");
const paused = run("strategyActions(data.strategies[1])");
assert.equal(active.querySelectorAll("button")[0].textContent, "Pause");
assert.equal(paused.querySelectorAll("button")[0].textContent, "Pause override");
run("data.effective_pauses = []");
assert.equal(run("strategyActions(data.strategies[1])").querySelectorAll("button")[0].textContent, "Pause");
run('data.strategies[0].status = "paused"');
assert.equal(run("strategyActions(data.strategies[0])").querySelectorAll("button")[0].textContent, "Pause override");
""")


def test_embedded_tester_sends_selected_style_and_displays_actual_selection(admin_browser):
    admin_browser(r"""
run(`
class WebSocket {
  static OPEN = 1;
  constructor(url) { this.url = url; this.readyState = 1; this.sent = []; }
  send(message) { this.sent.push(JSON.parse(message)); }
  close() { this.readyState = 3; }
}
testDetail(data.strategies[1]);
activeTest.mode.value = "chat";
activeTest.request = async () => ({authorized: true});
`);
(async () => {
  await run("activeTest.start()");
  run("activeTest.socket.onopen()");
  assert.equal(run("activeTest.socket.url.pathname"), "/admin-test-stream");
  assert.deepEqual(plain(run("activeTest.socket.sent[0]")), {strategy_id: "S2", mode: "chat", contact_type: "first_contact"});
  run('activeTest.message(JSON.stringify({kind: "progress", strategy: "S6"}))');
  assert.match(run("activeTest.result.textContent"), /S6, not S2.*Pauses, eligibility/);
  run('activeTest.message(JSON.stringify({kind: "progress", strategy: "S2"}))');
  assert.match(run("activeTest.result.textContent"), /The selected style was used/);
  run("activeTest.end()");
  assert.equal(run("activeTest.socket"), null);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""")


def test_second_contact_style_test_filters_personas_and_sends_the_selected_contact(admin_browser):
    admin_browser(r"""
run(`
class WebSocket {
  static OPEN = 1;
  constructor(url) { this.url = url; this.readyState = 1; this.sent = []; }
  send(message) { this.sent.push(JSON.parse(message)); }
  close() { this.readyState = 3; }
}
data.strategies[1].eligible_journey_states.push("second_contact");
testDetail(data.strategies[1]);
activeTest.mode.value = "chat";
activeTest.simulations = [
  {simulation_id: "samira", display_name: "Samira", contact_type: "first_contact"},
  {simulation_id: "samira-return", display_name: "Samira", contact_type: "second_contact"},
];
activeTest.contact.value = "second_contact";
activeTest.contact.listeners.change();
activeTest.request = async () => ({authorized: true});
`);
(async () => {
  assert.deepEqual(plain(run("activeTest.persona.children.map(item => item.value)")), ["samira-return"]);
  assert.match(run("activeTest.status.textContent"), /Continued support/);
  await run("activeTest.start()");
  run("activeTest.socket.onopen()");
  assert.deepEqual(plain(run("activeTest.socket.sent[0]")),
    {strategy_id: "S2", mode: "chat", contact_type: "second_contact"});
  assert.equal(run("activeTest.contact.disabled"), true);
  run("activeTest.end()");
  assert.equal(run("activeTest.contact.disabled"), false);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""")


def test_chat_keeps_question_controls_when_conversation_grows_and_when_test_ends(admin_browser):
    admin_browser(r"""
run(`
  testDetail(data.strategies[0]);
  activeTest.mode.value = "chat";
  activeTest.message(JSON.stringify({kind: "chat_ready"}));
`);
const form = run("activeTest.form");
const transcript = run("activeTest.transcript");
assert.equal(form.hidden, false);
assert.equal(form.className, "test-composer");
assert.equal(form.children[1].id, "test-question");
assert.equal(form.children[2].textContent, "Send question");
run(`
  for (let index = 0; index < 100; index++) activeTest.caption("AI guide", "Illustrative test answer.");
  activeTest.message(JSON.stringify({kind: "thinking"}));
`);
assert.equal(transcript.children.length, 100);
assert.equal(form.hidden, false);
assert.equal(run("activeTest.send.disabled"), true);
run('activeTest.message(JSON.stringify({kind: "chat_ready"}))');
assert.equal(run("activeTest.send.disabled"), false);
run('activeTest.message(JSON.stringify({kind: "chat_complete"}))');
assert.equal(form.hidden, false);
assert.equal(run("activeTest.send.disabled"), true);
assert.equal(run("activeTest.input.disabled"), true);
assert.match(run("activeTest.status.textContent"), /Test finished/);
""")


def test_activity_combines_reviews_checks_and_action_steps_without_losing_requested_state(admin_browser):
    admin_browser(r"""
run(`
data.actions = [
  {action_id: "A1", action: "freeze", status: "requested", created_at: "2026-11-12T12:00:00Z"},
  {action_id: "A1", action: "freeze", status: "applied", created_at: "2026-11-12T12:00:00Z", completed_at: "2026-11-12T12:01:00Z"},
  {action_id: "A2", action: "freeze", status: "requested", created_at: "2026-11-12T13:00:00Z"}
];
data.cases = [{case_id: "C1", tier: "Tier 2 to reinstate", finding: {strategy_id: "S2"}}];
data.decisions = [{reference: "D1", kind: "tier1_review", outcome: "approved", tier: "tier1"}];
section = "activity"; render();
`);
const items = plain(run("activityRows()"));
assert.equal(items.length, 4);
assert.equal(items[0].reference, "A2");
assert.equal(items[0].status, "Requested, not finished");
assert.equal(items.find((row) => row.reference === "A1").records.length, 2);
assert.equal(items.find((row) => row.reference === "A1").status, "Done");
assert.deepEqual([...new Set(items.map((row) => row.kind))].sort(), ["action", "case", "decision"]);
""")


def test_owner_can_reorder_and_inspect_pending_overrides_without_changing_the_expert_comparison(admin_browser):
    admin_browser(r"""
run('data.access.can_own = true; data.revision = "a".repeat(64); openOwnerDialog("order", data.strategies[0])');
const list = document.getElementById("owner-order");
assert.equal(document.getElementById("owner-dialog").open, true);
assert.match(document.getElementById("owner-impact").textContent, /pending.*expert comparison group stays unchanged/);
const original = plain(run("ownerDraft.order"));
const moves = list.querySelectorAll("button");
moves[1].listeners.click();
assert.deepEqual(plain(run("ownerDraft.order")), [original[1], original[0], ...original.slice(2)]);
assert.equal(document.getElementById("save-owner").disabled, false);
assert.deepEqual(plain(run("data.rankings[0].snapshot_order")), original);
run(`
data.state.owner_overrides = [{
  override_id: "GB-" + "b".repeat(32), kind: "order", order: [...ownerDraft.order],
  request_key: data.rankings[0].request_key, snapshot: null
}];
updateOwnerScope(); strategyDetail(data.strategies[0]);
`);
assert.equal(document.getElementById("save-owner").disabled, true);
assert.match(document.getElementById("detail-body").textContent, /Owner overrides.*Pending signature.*Revoke override/);
""")


def test_owner_unpause_revokes_manual_pause_without_ignoring_other_findings(admin_browser):
    admin_browser(r"""
run(`
data.access.can_own = true; data.revision = "a".repeat(64);
const pause = {override_id: "GB-" + "b".repeat(32), kind: "pause", strategy_id: "S2",
  journey_state: "*", order: [], source: "owner"};
data.state.owner_overrides = [pause];
data.effective_pauses = [pause, {...data.state.suspensions[0], source: "fairness"}];
openOwnerDialog("unpause", data.strategies[1]);
`);
assert.match(document.getElementById("owner-impact").textContent, /only this owner's pause.*other pause remains/);
assert.equal(document.getElementById("save-owner").textContent, "Revoke override");
run('data.state.frozen = true; document.getElementById("owner-scope").value = "first_contact"; updateOwnerScope()');
assert.equal(document.getElementById("save-owner").disabled, true);
assert.match(document.getElementById("owner-impact").textContent, /protectively frozen/);
assert.equal(run("strategyState(data.strategies[1]).label"), "Paused");
""")


def test_owner_activity_distinguishes_pending_signed_and_revoked_orders(admin_browser):
    admin_browser(r"""
run(`
data.actions = [{action: "owner_order", action_id: "GB-" + "b".repeat(32), status: "pending_signature"}];
data.state.owner_overrides = [{kind: "order", override_id: data.actions[0].action_id, snapshot: null}];
`);
assert.equal(run("activityRows()[0].status"), "Pending signature");
run("data.state.owner_overrides[0].snapshot = {snapshot_id: 'signed-owner-order'}");
assert.equal(run("activityRows()[0].status"), "Override active");
run("data.state.owner_overrides = []");
assert.equal(run("activityRows()[0].status"), "No longer in force");
assert.equal(run("data.actions[0].status"), "pending_signature");
""")


IDEA = r"""
run(`
window.idea = (kind, draft, status = "open") => ({
  proposal_id: "GP-" + "a".repeat(24), evidence_digest: "b".repeat(64), content_digest: "c".repeat(64), batch_id: "GRB-20261122T060000Z", kind,
  rationale: "Six declined answers from five journeys.", draft, status: {code: status, label: "Open: waiting for an owner"},
  allowed_decisions: kind === "false_negative_review" ? ["acknowledge", "defer", "reject"] : ["approve", "defer", "reject"],
  situation: {topic: "payment_continuity", journey_state: "first_contact", channel: "chat"}, created_at: "2026-11-22T06:00:00+00:00",
  evidence: {declined: 6, journeys: 5, unanswered: 6, review_flagged: 0, escalated: 0, content_retrieved_share: 0,
    eligible_styles: ["S1"], review_issues: {}, content_versions: {}, sample_journey_ids: ["J-1"],
    window_start: "a", window_end: "b", cases: []},
});
`);
"""


def test_an_owner_approves_a_proposed_style_from_its_details_and_a_reader_sees_no_form(admin_browser):
    admin_browser(IDEA + r"""
(async () => {
  run(`
    data.access.can_own = true; data.revision = "a".repeat(64);
    data.style_patterns = [{strategy_id: "S3", name: "Visual explanation"}];
    data.style_stages = ["first_contact", "values_reviewed"]; data.style_channels = ["chat", "voice"];
    data.style_accessibility_modes = ["standard"];
    window.posts = []; window.saved = [];
  `);
  const row = run(`window.idea("strategy_gap", {action: "add", name: "Proposed style", pattern_id: "S3",
    eligible_journey_states: ["first_contact"], channels: ["chat"], accessibility_modes: ["standard"]})`);
  const target = document.createElement("div");
  context.window.AdminImprovements.mount(target, {row, extra: {}, data: run("data"),
    api: async (path, payload) => { context.window.posts.push({path, payload}); return {decision_ref: "GB-1", status: "pending_signature"}; },
    saved: async (result, decision) => { context.window.saved.push(decision); }});
  const form = target.querySelectorAll("form")[0];
  const buttons = form.querySelectorAll("button");
  assert.deepEqual(buttons.map((button) => button.textContent), ["Approve style", "Defer", "Reject"]);
  assert.equal(form.querySelectorAll("input").filter((input) => input.type === "checkbox" && input.required).length, 0,
    "clicking Approve is the approval; there is no separate confirmation box");
  await buttons[0].listeners.click();
  const posted = plain(context.window.posts[0]);
  assert.equal(posted.path, "/api/proposals/decision");
  assert.equal(posted.payload.decision, "approve");
  assert.equal(posted.payload.confirmed, true);
  assert.equal(posted.payload.reason, "Approved by owner.");
  assert.equal(posted.payload.expected_revision, "a".repeat(64));
  assert.deepEqual(posted.payload.style, {name: "Proposed style", pattern_id: "S3",
    eligible_journey_states: ["first_contact"], channels: ["chat"], accessibility_modes: ["standard"]});
  assert.deepEqual(plain(context.window.saved), ["approve"]);
  run("data.access.can_own = false");
  const readOnly = document.createElement("div");
  context.window.AdminImprovements.mount(readOnly, {row, extra: {}, data: run("data"), api() {}, saved() {}});
  assert.equal(readOnly.querySelectorAll("form").length, 0);
  assert.match(readOnly.textContent, /Only an owner can decide/);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""")


def test_a_content_proposal_asks_for_a_passage_only_when_approving_and_rejection_needs_none(admin_browser):
    admin_browser(IDEA + r"""
(async () => {
  run(`data.access.can_own = true; window.posts = [];`);
  const row = run(`window.idea("content_gap", {action: "add", title: "Payment continuity", language: "en",
    journey_states: ["first_contact"], answers: "Questions about payments", related_content_ids: []})`);
  const target = document.createElement("div");
  context.window.AdminImprovements.mount(target, {row, data: run("data"), extra: {stages: ["*", "first_contact"], related: []},
    api: async (path, payload) => { context.window.posts.push(payload); return {decision_ref: "GB-1", status: "applied"}; },
    saved: async () => {}});
  const form = target.querySelectorAll("form")[0];
  const [approve, defer, reject] = form.querySelectorAll("button");
  await reject.listeners.click();
  assert.equal(context.window.posts.length, 0, "rejecting needs a short reason");
  assert.match(form.textContent, /Add a short reason/);
  form.querySelectorAll("input").find((input) => (input.placeholder || "").startsWith("Reason")).value = "ILLUSTRATIVE - owner rejects this idea.";
  await reject.listeners.click();
  assert.equal(context.window.posts[0].decision, "reject");
  assert.equal(context.window.posts[0].content, undefined);
  await approve.listeners.click();
  assert.equal(context.window.posts.length, 1, "approving without a passage must not post");
  assert.match(form.textContent, /Give the passage a title and text/);
  form.querySelectorAll("textarea")[0].value = "A passage";
  form.querySelectorAll("input").find((input) => input.maxLength === 200).value = "A title";
  await approve.listeners.click();
  assert.equal(context.window.posts.length, 1, "approving a passage needs a recorded source");
  assert.match(form.textContent, /Record the source/);
  form.querySelectorAll("input").find((input) => input.maxLength === 300).value = "Fund regulations, article 12";
  await approve.listeners.click();
  assert.equal(context.window.posts[1].source, "Fund regulations, article 12");
  assert.equal(context.window.posts[1].confirmed, true);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""")


def test_an_ai_suggestion_is_labelled_unchecked_and_prefills_the_owners_editor(admin_browser):
    admin_browser(IDEA + r"""
(async () => {
  run(`data.access.can_own = true;`);
  const row = run(`window.idea("content_gap", {action: "add", title: "Payment", language: "en", journey_states: ["first_contact"],
    answers: "q", related_content_ids: [], suggested: {title: "Drafted title", answers: "Does it change?", body: "Drafted passage.",
    sources: ["C-001-v1.3"], claims: ["Payments continue after the transition."], gaps: ["The payment date rule."],
    themes: ["Does the payment change"], coverage: ["First changed payment"], agent: "gg-gap-review:3"}})`);
  row.evidence.cases = [{journey_id: "J-1", created_at: "2026-11-22T06:00:00+00:00", outcome: "unanswered",
    decline_reason: "output_gate", review_issues: [], passages: [], question: "Will my payment change?"},
    {journey_id: "J-2", created_at: "2026-11-22T05:00:00+00:00", outcome: "review_flagged", decline_reason: "",
    review_issues: ["grounding"], passages: ["C-001-v1.3"], question: ""}];
  context.window.opened = [];
  const target = document.createElement("div");
  context.window.AdminImprovements.mount(target, {row, data: run("data"), extra: {stages: ["*", "first_contact"], related: []},
    api: async () => ({}), saved: async () => {}, openJourney: (id) => context.window.opened.push(id)});
  assert.match(target.textContent, /Will my payment change\?/);
  assert.match(target.textContent, /Not answered/);
  assert.match(target.textContent, /A safety check blocked the draft answer/);
  assert.match(target.textContent, /No approved passage was found/);
  assert.match(target.textContent, /Answered, but flagged/);
  assert.match(target.textContent, /not based on the approved passage/);
  assert.match(target.textContent, /Approved passages found: C-001-v1\.3/);
  assert.match(target.textContent, /Proposed by AI/);
  assert.match(target.textContent, /What was available/);
  target.querySelectorAll("button").find((button) => button.textContent === "J-2").listeners.click();
  assert.deepEqual(plain(context.window.opened), ["J-2"]);
  assert.match(target.textContent, /Drafted by AI\. Nobody has checked it yet/);
  assert.match(target.textContent, /The payment date rule/);
  assert.match(target.textContent, /Payments continue after the transition/);
  assert.match(target.textContent, /Source you checked against/);
  assert.match(target.textContent, /Members ask: Does the payment change/);
  assert.match(target.textContent, /Not yet covered: First changed payment/);
  const form = target.querySelectorAll("form")[0];
  assert.equal(form.querySelectorAll("input").find((input) => input.maxLength === 200).value, "Drafted title");
  assert.equal(form.querySelectorAll("textarea")[0].value, "Drafted passage.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
""")
