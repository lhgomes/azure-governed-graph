"""ILLUSTRATIVE - synthetic data. Dev conversations never enable unrestricted SDK content."""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from governedgraph.agents import foundry_tracing
from governedgraph.agents.foundry_tracing import CONVERSATION_SPAN, ContentFreeTraceExporter

PROJECT = ("/subscriptions/synthetic/resourceGroups/synthetic/providers/Microsoft.CognitiveServices/"
           "accounts/synthetic/projects/synthetic")


@pytest.fixture
def opted_in(monkeypatch):
    monkeypatch.setattr(foundry_tracing, "settings", SimpleNamespace(
        is_azure=True, environment_name="gg-dev", agent_name="gg-counsellor", agent_version="2",
    ))
    monkeypatch.setenv("GOVERNEDGRAPH_DEPLOY_VOICE_DEMO", "true")
    monkeypatch.setenv("GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS", "true")


def span(name=CONVERSATION_SPAN, *, events=(), **attributes):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

    return ReadableSpan(
        name=name, context=SpanContext(1, 2, False, TraceFlags(1)), parent=None,
        resource=Resource({"service.name": "test"}),
        attributes=attributes, events=events, links=(), kind=SpanKind.CLIENT,
        status=Status(StatusCode.ERROR, "private exception body"), start_time=1, end_time=2,
    )


def conversation_attributes():
    return {
        "gen_ai.operation.name": "invoke_agent",
        "governedgraph.synthetic.conversation": True, "governedgraph.synthetic.watermark": "ILLUSTRATIVE - synthetic data",
        "gen_ai.input.messages": foundry_tracing._message("user", "Synthetic question.")[0],
        "gen_ai.output.messages": foundry_tracing._message("assistant", "Checked synthetic answer.")[0],
        "gen_ai.request.tools": "never export tool arguments",
        "error.message": "never export exception bodies", "http.response.status_code": 409,
        "error.type": "ResourceExistsError",
    }


@pytest.mark.parametrize("explicit", [False, True])
def test_b4_default_strips_content_and_explicit_dev_preserves_only_checked_host_messages(opted_in, explicit):
    delegate = Mock()
    exporter = ContentFreeTraceExporter(delegate, PROJECT, synthetic_conversations=explicit)
    exporter.export([span(**conversation_attributes())])
    exported, = delegate.export.call_args.args[0]
    assert ("gen_ai.input.messages" in exported.attributes) is explicit
    assert ("gen_ai.output.messages" in exported.attributes) is explicit
    assert exported.attributes["http.response.status_code"] == 409
    assert exported.attributes["error.type"] == "ResourceExistsError"
    assert exported.status.status_code.name == "ERROR" and exported.status.description is None
    assert "gen_ai.request.tools" not in exported.attributes and "error.message" not in exported.attributes


@pytest.mark.parametrize("changes,flag", [
    ({"environment_name": "production"}, "true"),
    ({"is_azure": False}, "true"),
    ({}, "false"),
])
def test_b4_production_or_disabled_flags_never_export_conversation_even_with_markers(
        opted_in, monkeypatch, changes, flag):
    monkeypatch.setattr(foundry_tracing, "settings",
                        SimpleNamespace(**{**vars(foundry_tracing.settings), **changes}))
    monkeypatch.setenv("GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS", flag)
    delegate = Mock()
    ContentFreeTraceExporter(delegate, PROJECT, synthetic_conversations=True).export([span(**conversation_attributes())])
    assert "gen_ai.input.messages" not in delegate.export.call_args.args[0][0].attributes


def test_b4_sdk_drafts_and_tools_still_cannot_export_content_in_dev_mode(opted_in):
    delegate = Mock()
    ContentFreeTraceExporter(delegate, PROJECT, synthetic_conversations=True).export([
        span(name="invoke_agent sdk", **conversation_attributes()),
    ])
    attributes = delegate.export.call_args.args[0][0].attributes
    assert not ({"gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.request.tools"} & attributes.keys())


def test_b4_sanitized_trace_buffer_recovers_after_a_bounded_transport_failure(monkeypatch, tmp_path):
    import sys
    import time
    import io
    import requests
    from urllib3.response import HTTPResponse
    from azure.core.credentials import AccessToken
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    from opentelemetry.sdk.trace.export import SpanExportResult
    from governedgraph import observability

    monkeypatch.setattr(observability, "settings", SimpleNamespace(run_dir=tmp_path))
    monkeypatch.setenv("APPLICATIONINSIGHTS_STATSBEAT_DISABLED", "true")
    base = AzureMonitorTraceExporter.__mro__[1]
    monkeypatch.setattr(base, "_should_collect_stats", lambda self: False)
    monkeypatch.setattr(base, "_should_collect_customer_sdkstats", lambda self: False)
    monkeypatch.setattr(sys.modules[base.__module__], "get_configuration_manager", lambda: None)
    calls = []

    def request(session, method, url, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise requests.exceptions.ReadTimeout("synthetic ingestion outage")
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = b'{"itemsReceived":1,"itemsAccepted":1,"errors":[]}'
        response._content_consumed = True
        response.raw = HTTPResponse(body=io.BytesIO(response._content), status=200)
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    options = observability.monitor_export_options("traces")
    options["storage_min_retry_interval"] = 0
    delegate = AzureMonitorTraceExporter(
        connection_string="InstrumentationKey=00000000-0000-0000-0000-000000000001",
        credential=SimpleNamespace(get_token=lambda *args, **kwargs:
                                   AccessToken("synthetic-monitor-token", int(time.time()) + 3600)),
        tracer_provider=SimpleNamespace(resource=span().resource),
        **options,
    )
    try:
        exporter = ContentFreeTraceExporter(delegate, PROJECT)
        assert exporter.export([span(**conversation_attributes())]) == SpanExportResult.FAILURE
        files = [path for path in (tmp_path / "telemetry" / "traces").rglob("*") if path.is_file()]
        assert files
        buffered = b"".join(path.read_bytes() for path in files)
        assert b"gen_ai.input.messages" not in buffered
        assert b"gen_ai.output.messages" not in buffered and b"private exception body" not in buffered
        assert b"synthetic-monitor-token" not in buffered
        assert exporter.export([span(name="governedgraph.recovered")]) == SpanExportResult.SUCCESS
        assert len(calls) == 3 and all(timeout == (5, 5) for timeout in calls)
        assert delegate.storage is not None and not list(delegate.storage.gets())
    finally:
        delegate.shutdown()


@pytest.mark.parametrize("error_type,expected", [
    ("azure.core.exceptions.ResourceExistsError", True),
    ("ResourceNotModifiedError", True),
    ("Bearer private-value", False),
])
def test_b4_sdk_exception_type_survives_without_exception_bodies_or_falsified_success(error_type, expected):
    from opentelemetry.sdk.trace import Event

    delegate = Mock()
    ContentFreeTraceExporter(delegate, PROJECT).export([span(
        name="BlobClient.download_blob",
        events=(Event("exception", {"exception.type": error_type,
                                    "exception.message": "private response body",
                                    "exception.stacktrace": "private stack"}),),
    )])
    exported, = delegate.export.call_args.args[0]
    assert ("governedgraph.error.type" in exported.attributes) is expected
    if expected:
        assert exported.attributes["governedgraph.error.type"] == error_type
    assert not exported.events and exported.status.status_code.name == "ERROR"
    assert "private" not in repr(dict(exported.attributes))


@pytest.mark.parametrize("stage,code,retained", [
    ("decision_contract", "intent_classification", True),
    ("review_consistency", "review_feedback", True),
    ("private member text", "private response body", False),
    (["private member text"], ["private response body"], False),
])
def test_b4_closed_validation_diagnostics_survive_without_exporting_raw_decisions(stage, code, retained):
    delegate = Mock()
    ContentFreeTraceExporter(delegate, PROJECT).export([span(
        name="invoke_agent gg-intent-agent",
        **{"governedgraph.decision.validation_stage": stage, "governedgraph.decision.failure_code": code},
    )])
    exported, = delegate.export.call_args.args[0]
    assert ("governedgraph.decision.validation_stage" in exported.attributes) is retained
    assert ("governedgraph.decision.failure_code" in exported.attributes) is retained
    assert "private" not in repr(dict(exported.attributes))


def test_b4_dependency_diagnostics_do_not_export_arbitrary_cause_or_queue_values():
    delegate = Mock()
    ContentFreeTraceExporter(delegate, PROJECT).export([span(
        name="governedgraph.dependency.outcome",
        **{"governedgraph.dependency.cause": "private text", "governedgraph.dependency.queue": "private queue",
           "governedgraph.dependency.amqp_condition": "private payload", "governedgraph.dependency.attempt": True,
           "governedgraph.dependency.idle_seconds": "private duration"},
    )])
    exported, = delegate.export.call_args.args[0]
    assert not any(key.startswith("governedgraph.dependency.") for key in exported.attributes)
    assert exported.status.status_code.name == "ERROR"


@pytest.mark.parametrize("text", [
    "Bearer synthetic-secret-value",
    "Authorization: private-value",
    "https://synthetic.example/?sig=private-value",
    "api_key=private-value",
    "eyJabcdefgh.abcdefgh.abcdefgh",
    "x" * 8000,
])
def test_b4_possible_credentials_and_oversized_text_are_withheld_not_logged(text):
    encoded, status = foundry_tracing._message("user", text)
    assert status != "captured" and text not in encoded
    assert len(encoded.encode("utf-8")) <= 7500 and isinstance(json.loads(encoded), list)


def test_b4_capture_is_only_for_authenticated_allowlisted_synthetic_members(opted_in, monkeypatch):
    from governedgraph import observability

    observed = []
    @contextmanager
    def operation(*args, **kwargs):
        captured = {}
        observed.append(captured)
        yield SimpleNamespace(set_attributes=captured.update)
    monkeypatch.setattr(observability, "operation", operation)
    foundry_tracing.record_synthetic_conversation("J-synthetic", "MBR-OTHER", "Question", "Answer", "response")
    assert observed == []
    foundry_tracing.record_synthetic_conversation("J-synthetic", "MBR-7Q2K", "Question", "Answer", "response")
    assert len(observed) == 1
    assert json.loads(observed[0]["gen_ai.output.messages"])[0]["parts"][0]["content"] == "Answer"
    assert observed[0]["gen_ai.conversation.id"] == "J-synthetic"


def test_b4_trace_observation_occurs_after_durable_delivery_and_not_on_failed_outbox(tmp_path, monkeypatch):
    from governedgraph.agents.counsellor import Counsellor
    from governedgraph.common.member_auth import MemberIdentity
    from governedgraph.explainability.store import LocalExplainabilityStore
    from governedgraph.learning.run_cycle import run_cycle
    from governedgraph.orchestrator.service import DeliveryPending, OrchestratorService
    from governedgraph.orchestrator.store import FileJourneyStore
    from governedgraph.strategy_service.service import StrategyService

    service = StrategyService(state_dir=tmp_path / "strategy", control_share=0)
    run_cycle(service)
    observed, sent = [], []
    def observe(*args):
        assert sent
        observed.append(args)
    monkeypatch.setattr(foundry_tracing, "record_synthetic_conversation", observe)
    publisher = SimpleNamespace(send=lambda *_: sent.append(True))
    runtime = OrchestratorService(
        FileJourneyStore(tmp_path / "journeys"), publisher,
        counsellor_factory=lambda j, auth, **kwargs: Counsellor(j, auth, strategy_service=service, **kwargs),
        audit_store=LocalExplainabilityStore(tmp_path / "audit.jsonl"),
    )
    identity = MemberIdentity("MBR-7Q2K", True)
    start = runtime.start(identity, "chat", "standard")
    result = runtime.turn(start["journey_id"], "turn-one", identity, "", "Will this change impact my monthly payments?")
    assert observed[-1][3] == result["text"] and result["numeric_fidelity_passed"]
    count = len(observed)
    def fail(*_):
        raise TimeoutError("synthetic dependency error")
    publisher.send = fail
    with pytest.raises(DeliveryPending):
        runtime.turn(start["journey_id"], "turn-two", identity, "", "Please explain my transition letter.")
    assert len(observed) == count
