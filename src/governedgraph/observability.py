"""Content-free operational evidence, never a source of runtime learning."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import logging
import math
import os
import re
from threading import Lock
from typing import Literal

from governedgraph.common.config import settings
from governedgraph.common.tracing import traceparent

log = logging.getLogger("governedgraph.observability")
_logs = None
_lock = Lock()
FIELDS = {
    "event_id", "journey_id", "contact_key", "request_id", "agent_name",
    "model_deployment", "input_tokens", "cached_input_tokens", "output_tokens",
    "state", "arm", "duration_ms", "escalated", "numeric_fidelity_passed",
    "strategy_id", "acknowledgement_ms", "substantive_ms", "complete_audio_ms",
    "usage_complete",
    "window_id", "fcr_uplift_pts", "repeat_reduction_pts", "decision",
    "occurred_at",
    "snapshot_id", "suspended_ids",
}


def monitor_export_options(signal: Literal["traces", "logs"]) -> dict[str, object]:
    return {
        "timeout": 5.0,
        "read_timeout": 5.0,
        "retry_total": 0,
        "disable_offline_storage": False,
        "storage_directory": str(settings.run_dir / "telemetry" / signal),
        "storage_retention_period": 3600,
    }


def configure_events() -> None:
    global _logs
    with _lock:
        if _logs is not None:
            return
        if not settings.appinsights_connection_string:
            raise ValueError("Entra-authenticated Application Insights routing is required")
        from azure.identity import DefaultAzureCredential
        from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        provider = LoggerProvider(resource=Resource.create(
            {"service.name": os.getenv("GOVERNEDGRAPH_SERVICE_NAME", "gg-orchestrator")}))
        provider.add_log_record_processor(BatchLogRecordProcessor(AzureMonitorLogExporter(
            connection_string=settings.appinsights_connection_string,
            credential=DefaultAzureCredential(), **monitor_export_options("logs"),
        )))
        handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
        log.addHandler(handler)
        logging.getLogger("governedgraph.strategy_service").addHandler(handler)
        log.setLevel(logging.INFO)
        _logs = provider


def flush_events() -> None:
    if _logs is not None and not _logs.force_flush(timeout_millis=10000):
        raise RuntimeError("operational event export did not flush")


def contact_key(member_ref: str) -> str:
    return hashlib.sha256(("governedgraph:contact:" + member_ref).encode()).hexdigest()


def emit(name: str, **fields) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name) or set(fields) - FIELDS:
        raise ValueError("invalid content-free telemetry contract")
    if any(not isinstance(value, (str, bool, int, float))
           or isinstance(value, float) and not math.isfinite(value) for value in fields.values()):
        raise ValueError("invalid telemetry scalar")
    if "occurred_at" not in fields:
        fields["occurred_at"] = datetime.now(timezone.utc).isoformat()
    instant = datetime.fromisoformat(fields["occurred_at"])
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("telemetry timestamp requires a timezone")
    log.info(name, extra={"data": "ILLUSTRATIVE - synthetic data", **fields})


def model_usage(response, *, journey_id: str, agent_name: str, model: str) -> None:
    usage = getattr(response, "usage", None)
    details = getattr(usage, "input_tokens_details", None)
    counts = (getattr(usage, "input_tokens", None), getattr(details, "cached_tokens", None),
              getattr(usage, "output_tokens", None))
    complete = (all(type(value) is int and value >= 0 for value in counts)
                and counts[1] <= counts[0])
    fields = {"journey_id": journey_id, "agent_name": agent_name, "model_deployment": model,
              "event_id": f"usage:{getattr(response, 'id', '')}", "usage_complete": complete}
    if complete:
        fields.update(zip(("input_tokens", "cached_input_tokens", "output_tokens"), counts))
    emit("model_usage" if complete else "model_usage_missing", **fields)


def headers(journey_id: str, parent: str = "") -> dict[str, str]:
    from governedgraph.agents.foundry_tracing import journey_trace_id

    candidate = traceparent(parent)
    from opentelemetry import trace

    current = trace.get_current_span().get_span_context()
    if current.is_valid and current.trace_id == journey_trace_id(journey_id):
        candidate = f"00-{current.trace_id:032x}-{current.span_id:016x}-01"
    return {"X-Journey-ID": journey_id,
            "traceparent": f"00-{journey_trace_id(journey_id):032x}-{candidate.split('-')[2]}-01"}


@contextmanager
def operation(journey_id: str, name: str, *, agent_name: str, parent: str = "", server: bool = False):
    from governedgraph.agents.foundry_tracing import configure_tracing, journey_trace_id, _journey
    from opentelemetry import trace
    from opentelemetry.context import Context

    provider = configure_tracing()
    trace_id = journey_trace_id(journey_id)
    current = trace.get_current_span().get_span_context()
    context = None
    if current.trace_id != trace_id:
        context = Context()
        parsed = headers(journey_id, parent)["traceparent"].split("-")
        if parent == "-".join(parsed):
            context = trace.set_span_in_context(trace.NonRecordingSpan(trace.SpanContext(
                trace_id=trace_id, span_id=int(parsed[2], 16), is_remote=True,
                trace_flags=trace.TraceFlags(1),
            )), context)
    marker = _journey.set(journey_id)
    try:
        with provider.get_tracer("governedgraph").start_as_current_span(
            name, context=context, record_exception=False, set_status_on_exception=False,
            kind=trace.SpanKind.SERVER if server else trace.SpanKind.INTERNAL,
            attributes={"journey_id": journey_id, "gen_ai.agent.name": agent_name},
        ) as span:
            yield span
    finally:
        _journey.reset(marker)
