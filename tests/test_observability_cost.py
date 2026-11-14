"""ILLUSTRATIVE - synthetic data; content-free traces and mature operational costs."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from governedgraph.agents.foundry_tracing import journey_trace_id
from governedgraph.common.operational_logging import OperationalFormatter
from governedgraph.common.contracts import ContractError
from governedgraph.cost_reporting import Contact, Cost, report
from governedgraph.observability import contact_key, emit, headers, model_usage, operation

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 1, tzinfo=timezone.utc)
PROJECT_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-synthetic"
    "/providers/Microsoft.CognitiveServices/accounts/ais-synthetic/projects/project-synthetic"
)


def test_b3_gateway_omits_empty_named_values_and_requires_every_agent_pin():
    source = (ROOT / "infra/modules/model-gateway.bicep").read_text()
    named_values = source.split("resource values ", 1)[1].split("resource api ", 1)[0]
    assert "for item in items(configuration): if (!empty(item.value))" in named_values
    assert "value: item.value" in named_values
    assert "@minLength(1)\nparam runtimeClientId string" in source
    readiness = source.split("var agentsReady = ", 1)[1].split("resource values ", 1)[0]
    assert re.fullmatch(r"!empty\(\w+\)(?:\s*&&\s*!empty\(\w+\))*\s*", readiness)
    assert set(re.findall(r"!empty\((\w+)\)", readiness)) == {
        "mainAgentVersion", "compactAgentVersion", "intentAgentVersion",
        "understandingAgentVersion", "reviewAgentVersion",
    }


def test_b3_b8_gateway_uses_deny_policy_until_all_pins_exist_including_on_redeploy():
    source = (ROOT / "infra/modules/model-gateway.bicep").read_text()
    policy = source.split("resource policy ", 1)[1].split("var tracedApis", 1)[0]
    assert "properties: agentsReady ? pinnedAgentPolicy : unavailableAgentPolicy" in policy
    assert "dependsOn: [ values ]" in policy
    operation = source.split("resource operation ", 1)[1].split("var pinnedAgentPolicy", 1)[0]
    assert "dependsOn: [ policy ]" in operation
    assert ("var pinnedAgentPolicy = {\n  format: 'xml', value: replace(loadTextContent('../policies/model-responses.xml'), "
            "'__CONVERSATION_TOKENS_PER_MINUTE__', string(conversationTokensPerMinute))") in source
    assert "var unavailableAgentPolicy = {\n  format: 'xml', value: loadTextContent('../policies/model-unavailable.xml')" in source
    assert " = if (" not in policy  # an incomplete redeploy must replace a previously active policy


def test_b3_gateway_bootstrap_explicitly_fails_and_never_forwards_to_a_model():
    path = ROOT / "infra/policies/model-unavailable.xml"
    text = path.read_text()
    root = ET.fromstring(text)
    inbound = root.find("inbound")
    assert [child.tag for child in inbound] == ["base", "return-response"]
    response = inbound.find("return-response")
    assert response.find("set-status").attrib["code"] == "503"
    assert response.find("set-header").attrib == {"name": "Content-Type", "exists-action": "override"}
    assert response.find("set-header/value").text == "application/json"
    assert json.loads(response.find("set-body").text)["error"]["code"] == "AgentConfigurationIncomplete"
    assert "{{" not in text
    assert not root.findall(".//forward-request") and not root.findall(".//send-request")


def observation(*, resolved=True):
    return Contact("contact-1", "J-1", contact_key("MBR-SYNTHETIC"),
                   START, START + timedelta(hours=1) if resolved else None)


def cost():
    return Cost("receipt-1", "J-1", "gg-counsellor", Decimal("0.12"), "USD", "synthetic receipt")


def compute(contacts, costs, *, days=16, **overrides):
    options = dict(as_of=START+timedelta(days=days), observed_through=START+timedelta(days=days),
                   cohort_start=START, cohort_end=START+timedelta(days=1), cost_scope="measured synthetic receipt")
    options.update(overrides)
    return report(contacts, costs, **options)


@pytest.mark.parametrize("extra", [{"age_band": "75+"}, {"utterance": "private text"}, {"member_ref": "MBR-X"}])
def test_b4_b7_event_contract_rejects_content_and_demographics(extra):
    with pytest.raises(ValueError):
        emit("journey_started", **extra)


def test_content_free_formatter_keeps_attribution_not_payload():
    record = logging.LogRecord("governedgraph", logging.INFO, "", 1, "model_usage", (), None)
    record.journey_id, record.agent_name = "J-1", "gg-advice-classifier"
    record.utterance, record.authorization = "never export", "never export"
    result = json.loads(OperationalFormatter().format(record))
    assert result["journey_id"] == "J-1" and result["agent_name"] == "gg-advice-classifier"
    assert "never export" not in json.dumps(result)


@pytest.mark.parametrize("issues,expected", [
    (["relevance", "grounding"], True),
    (["private utterance"], False),
    ([{"grounding": "private text"}], False),
    ("private text", False),
])
def test_b4_review_console_diagnostics_keep_only_closed_codes(issues, expected):
    record = logging.LogRecord("governedgraph", logging.WARNING, "", 1, "answer_review_nonpass", (), None)
    record.journey_id, record.verdict, record.issues = "J-1", "revise", issues
    record.feedback = [{"quote": "never export draft text"}]
    result = json.loads(OperationalFormatter().format(record))
    assert ("issues" in result) is expected and ("verdict" in result) is expected
    assert "private" not in json.dumps(result) and "never export" not in json.dumps(result)
    if expected:
        assert result["issues"] == issues and result["verdict"] == "revise"


def test_b7_contact_key_is_linkable_pseudonym_not_member_reference():
    assert contact_key("MBR-A") == contact_key("MBR-A") != contact_key("MBR-B")
    assert len(contact_key("MBR-A")) == 64 and "MBR-A" not in contact_key("MBR-A")


def test_all_outbound_headers_have_the_same_journey_trace_root():
    for parent in ("", "00-"+"a"*32+"-"+"b"*16+"-01"):
        result = headers("J-1", parent)
        assert int(result["traceparent"].split("-")[1], 16) == journey_trace_id("J-1")
        assert result["X-Journey-ID"] == "J-1"
    assert headers("J-1")["traceparent"].split("-")[1] != headers("J-2")["traceparent"].split("-")[1]


def test_local_operation_needs_no_azure_dependency():
    with operation("J-1", "test.operation", agent_name="synthetic") as span:
        assert span is None


def test_b4_foundry_connection_uses_project_identity_not_a_key():
    ai = (ROOT / "infra/modules/ai.bicep").read_text()
    connection = ai.split("resource insightsConnection ", 1)[1].split("resource guardrailPolicy ", 1)[0]
    assert "authType: 'ProjectManagedIdentity'" in connection
    assert "useWorkspaceManagedIdentity: true" in connection
    assert "credentials:" not in connection
    assert "ApplicationInsightsConnectionString: appInsightsRouting" in connection
    assert "@secure()\nparam appInsightsRouting string" in ai
    monitoring = (ROOT / "infra/modules/monitoring.bicep").read_text()
    assert "DisableLocalAuth: true" in monitoring


def test_foundry_trace_project_id_is_deployment_metadata():
    ai = (ROOT / "infra/modules/ai.bicep").read_text()
    main = (ROOT / "infra/workloads.bicep").read_text()
    compute = (ROOT / "infra/modules/compute.bicep").read_text()
    assert "output projectResourceId string = project.id" in ai
    assert "AZURE_AI_PROJECT_RESOURCE_ID: ai.outputs.projectResourceId" in main
    assert "output AZURE_AI_PROJECT_RESOURCE_ID string = ai.outputs.projectResourceId" in main
    assert "var serviceEnv = [for k in items(env):" in compute
    assert "env: concat(serviceEnv, a.name == 'strategy-service' ? strategyEnvList : []" in compute


@pytest.mark.parametrize("project_attributes", [
    {},
    {"microsoft.foundry.project.id": PROJECT_ID},
    {"gen_ai.azure_ai_project.id": PROJECT_ID},
    {"microsoft.foundry.project.id": "wrong-project", "gen_ai.azure_ai_project.id": "stale-project"},
])
def test_b4_native_trace_export_preserves_metadata_not_content_or_exception_bodies(monkeypatch, project_attributes):
    import sys
    from types import ModuleType
    from unittest.mock import Mock
    from governedgraph.agents.foundry_tracing import ContentFreeTraceExporter

    for name in ("opentelemetry", "opentelemetry.sdk", "opentelemetry.sdk.resources",
                 "opentelemetry.sdk.trace", "opentelemetry.trace"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    sys.modules["opentelemetry.sdk.resources"].Resource = lambda attrs: attrs
    sys.modules["opentelemetry.sdk.trace"].ReadableSpan = lambda **kwargs: SimpleNamespace(**kwargs)
    sys.modules["opentelemetry.trace"].Status = lambda code: SimpleNamespace(status_code=code)
    span = SimpleNamespace(
        name="invoke_agent gg-counsellor", context=object(), parent=object(),
        resource={"service.name": "gg-orchestrator", "sensitive": "never export"},
        attributes={"journey_id": "J-synthetic", "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.name": "gg-counsellor", "gen_ai.response.id": "response-synthetic",
                    "gen_ai.usage.input_tokens": 12, "gen_ai.input.messages": "never export",
                    "gen_ai.output.messages": "never export", "gen_ai.request.tools": "never export",
                    "error.message": "never export", "age_band": "never export",
                    **project_attributes},
        events=[SimpleNamespace(name="exception", attributes={"exception.message": "never export"})],
        links=[{"sensitive": "never export"}],
        kind="CLIENT", status=SimpleNamespace(status_code="ERROR", description="never export"),
        start_time=1, end_time=2, instrumentation_scope=object(),
    )
    delegate = Mock()
    exporter = ContentFreeTraceExporter(delegate, PROJECT_ID)
    assert exporter.export([span]) is delegate.export.return_value
    exported, = delegate.export.call_args.args[0]
    assert exported.context is span.context and exported.parent is span.parent
    assert exported.attributes == {
        "journey_id": "J-synthetic", "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "gg-counsellor", "gen_ai.response.id": "response-synthetic",
        "gen_ai.usage.input_tokens": 12,
        "microsoft.foundry.project.id": PROJECT_ID,
        "gen_ai.azure_ai_project.id": PROJECT_ID,
    }
    assert any(exported.attributes.get(key) == PROJECT_ID
               for key in ("microsoft.foundry.project.id", "gen_ai.azure_ai_project.id"))
    assert exported.status.status_code == "ERROR"
    assert exported.start_time == 1 and exported.end_time == 2 and exported.kind == "CLIENT"
    assert "never export" not in repr(vars(exported))
    assert span.events and "error.message" in span.attributes
    exporter.force_flush(123)
    delegate.force_flush.assert_called_once_with(123)
    exporter.shutdown()
    delegate.shutdown.assert_called_once_with()
    delegate.export.side_effect = RuntimeError("export failed")
    with pytest.raises(RuntimeError, match="export failed"):
        exporter.export([span])


@pytest.mark.parametrize("project_id", ["", "internal-project-id", "https://synthetic.invalid/api/projects/example"])
def test_foundry_trace_export_rejects_missing_or_non_arm_project_id(project_id):
    from governedgraph.agents.foundry_tracing import ContentFreeTraceExporter

    with pytest.raises(ValueError, match="AZURE_AI_PROJECT_RESOURCE_ID"):
        ContentFreeTraceExporter(object(), project_id)


def test_b4_uplift_signal_uses_only_governed_aggregates(caplog):
    from unittest.mock import Mock
    from governedgraph.learning.uplift import evaluate_window

    caplog.set_level(logging.INFO, logger="governedgraph.observability")
    result = evaluate_window({
        "window_id": "W-synthetic", "window_closed": True,
        "treatment": {"interactions": 10000, "resolved": 9200, "repeat_contacts": 100},
        "control": {"interactions": 10000, "resolved": 8000, "repeat_contacts": 400},
        "watermark": "ILLUSTRATIVE - synthetic data",
    }, Mock())
    record = caplog.records[-1]
    assert record.getMessage() == "graph_uplift" and record.window_id == result["window_id"]
    assert record.fcr_uplift_pts == result["fcr_uplift_pts"]
    assert not hasattr(record, "treatment") and not hasattr(record, "control")


def test_usage_includes_cached_inputs_and_keeps_raw_response_out(caplog):
    caplog.set_level(logging.INFO, logger="governedgraph.observability")
    response = SimpleNamespace(id="response-1", output_text="private",
                               usage=SimpleNamespace(input_tokens=12, output_tokens=3,
                                                     input_tokens_details=SimpleNamespace(cached_tokens=4)))
    model_usage(response, journey_id="J-1", agent_name="agent", model="gpt-compact")
    record = caplog.records[-1]
    assert record.input_tokens == 12 and record.cached_input_tokens == 4 and record.output_tokens == 3
    assert record.usage_complete is True and not hasattr(record, "output_text") and "private" not in caplog.text


@pytest.mark.parametrize("usage", [None, SimpleNamespace(input_tokens=True, output_tokens=0),
                                  SimpleNamespace(input_tokens=2, output_tokens=1,
                                                  input_tokens_details=SimpleNamespace(cached_tokens=3))])
def test_missing_or_invalid_usage_is_unknown_not_free(usage, caplog):
    caplog.set_level(logging.INFO, logger="governedgraph.observability")
    model_usage(SimpleNamespace(id="response-1", usage=usage), journey_id="J-1", agent_name="agent", model="gpt-compact")
    record = caplog.records[-1]
    assert record.getMessage() == "model_usage_missing" and record.usage_complete is False
    assert not hasattr(record, "input_tokens")


def test_cost_requires_actual_resolution_and_full_fourteen_days():
    assert compute([observation()], [cost()], days=14)["cost_per_resolved_query"] is None
    assert compute([observation()], [cost()])["cost_per_resolved_query"] == "0.12"
    assert compute([observation(resolved=False)], [cost()])["cost_per_resolved_query"] is None


@pytest.mark.parametrize("new_journey", ["J-1", "J-2"])
def test_repeat_contact_same_or_new_journey_at_day_fourteen_disqualifies(new_journey):
    first = observation()
    repeated = Contact("contact-2", new_journey, first.contact_key,
                       first.resolved_at + timedelta(days=14))
    result = compute([first, repeated], [cost()])
    assert result["qualified_resolutions"] == 0 and result["cost_per_resolved_query"] is None


def test_repeat_after_window_or_other_member_does_not_disqualify():
    first = observation()
    later = Contact("contact-2", "J-2", first.contact_key, first.resolved_at+timedelta(days=14, seconds=1))
    other = Contact("contact-3", "J-3", contact_key("OTHER"), START+timedelta(days=2))
    assert compute([first, later, other], [cost()])["qualified_resolutions"] == 1


def test_cost_numerator_includes_failed_journeys_and_agent_attribution():
    failed = Contact("contact-2", "J-2", contact_key("OTHER"), START, None)
    extra = Cost("receipt-2", "J-2", "gg-advice-classifier", Decimal("0.03"), "USD", "synthetic receipt")
    result = compute([observation(), failed], [cost(), extra])
    assert result["cost_per_resolved_query"] == "0.15"


def test_resumed_old_journey_does_not_enter_a_new_start_cohort():
    old = Contact("old", "J-1", contact_key("MBR"), START-timedelta(days=1))
    assert compute([old, observation()], [cost()])["cohort_journeys"] == 0


def test_no_cost_receipt_is_not_assumed_zero():
    result = compute([observation()], [])
    assert result["observed_cost"] is None and result["cost_per_resolved_query"] is None


def test_incomplete_observation_cannot_prove_no_repeat():
    with pytest.raises(ValueError):
        compute([observation()], [cost()], observed_through=START)


def test_duplicate_or_mixed_currency_evidence_is_rejected():
    with pytest.raises(ValueError):
        compute([observation(), observation()], [cost()])
    with pytest.raises(ValueError):
        compute([observation()], [cost(), cost()])
    other = Cost("receipt-2", "J-1", "agent", Decimal("1"), "EUR", "synthetic receipt")
    with pytest.raises(ValueError):
        compute([observation()], [cost(), other])


def test_b7_closed_cost_contract_and_exact_decimal_amount():
    fields = dict(receipt_id="r", journey_id="J-1", agent_name="agent",
                  amount="0.01", currency="USD", source="synthetic")
    assert Cost.from_dict(fields).amount == Decimal("0.01")
    with pytest.raises(ValueError):
        Cost.from_dict({**fields, "amount": 0.01})
    with pytest.raises(ContractError):
        Cost.from_dict({**fields, "age_band": "75+"})


def test_gateway_preserves_identity_pins_and_fails_closed():
    root = ET.parse(ROOT/"infra/policies/model-responses.xml").getroot()
    gateway = (ROOT/"infra/modules/model-gateway.bicep").read_text()
    assert "loadTextContent('../policies/model-responses.xml')" in gateway
    assert root.find(".//validate-azure-ad-token/client-application-ids/application-id") is not None
    limit = root.find(".//llm-token-limit")
    assert "X-Journey-ID" in limit.attrib["counter-key"] and limit.attrib["token-quota"] == "120000"
    assert limit.attrib["tokens-per-minute"] == "__CONVERSATION_TOKENS_PER_MINUTE__"
    assert limit.attrib["token-quota-period"] == "Daily"
    assert "param conversationTokensPerMinute int = 12000" in gateway
    assert root.find(".//authentication-managed-identity") is None
    assert root.find(".//retry") is None
    text = (ROOT/"infra/policies/model-responses.xml").read_text()
    for role, name in (
        ("main", "gg-counsellor"),
        ("compact", "gg-counsellor-voice-compact"),
        ("intent", "gg-intent-agent"),
        ("understanding", "gg-understanding-agent"),
        ("review", "gg-answer-review-agent"),
    ):
        assert f'name == "{name}"' in text and f"{{{{model-{role}-agent-version}}}}" in text
        assert f"'model-{role}-agent-version':" in gateway
    pinned = next(when for when in root.findall(".//when")
                  if when.find(".//set-status[@reason='Approved pinned agent required']") is not None)
    assert pinned.attrib["condition"].count('name == "') == 5
    main = (ROOT/"infra/workloads.bicep").read_text()
    assert "AZURE_AI_REVIEW_AGENT_VERSION: reviewAgentVersion" in main
    assert "reviewAgentVersion: reviewAgentVersion" in main
    assert root.find(".//set-body") is None


def test_b1_b2_b8_generated_answers_stay_suspended_after_failed_hosted_acceptance():
    root = ET.parse(ROOT/"infra/policies/model-responses.xml").getroot()
    pause = next(when for when in root.findall(".//when")
                 if when.find(".//set-status[@reason='Generated answers suspended pending semantic acceptance']")
                 is not None)
    status = pause.find(".//set-status")
    assert status is not None and status.attrib["code"] == "503"
    assert pause.attrib["condition"].count('name == "') == 2
    for writer in ("gg-counsellor", "gg-counsellor-voice-compact"):
        assert f'name == "{writer}"' in pause.attrib["condition"]
    for decision in ("gg-intent-agent", "gg-understanding-agent", "gg-answer-review-agent"):
        assert decision not in pause.attrib["condition"]


def test_b9_gateway_names_the_conversation_quota_refusal_for_the_host():
    from governedgraph.agents.counsellor import QUOTA_HEADER, QUOTA_HEADER_VALUE

    root = ET.parse(ROOT/"infra/policies/model-responses.xml").getroot()
    refusal = root.find("./on-error//return-response")
    assert refusal.find("set-status").attrib["code"] == "403"
    header = refusal.find("set-header")
    assert header.attrib["name"] == QUOTA_HEADER and header.find("value").text == QUOTA_HEADER_VALUE
    assert "llm-token-limit" in root.find("./on-error//when").attrib["condition"]


def test_b8_only_exact_project_scoped_human_approval_can_leave_the_protective_writer_hold():
    root = ET.parse(ROOT/"infra/policies/model-responses.xml").getroot()
    pause = next(when for when in root.findall(".//when")
                 if when.find(".//set-status[@reason='Generated answers suspended pending semantic acceptance']")
                 is not None)
    assert " ".join(pause.attrib["condition"].split()) == (
        '@{ var body = context.Request.Body.As<Newtonsoft.Json.Linq.JObject>(preserveContent: true); '
        'var name = (string)body["agent_reference"]["name"]; '
        'var version = (string)body["agent_reference"]["version"]; '
        'var isWriter = name == "gg-counsellor" || name == "gg-counsellor-voice-compact"; '
        'if (!isWriter) { return false; } '
        'var approval = Newtonsoft.Json.Linq.JObject.Parse(System.Text.Encoding.UTF8.GetString( '
        'System.Convert.FromBase64String("{{model-writer-approval}}"))); '
        'var writers = approval["writers"] as Newtonsoft.Json.Linq.JObject; '
        'var writer = writers == null ? null : writers[name] as Newtonsoft.Json.Linq.JObject; '
        'var approved = approval["approved"]; '
        'return !((string)approval["tier"] == "tier2" '
        '&& approved != null && approved.Type == Newtonsoft.Json.Linq.JTokenType.Boolean && (bool)approved '
        '&& (string)approval["project_endpoint"] == "{{model-project-endpoint}}" '
        '&& System.Text.RegularExpressions.Regex.IsMatch((string)approval["decision_ref"] ?? "", "^(?:GB|HR)-[A-Za-z0-9][A-Za-z0-9._-]{0,127}$") '
        '&& !string.IsNullOrWhiteSpace((string)approval["evidence_ref"]) '
        '&& writer != null && (string)writer["version"] == version '
        '&& System.Text.RegularExpressions.Regex.IsMatch((string)writer["definition_sha256"] ?? "", "^[0-9a-f]{64}$")); }'
    )
    gateway = (ROOT / "infra/modules/model-gateway.bicep").read_text()
    assert "var writerApproval = empty(writerApprovalJson) ? {} : json(writerApprovalJson)" in gateway
    assert "'model-writer-approval': base64(string(writerApproval))" in gateway
    assert "'model-project-endpoint': projectEndpoint" in gateway
    assert "@maxLength(3000)\nparam writerApprovalJson string = ''" in gateway
    from scripts.delivery import parameters
    assert parameters({"writerApproval": None})["parameters"]["writerApproval"]["value"] is None
    main = (ROOT / "infra/workloads.bicep").read_text()
    assert "param writerApproval object?\n" in main
    assert "writerApprovalJson: empty(writerApproval) ? '' : string(writerApproval)" in main


@pytest.mark.parametrize("raw", [
    None,
    "",
    "{}",
    json.dumps({"approved": False}),
    json.dumps({"approved": True, "decision_ref": "HR-SYNTHETIC",
                "evidence_ref": 'ILLUSTRATIVE - synthetic "quoted" evidence\\reference\nline'}),
])
def test_azd_approval_object_substitution_preserves_json_and_empty_revocation(raw):
    from scripts.delivery import parameters
    resolved = parameters({"writerApproval": json.loads(raw or "null")})["parameters"]["writerApproval"]["value"]
    assert resolved is None or isinstance(resolved, dict)
    gateway_json = json.dumps(resolved) if resolved else ""
    gateway_approval = json.loads(gateway_json) if gateway_json else {}
    assert gateway_approval == (json.loads(raw) if raw else {})
    if not raw or raw == "{}":
        assert gateway_approval.get("approved") is not True
    if raw and '"' in raw:
        with pytest.raises(json.JSONDecodeError):
            json.loads(json.dumps({"value": "${APPROVAL}"}).replace("${APPROVAL}", raw))


def test_workbook_never_substitutes_old_voice_slo_or_assumed_cost():
    slos = (ROOT/"infra/queries/journey-slos.kql").read_text()
    query = (ROOT/"infra/queries/resolved-cost.kql").read_text()
    assert "target_ms=1000.0" in slos and "target_ms=6000.0" in slos and "n >= 20" in slos
    assert "ResolvedAt+14d <= AsOf" in query and "ContactAt <= ResolvedAt+14d" in query
    assert "full_stack_cost_per_resolved_query_usd=real(null)" in query
