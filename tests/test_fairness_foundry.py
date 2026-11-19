"""Protective-only fairness tools never delegate scope or governance to a model."""
from dataclasses import replace
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest

from governedgraph.agents import fairness_runtime as runtime
from governedgraph.agents.fairness_explainability import FairnessExplainabilityAgent, run_cohort_audit
from governedgraph.common.contracts import ContractError
from governedgraph.learning.ranking_pipeline import load_thresholds
from strategy_doubles import COHORTS, local_strategy_service, pinned_to_active


def test_b7_b8_fairness_invocation_role_has_exact_approved_rights_and_scopes():
    root = Path(__file__).resolve().parents[1]
    source = (root / "infra/modules/fairness-foundry-rbac.bicep").read_text()
    permissions = source.split("permissions: [", 1)[1].split("notDataActions:", 1)[0]
    actions = [line.strip().strip("'") for line in permissions.splitlines()
               if line.strip().startswith("'")]
    assert actions == ["Microsoft.CognitiveServices/accounts/AIServices/agents/read"]
    assert "responses/" not in permissions
    assert "actions: []" in permissions
    for name, scope in (("fairnessInvocation", "project"), ("fairnessRecordsRead", "records")):
        assignment = source.split(f"resource {name} ", 1)[1].split("\n}", 1)[0]
        assert f"scope: {scope}" in assignment
        assert "principalId: fairnessPrincipalId" in assignment
    assert "name: 'explainability-records'" in source
    assert "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1" in source
    assert "module fairnessFoundry 'fairness-foundry-rbac.bicep'" in (
        root / "infra/modules/rbac.bicep"
    ).read_text()


def test_b7_operator_cli_outputs_receipts_not_member_report(monkeypatch, capsys):
    bound, _, _, reads = tools("report", member_ref="MBR-7Q2K")

    def factory(request):
        assert request == bound.request
        return runtime.FoundryFairnessAgent(client=Responses([])), bound

    monkeypatch.setattr(runtime, "azure_runtime", factory)
    runtime.main(["--action", "report", "--member-ref", "MBR-7Q2K"])
    output = capsys.readouterr().out
    assert json.loads(output)["report_prepared"] is True
    assert reads == ["MBR-7Q2K"]
    assert "MBR-" not in output and "private-record" not in output


def test_b7_b8_consumer_assignment_is_only_on_the_protective_agent():
    source = (Path(__file__).resolve().parents[1] / "infra/modules/fairness-agent-consumer.bicep").read_text()
    assert "name: 'gg-fairness-explainability'" in source
    assert "scope: agent" in source
    assert "principalId: fairnessPrincipalId" in source
    assert "eed3b665-ab3a-47b6-8f48-c9382fb1dad6" in source
    assert "roleDefinitions@" not in source
    assert "scope: project\n" not in source


@pytest.mark.parametrize("approved", [True, False])
def test_b8_endpoint_transport_verifies_selector_and_retains_definition_check(monkeypatch, approved):
    expected = {"version_selection_rules": [{"agent_version": "3", "traffic_percentage": 100, "type": "FixedRatio"}]}
    definition = SimpleNamespace(as_dict=lambda: {"tools": "protective-only"})
    version = SimpleNamespace(name=runtime.settings.fairness_agent_name, version="3", definition=definition)
    selector = expected if approved else {"version_selection_rules": [{"agent_version": "@latest"}]}
    calls = []

    def openai_client(**kwargs):
        calls.append(kwargs)
        return Responses([])

    project = SimpleNamespace(agents=SimpleNamespace(
        get_version=lambda *_: version,
        get=lambda _: SimpleNamespace(as_dict=lambda: {"agent_endpoint": {"version_selector": selector}}),
    ), get_openai_client=openai_client)
    module = ModuleType("azure.ai.projects")
    module.AIProjectClient = lambda **kwargs: project
    monkeypatch.setitem(sys.modules, "azure.ai.projects", module)
    monkeypatch.setattr(runtime, "agent_definition", lambda: definition)
    monkeypatch.setattr(runtime, "endpoint_selector", lambda _: SimpleNamespace(as_dict=lambda: expected))
    monkeypatch.setattr(runtime, "settings", replace(runtime.settings, fairness_agent_version="3"))
    if approved:
        runtime.FoundryFairnessAgent(credential=object())
        assert calls == [{"agent_name": runtime.settings.fairness_agent_name, "max_retries": 0, "timeout": 20}]
    else:
        with pytest.raises(runtime.FoundryUnavailable, match="exclusively"):
            runtime.FoundryFairnessAgent(credential=object())
        assert not calls


class Responses:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(status="completed", id="response", output=self.outputs.pop(0),
                               output_text="Untrusted model text must never become a decision.")


def tools(action="audit", **scope):
    cases, audits, reads = [], [], []

    def audit():
        audits.append(True)
        return "audit-1234abcd"

    def records(member):
        reads.append(member)
        return [{"member_ref": member, "record_id": "private-record"},
                {"member_ref": "MBR-OTHER", "record_id": "not-this-member"}]

    bound = runtime.FairnessTools(runtime.FairnessRequest(action, **scope),
                                  audit_runner=audit, case_writer=cases.append, records_reader=records)
    return bound, cases, audits, reads


@pytest.mark.parametrize("payload", [
    {"action": "audit", "age_band": "older"}, {"action": "audit", "member_ref": "MBR-7Q2K"},
    {"action": "audit", "strategy_id": "S2"}, {"action": "reinstate", "strategy_id": "../S2"},
    {"action": "report", "member_ref": ""}, {"action": "promote"}, {"action": []},
    {"action": "audit", "strategy_id": 0},
])
def test_b7_closed_fairness_request_rejects_scope_and_demographics(payload):
    with pytest.raises(ContractError):
        runtime.FairnessRequest.from_dict(payload)


def test_b6_existing_audit_applies_all_vetoes_without_model_permission(tmp_path):
    service = local_strategy_service(tmp_path / "state")
    protective = FairnessExplainabilityAgent(service, tmp_path / "governance")
    report = run_cohort_audit(load_thresholds(), pinned_to_active(service, COHORTS, tmp_path / "cohorts.csv"))

    def audit():
        protective.apply_veto_gate(report)
        return report.audit_id

    bound = runtime.FairnessTools(runtime.FairnessRequest("audit"), audit_runner=audit,
                                  case_writer=lambda _: None, records_reader=lambda _: [])
    client = Responses([])
    result = runtime.FoundryFairnessAgent(client=client).run(bound)
    assert result["protective_vetoes_enforced"] and service.suspended_for("first_contact") == ["S2"]
    assert "Untrusted model" not in json.dumps(result)


def test_b8_reinstatement_is_refused_and_durably_routed_before_model_reply():
    bound, cases, audits, reads = tools("reinstate", strategy_id="S2")
    result = runtime.FoundryFairnessAgent(client=Responses([])).run(bound)
    assert result["refused"] and result["tier"] == "Tier 2"
    assert len(cases) == 1 and cases[0]["case_id"] == result["case_id"]
    assert cases[0]["strategy_id"] == "S2" and cases[0]["status"] == "refused_pending_human_decision"
    assert not audits and not reads
    assert bound.open_governance_case() == {k: result[k] for k in ("case_id", "tier", "refused", "watermark")}
    assert len(cases) == 1


def test_b8_governance_write_failure_never_claims_routing():
    bound, _, _, _ = tools("reinstate", strategy_id="S2")

    def unavailable(_):
        raise OSError("case store unavailable")

    bound.case_writer = unavailable
    with pytest.raises(OSError):
        runtime.FoundryFairnessAgent(client=Responses([])).run(bound)
    assert bound.case_id is None


@pytest.mark.parametrize("name,arguments", [
    ("reinstate", "{}"), ("promote", "{}"), ("edit_content", "{}"), ("release_freeze", "{}"),
    ("suspend_strategy", '{"strategy_id":"S5"}'),
    ("run_cohort_audit", '{"cohort":"older"}'),
    ("assemble_explainability_report", '{"member_ref":"MBR-OTHER"}'),
    ("run_cohort_audit", "[]"),
])
def test_b7_b8_model_cannot_inject_scope_or_widen_tools(name, arguments):
    bound, _, _, _ = tools()
    with pytest.raises(PermissionError):
        bound.dispatch(name, arguments)


def test_b4_b7_member_report_contents_and_scope_never_enter_foundry():
    bound, _, _, reads = tools("report", member_ref="MBR-7Q2K")
    call = SimpleNamespace(type="function_call", name="assemble_explainability_report",
                           arguments="{}", call_id="call-report")
    client = Responses([call], [])
    result = runtime.FoundryFairnessAgent(client=client).run(bound)
    assert result["report_prepared"] and reads == ["MBR-7Q2K"]
    assert bound.member_report["interactions"] == 1
    assert bound.member_report["records"][0]["record_id"] == "private-record"
    serialized = json.dumps(client.calls)
    assert "MBR-" not in serialized and "private-record" not in serialized and "not-this-member" not in serialized
    assert "previous_response_id" in client.calls[1]


def test_b8_wrong_bound_operation_is_refused():
    bound, _, _, _ = tools("reinstate", strategy_id="S2")
    with pytest.raises(PermissionError):
        bound.dispatch("suspend_strategy", "{}")
    with pytest.raises(PermissionError):
        bound.dispatch("assemble_explainability_report", "{}")


def test_b6_audit_failure_never_claims_success():
    bound, _, _, _ = tools()

    def failed():
        raise RuntimeError("audit failed; existing adapter freezes")

    bound.audit_runner = failed
    with pytest.raises(RuntimeError):
        runtime.FoundryFairnessAgent(client=Responses([])).run(bound)
    assert bound.audit_id is None


def test_b8_definition_has_exactly_four_closed_protective_tools(monkeypatch):
    module = ModuleType("azure.ai.projects.models")
    module.FunctionTool = lambda **kwargs: kwargs
    module.PromptAgentDefinition = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, "azure.ai.projects.models", module)
    definition = runtime.agent_definition()
    assert definition.model == runtime.settings.compact_deployment
    assert {tool["name"] for tool in definition.tools} == set(runtime.TOOL_DESCRIPTIONS)
    assert all(tool["strict"] and tool["parameters"]["additionalProperties"] is False
               and tool["parameters"]["properties"] == {} for tool in definition.tools)


def test_b7_azure_runtime_refuses_non_fairness_host_before_using_credentials(monkeypatch):
    identity, blob = ModuleType("azure.identity"), ModuleType("azure.storage.blob")
    identity.ManagedIdentityCredential = lambda **_: pytest.fail("wrong host must not acquire a credential")
    blob.BlobServiceClient = object
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.storage.blob", blob)
    monkeypatch.setattr(runtime, "settings", replace(runtime.settings))
    monkeypatch.setenv("GOVERNEDGRAPH_P2_ROLE", "pipeline")
    with pytest.raises(PermissionError):
        runtime.azure_runtime(runtime.FairnessRequest("audit"))


def test_b8_model_tool_loop_is_bounded_and_preflight_still_routes_case():
    bound, cases, _, _ = tools("reinstate", strategy_id="S2")
    call = SimpleNamespace(type="function_call", name="open_governance_case", arguments="{}", call_id="case")
    client = Responses(*[[call] for _ in range(runtime.MAX_TOOL_ROUNDS + 1)])
    with pytest.raises(runtime.FoundryUnavailable, match="limit"):
        runtime.FoundryFairnessAgent(client=client).run(bound)
    assert len(cases) == 1


def test_b6_model_failure_does_not_undo_protective_preflight():
    bound, _, audits, _ = tools()
    client = Responses([])
    original = client.create

    def incomplete(**kwargs):
        result = original(**kwargs)
        result.status = "failed"
        return result

    client.create = incomplete
    with pytest.raises(runtime.FoundryUnavailable, match="did not complete"):
        runtime.FoundryFairnessAgent(client=client).run(bound)
    assert audits == [True]


@pytest.mark.parametrize("changed", ["name", "version", "definition"])
def test_b8_pinned_fairness_definition_is_verified_before_inference(monkeypatch, changed):
    definition = SimpleNamespace(as_dict=lambda: {"tools": "protective-only"})
    version = SimpleNamespace(name=runtime.settings.fairness_agent_name, version="3", definition=definition)
    setattr(version, changed, SimpleNamespace(as_dict=lambda: {"tools": "widened"})
            if changed == "definition" else "unapproved")
    project = SimpleNamespace(agents=SimpleNamespace(get_version=lambda *_: version),
                              get_openai_client=lambda **_: pytest.fail("invalid pin must not invoke a model"))
    module = ModuleType("azure.ai.projects")
    module.AIProjectClient = lambda **_: project
    monkeypatch.setitem(sys.modules, "azure.ai.projects", module)
    monkeypatch.setattr(runtime, "agent_definition", lambda: definition)
    monkeypatch.setattr(runtime, "settings", replace(runtime.settings, fairness_agent_version="3"))
    with pytest.raises(runtime.FoundryUnavailable, match="definition"):
        runtime.FoundryFairnessAgent(credential=object())


def test_b8_fairness_provisioning_is_plan_only_by_default_and_reuses_exact_version(monkeypatch):
    import importlib.util
    from governedgraph.common.config import REPO_ROOT

    spec = importlib.util.spec_from_file_location("gg_fairness_create_agents", REPO_ROOT / "scripts/create_agents.py")
    assert spec is not None and spec.loader is not None
    provisioner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provisioner)
    definition = SimpleNamespace(as_dict=lambda: {"kind": "prompt", "tools": "protective-only"})
    monkeypatch.setattr(runtime, "agent_definition", lambda: definition)
    pinned = []
    monkeypatch.setattr(provisioner, "pin_fairness_endpoint",
                        lambda project, version, *, agent_name: pinned.append((agent_name, version)))
    errors = ModuleType("azure.core.exceptions")
    errors.ResourceNotFoundError = type("NotFound", (Exception,), {})
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", errors)
    created = []

    def create_version(**kwargs):
        assert kwargs["agent_name"] == runtime.settings.fairness_agent_name
        assert kwargs["metadata"]["decision"] == "IMP-34"
        version = SimpleNamespace(version="1", definition=definition, metadata=kwargs["metadata"])
        created.append(version)
        return version

    project = SimpleNamespace(agents=SimpleNamespace(list_versions=lambda _: created, create_version=create_version))
    plan = provisioner.provision(None, apply=False, role="fairness")
    assert "plan only" in plan["action"] and not created
    assert provisioner.provision(project, apply=True, role="fairness")["agent_version"] == "1"
    assert provisioner.provision(project, apply=True, role="fairness")["agent_version"] == "1"
    assert len(created) == 1
    assert pinned == [(runtime.settings.fairness_agent_name, "1")] * 2


@pytest.mark.parametrize("acknowledged", [True, False])
@pytest.mark.parametrize("agent_name", [None, "gg-explicit-synthetic-fairness"])
def test_b8_fairness_endpoint_is_pinned_not_automatically_latest(monkeypatch, acknowledged, agent_name):
    import importlib.util
    from governedgraph.common.config import REPO_ROOT

    spec = importlib.util.spec_from_file_location("gg_fairness_endpoint", REPO_ROOT / "scripts/create_agents.py")
    assert spec is not None and spec.loader is not None
    provisioner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provisioner)
    module = ModuleType("azure.ai.projects.models")

    class Model:
        def __init__(self, **kwargs):
            self.values = kwargs

        def as_dict(self):
            def encoded(value):
                if isinstance(value, Model):
                    return value.as_dict()
                if isinstance(value, list):
                    return [encoded(item) for item in value]
                return value
            return {key: encoded(value) for key, value in self.values.items()}

    module.AgentEndpointConfig = module.FixedRatioVersionSelectionRule = module.VersionSelector = Model
    monkeypatch.setitem(sys.modules, "azure.ai.projects.models", module)
    original = {"version_selection_rules": [{"agent_version": "@latest"}]}
    updates, reads = [], []
    expected_name = runtime.settings.fairness_agent_name if agent_name is None else agent_name

    def update_details(**kwargs):
        assert kwargs["agent_name"] == expected_name
        updates.append(kwargs)
        endpoint = kwargs["agent_endpoint"].as_dict()
        if not acknowledged:
            endpoint = {"version_selector": original}
        return SimpleNamespace(as_dict=lambda: {"agent_endpoint": endpoint})

    def get(name):
        reads.append(name)
        return SimpleNamespace(as_dict=lambda: {"agent_endpoint": {"version_selector": original}})
    project = SimpleNamespace(agents=SimpleNamespace(
        get=get,
        update_details=update_details,
    ))
    if acknowledged:
        provisioner.pin_fairness_endpoint(project, "1", agent_name=agent_name)
    else:
        with pytest.raises(RuntimeError, match="acknowledge"):
            provisioner.pin_fairness_endpoint(project, "1", agent_name=agent_name)
    assert reads == [expected_name]
    assert len(updates) == 1
    assert updates[0]["agent_endpoint"].as_dict()["version_selector"]["version_selection_rules"][0]["agent_version"] == "1"
