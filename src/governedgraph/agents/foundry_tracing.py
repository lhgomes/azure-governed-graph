"""Content-free by default; explicitly opted-in synthetic dev conversations only."""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

from governedgraph.common import domain
from governedgraph.common.config import settings

_journey: ContextVar[str] = ContextVar("gg_trace_journey", default="")
_provider = None
_lock = Lock()
SPAN_ATTRIBUTES = {
    "journey_id", "gen_ai.agent.name", "gen_ai.agent.id", "gen_ai.agent.version",
    "gen_ai.operation.name", "gen_ai.provider.name", "gen_ai.system",
    "gen_ai.request.model", "gen_ai.response.model", "gen_ai.response.id",
    "gen_ai.conversation.id", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens", "server.address", "server.port", "az.namespace",
    "governedgraph.turn.duration_ms", "governedgraph.prompt_shields.duration_ms",
    "governedgraph.voice_turn.slo_met", "governedgraph.voice_turn.measurement",
    "http.response.status_code", "http.status_code", "error.type", "governedgraph.error.type",
    "governedgraph.decision.code", "governedgraph.decision.reason",
    "governedgraph.decision.validation_stage", "governedgraph.decision.failure_code",
    "governedgraph.dependency.operation", "governedgraph.dependency.outcome", "governedgraph.dependency.http_status_code",
    "governedgraph.dependency.queue", "governedgraph.dependency.attempt", "governedgraph.dependency.idle_seconds",
    "governedgraph.dependency.amqp_condition", "governedgraph.dependency.cause",
    "governedgraph.synthetic.conversation", "governedgraph.synthetic.watermark", "governedgraph.conversation.capture_status",
}
CONVERSATION_SPAN = "invoke_agent governedgraph.synthetic_conversation"
SYNTHETIC_TRACE_MEMBERS = domain.demo()["trace_members"]
_CREDENTIAL = re.compile(
    r"\bBearer\s+\S+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|\b(?:authorization|password|accountkey|api[_-]?key|client[_-]?secret)\s*[:=]"
    r"|[?&](?:sig|token|key|code)=", re.IGNORECASE,
)


def synthetic_capture_enabled() -> bool:
    return (settings.environment_name == "gg-dev"
            and os.getenv("GOVERNEDGRAPH_DEPLOY_VOICE_DEMO") == "true"
            and os.getenv("GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS") == "true")


def _message(role: str, text: str) -> tuple[str, str]:
    status = "captured"
    if _CREDENTIAL.search(text):
        text, status = "[Trace text withheld: possible credential.]", "credential_withheld"
    body = [{"role": role, "parts": [{"type": "text", "content": text}]}]
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 7500:
        return _message(role, "[Trace text withheld: size limit.]")[0], "size_withheld"
    return encoded, status


def record_synthetic_conversation(journey_id: str, member_ref: str, user_text: str | None,
                                  checked_text: str, response_id: str) -> None:
    if not synthetic_capture_enabled() or member_ref not in SYNTHETIC_TRACE_MEMBERS:
        return
    from governedgraph.observability import operation

    incoming, in_status = _message("user", user_text) if user_text is not None else ("[]", "captured")
    outgoing, out_status = _message("assistant", checked_text)
    with operation(journey_id, CONVERSATION_SPAN, agent_name=settings.agent_name) as span:
        span.set_attributes({
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.id": f"{settings.agent_name}:{settings.agent_version}",
            "gen_ai.agent.version": settings.agent_version,
            "gen_ai.conversation.id": journey_id, "gen_ai.response.id": response_id,
            "governedgraph.synthetic.conversation": True,
            "governedgraph.synthetic.watermark": "ILLUSTRATIVE - synthetic data",
            "governedgraph.conversation.capture_status": out_status if in_status == "captured" else in_status,
            "gen_ai.input.messages": incoming, "gen_ai.output.messages": outgoing,
        })


AMQP_CONDITIONS = frozenset({
    "amqp:connection:forced", "amqp:connection:framing-error", "amqp:link:detach-forced",
    "amqp:link:transfer-limit-exceeded", "amqp:internal-error", "amqp:resource-limit-exceeded",
    "amqp:unauthorized-access", "amqp:not-found",
})
DEPENDENCY_CAUSES = frozenset({"idle_timeout", "handler_shutdown", "connection_reset", "unknown"})


def dependency_outcome(journey_id: str, name: str, outcome: str, error=None, *,
                       queue: str = "", attempt: int | None = None, idle_seconds: float | None = None) -> None:
    import math

    if name not in {"blob_create_if_absent", "strategy_conditional_read", "service_bus_send"}:
        raise ValueError("unapproved dependency diagnostic")
    if outcome not in {"already_exists", "not_modified", "reconnecting", "recovered", "idle_recycled", "failed"}:
        raise ValueError("unapproved dependency outcome")
    if queue not in {"", "journey-events", "escalations"} or (attempt is not None and (
            type(attempt) is not int or attempt not in {1, 2})):
        raise ValueError("unapproved dependency diagnostic context")
    if idle_seconds is not None and (not math.isfinite(idle_seconds) or idle_seconds < 0):
        raise ValueError("invalid sender idle age")
    attributes = {"governedgraph.dependency.operation": name, "governedgraph.dependency.outcome": outcome}
    if queue:
        attributes["governedgraph.dependency.queue"] = queue
    if attempt is not None:
        attributes["governedgraph.dependency.attempt"] = attempt
    if idle_seconds is not None:
        attributes["governedgraph.dependency.idle_seconds"] = int(idle_seconds)
    if journey_id or _journey.get():
        attributes["journey_id"] = journey_id or _journey.get()
    with configure_tracing().get_tracer("governedgraph").start_as_current_span(
        "governedgraph.dependency.outcome", attributes=attributes,
        record_exception=False, set_status_on_exception=False,
    ) as span:
        if error is not None:
            span.set_attribute("error.type", type(error).__name__)
            status = getattr(error, "status_code", None)
            if type(status) is int and 100 <= status <= 599:
                span.set_attribute("governedgraph.dependency.http_status_code", status)
            if name == "service_bus_send":
                message = str(error).lower()
                condition = re.search(r"\bamqp:[a-z-]+(?::[a-z-]+)?", message)
                if condition is not None and condition[0] in AMQP_CONDITIONS:
                    span.set_attribute("governedgraph.dependency.amqp_condition", condition[0])
                cause = ("idle_timeout" if "idletimerexpired" in message or "idle timeout" in message else
                         "handler_shutdown" if "handler has already been shutdown" in message else
                         "connection_reset" if "connectionreseterror" in message else "unknown")
                span.set_attribute("governedgraph.dependency.cause", cause)
        if outcome == "failed":
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR))


class ContentFreeTraceExporter:
    def __init__(self, exporter, project_resource_id: str, *, synthetic_conversations: bool = False):
        if not re.fullmatch(
            r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices"
            r"/accounts/[^/]+/projects/[^/]+", project_resource_id, re.IGNORECASE,
        ):
            raise ValueError("AZURE_AI_PROJECT_RESOURCE_ID must be the Foundry project's ARM resource ID")
        self.exporter = exporter
        self.project_resource_id = project_resource_id
        self.synthetic_conversations = synthetic_conversations

    def export(self, spans):
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.trace import Status

        # SDK exception events and status descriptions can contain response bodies.
        def attributes(span):
            source = span.attributes or {}
            approved = {key: value for key, value in source.items() if key in SPAN_ATTRIBUTES}
            for key, allowed in (
                ("governedgraph.decision.validation_stage", {
                    "request", "completion", "json", "review_checks", "review_consistency",
                    "decision_contract", "audit_evidence",
                }),
                ("governedgraph.decision.failure_code", {
                    "intent_classification", "incomplete", "review_object", "review_checks",
                    "review_consistency", "review_verdict", "review_issues", "review_feedback",
                    "review_concepts", "untaught_concept", "nonpass_concepts", "schema_or_dependency",
                }),
                ("governedgraph.dependency.queue", {"journey-events", "escalations"}),
                ("governedgraph.dependency.amqp_condition", AMQP_CONDITIONS),
                ("governedgraph.dependency.cause", DEPENDENCY_CAUSES),
                ("governedgraph.dependency.attempt", {1, 2}),
            ):
                if key in approved and (not isinstance(approved[key], (str, int)) or approved[key] not in allowed):
                    approved.pop(key)
            idle = approved.get("governedgraph.dependency.idle_seconds")
            if idle is not None and (type(idle) is not int or idle < 0):
                approved.pop("governedgraph.dependency.idle_seconds")
            if "governedgraph.dependency.attempt" in approved and type(approved["governedgraph.dependency.attempt"]) is not int:
                approved.pop("governedgraph.dependency.attempt")
            error_type = source.get("error.type")
            if error_type is None:
                error_type = next(((event.attributes or {}).get("exception.type")
                                   for event in span.events if event.name == "exception"), None)
            if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,159}", error_type):
                approved["error.type"] = approved["governedgraph.error.type"] = error_type
            else:
                approved.pop("error.type", None)
                approved.pop("governedgraph.error.type", None)
            if (self.synthetic_conversations and synthetic_capture_enabled()
                    and span.name == CONVERSATION_SPAN
                    and source.get("governedgraph.synthetic.conversation") is True
                    and source.get("governedgraph.synthetic.watermark") == "ILLUSTRATIVE - synthetic data"):
                for key, role in (("gen_ai.input.messages", "user"), ("gen_ai.output.messages", "assistant")):
                    value = source.get(key)
                    if not isinstance(value, str) or len(value.encode("utf-8")) > 7500:
                        continue
                    try:
                        messages = json.loads(value)
                    except ValueError:
                        continue
                    if messages == [] and role == "user":
                        approved[key] = "[]"
                    elif (isinstance(messages, list) and len(messages) == 1
                          and isinstance(messages[0], dict) and set(messages[0]) == {"role", "parts"}
                          and messages[0]["role"] == role and isinstance(messages[0]["parts"], list)
                          and len(messages[0]["parts"]) == 1 and isinstance(messages[0]["parts"][0], dict)
                          and set(messages[0]["parts"][0]) == {"type", "content"}
                          and messages[0]["parts"][0]["type"] == "text"
                          and isinstance(messages[0]["parts"][0]["content"], str)):
                        approved[key] = _message(role, messages[0]["parts"][0]["content"])[0]
            return {**approved, "microsoft.foundry.project.id": self.project_resource_id,
                    "gen_ai.azure_ai_project.id": self.project_resource_id}

        safe = [ReadableSpan(
            name=span.name, context=span.context, parent=span.parent,
            resource=Resource({"service.name": os.getenv("GOVERNEDGRAPH_SERVICE_NAME", "gg-orchestrator")}),
            attributes=attributes(span),
            events=(), links=(), kind=span.kind, status=Status(span.status.status_code),
            start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        ) for span in spans]
        return self.exporter.export(safe)

    def shutdown(self):
        return self.exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis)


def journey_trace_id(journey_id: str) -> int:
    return int(hashlib.sha256(("governedgraph:" + journey_id).encode()).hexdigest()[:32], 16) or 1


def configure_tracing():
    from governedgraph.observability import monitor_export_options

    global _provider
    with _lock:
        if _provider is not None:
            return _provider
        if not settings.appinsights_connection_string:
            raise ValueError("Entra-authenticated Application Insights routing is required")
        from azure.core.settings import settings as azure_settings

        azure_settings.tracing_implementation = "opentelemetry"
        from azure.ai.projects.telemetry import AIProjectInstrumentor
        from azure.identity import DefaultAzureCredential
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

        class JourneyIdGenerator(RandomIdGenerator):
            def generate_trace_id(self) -> int:
                journey_id = _journey.get()
                return journey_trace_id(journey_id) if journey_id else super().generate_trace_id()

        os.environ["AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"] = "true"
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
        os.environ["AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"] = "false"
        resource = Resource.create({"service.name": os.getenv("GOVERNEDGRAPH_SERVICE_NAME", "gg-orchestrator")})
        provider = TracerProvider(id_generator=JourneyIdGenerator(), resource=resource)
        provider.add_span_processor(BatchSpanProcessor(ContentFreeTraceExporter(AzureMonitorTraceExporter(
            connection_string=settings.appinsights_connection_string,
            credential=DefaultAzureCredential(), **monitor_export_options("traces"),
        ), settings.project_resource_id, synthetic_conversations=synthetic_capture_enabled())))
        trace.set_tracer_provider(provider)
        AIProjectInstrumentor().instrument(enable_content_recording=False,
                                          enable_trace_context_propagation=True,
                                          enable_baggage_propagation=False)
        _provider = provider
        return provider


@contextmanager
def journey_span(tools):
    from governedgraph.observability import operation

    with operation(tools.journey.journey_id, "governedgraph.member_journey",
                   agent_name=tools.agent_name, parent=tools.trace_parent) as span:
        span.set_attributes({"gen_ai.agent.version": tools.agent_version,
                             "gen_ai.request.model": tools.model_deployment})
        ctx = span.get_span_context()
        tools.trace_parent = f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-01"
        yield span


def flush_traces() -> None:
    from governedgraph.observability import flush_events

    if _provider is not None and not _provider.force_flush(timeout_millis=10000):
        raise RuntimeError("Foundry trace export did not flush")
    flush_events()
