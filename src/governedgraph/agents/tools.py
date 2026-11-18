"""Counsellor tools (journey-bound; the model never supplies member or journey identity).

Tools are bound to a JourneyContext created by the orchestrator. The model can
NOT choose which member's values to read (member scoping), cannot pass
demographics (the request key comes from journey state), and cannot pick a
strategy outside the ranked response it received (B3).
"""
from __future__ import annotations

import json
import logging
import uuid
from time import perf_counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from governedgraph.common.contracts import (
    DecisionEvidence,
    EscalationRequest,
    ExplainabilityRecord,
    MemberValues,
    StrategyResponse,
)
from governedgraph.common.tracing import traceparent
from governedgraph.agents.conversation_context import ConversationMessage, reference_messages
from governedgraph.observability import operation
from governedgraph.guardrails.prompt_safety import ContentSafetyError
from governedgraph.orchestrator.state_machine import Journey
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY

if TYPE_CHECKING:
    from governedgraph.agents.decision_agents import DecisionContext

log = logging.getLogger("governedgraph.tools")


@dataclass
class Trace:
    values: MemberValues | None = None
    ranked: StrategyResponse | None = None
    content_versions: list[str] = field(default_factory=list)
    content_texts: list[str] = field(default_factory=list)
    escalations: list[EscalationRequest] = field(default_factory=list)
    composition_source: str = "template"
    turn_duration_ms: float = 0.0
    screen_duration_ms: float = 0.0
    audit_record: ExplainabilityRecord | None = None
    stage_ms: dict[str, float] = field(default_factory=dict)
    model_rounds: int = 0
    decision_evidence: list[DecisionEvidence] = field(default_factory=list)
    review_feedback: list[dict[str, str]] = field(default_factory=list)
    turn_trace: list[dict] = field(default_factory=list)
    rate_limit_wait: float = 0.0

    def note(self, stage: str, result: str, issues: list[str] | tuple[str, ...] = ()) -> None:
        """Content-free turn evidence: closed codes only, never member or answer text."""
        if len(self.turn_trace) < 16:
            self.turn_trace.append({"stage": stage, "result": result, "issues": list(issues)})

    @contextmanager
    def measure(self, stage: str):
        started = perf_counter()
        try:
            yield
        finally:
            self.stage_ms[stage] = self.stage_ms.get(stage, 0.0) + (perf_counter() - started) * 1000


class CounsellorTools:
    def __init__(self, journey: Journey, authenticated_member_ref: str, values_client, strategy_client,
                 search, store, model_deployment: str, template_version: str, *,
                 agent_name: str = "local-template-composer", agent_version: str = "1",
                 prompt_screen=None, step_up_verified: bool = True):
        self.journey = journey
        self.auth_ref = authenticated_member_ref
        self.values_client = values_client
        self.strategy_client = strategy_client
        self.search = search
        self.store = store
        self.model_deployment = model_deployment
        self.template_version = template_version
        self.explanation_pattern = ""
        self.agent_name = agent_name
        self.agent_version = agent_version
        self.prompt_screen = prompt_screen
        self.step_up_verified = step_up_verified
        self.trace = Trace()
        self.response_id = ""
        self.trace_parent = traceparent()
        self.related_answer_context: DecisionContext | None = None
        self.conversation_history: tuple[ConversationMessage, ...] = ()
        self.conversation_screened = False

    def conversation_context(self) -> list[dict[str, str]]:
        if self.conversation_history and not self.conversation_screened:
            raise ContentSafetyError("customer conversation has not been screened")
        return reference_messages(self.conversation_history)

    # ---- tool: get_member_values (read-only, B1) ----
    def get_member_values(self) -> str:
        """Return the authenticated member's official values from the administrator's system of record.
        Values are read-only. Quote figures ONLY as placeholders {{value:<value_id>}} - never write digits yourself."""
        if not self.step_up_verified:
            raise PermissionError("step-up authentication required before personal values")
        with operation(self.journey.journey_id, "governedgraph.tool.member_values", agent_name=self.agent_name,
                       parent=self.trace_parent):
            vals = self.values_client.get(self.journey.member_ref, self.auth_ref,
                                      journey_id=self.journey.journey_id, trace_parent=self.trace_parent)
        self.trace.values = vals
        return self.value_references()

    def value_references(self) -> str:
        if self.trace.values is None:
            raise ValueError("official values have not been fetched")
        return json.dumps([{"value_id": v.value_id, "label": v.label, "placeholder": f"{{{{value:{v.value_id}}}}}",
                            "as_of_placeholder": f"{{{{asof:{v.value_id}}}}}", "source_system": v.source_system}
                           for v in self.trace.values.values])

    # ---- tool: get_ranked_strategies (Strategy Service, B3/B6/B7) ----
    def get_ranked_strategies(self) -> str:
        """Return the approved explanation strategies ranked for the current journey state, plus suspended ones.
        Use the rank-1 strategy unless the member failed a teach-back with it."""
        req = {"journey_id": self.journey.journey_id, **self.journey.request_key()}
        return self._ranked_for_request(req)

    def _ranked_for_request(self, req: dict) -> str:
        with operation(self.journey.journey_id, "governedgraph.tool.ranking", agent_name=self.agent_name,
                       parent=self.trace_parent):
            resp = self.strategy_client.get_ranked(req)
        self.trace.ranked = resp
        return json.dumps(resp.to_dict())

    # ---- tool: search_approved_content (governed RAG, B3) ----
    def search_approved_content(self, query: str) -> str:
        """Search the APPROVED, versioned domain content. Answer only from these passages."""
        lang = self.journey.response_language
        with operation(self.journey.journey_id, "governedgraph.tool.approved_content", agent_name=self.agent_name,
                       parent=self.trace_parent):
            hits = self.search.search(query, self.journey.stage, language=lang, top=5)
        if self.prompt_screen is not None:
            with self.trace.measure("document_screening"):
                result = self.prompt_screen.screen("", self.auth_ref, documents=[h.body for h in hits])
            if not result.allowed:
                log.error("approved_content_screen_blocked", extra={"journey_id": self.journey.journey_id,
                                                                   "reason": result.reason})
                raise ContentSafetyError(result.reason)
        self.trace.content_versions.clear()
        self.trace.content_texts.clear()
        for h in hits:
            if h.id not in self.trace.content_versions:
                self.trace.content_versions.append(h.id)
                self.trace.content_texts.append(h.body)
        return json.dumps([asdict(h) for h in hits])

    # ---- tool: escalate_to_advisor (B9, GNA) ----
    def escalate_to_advisor(self, reason_code: str, context_summary: str) -> str:
        """Hand the member over to a licensed human advisor with a context summary.
        Use for any option-level or personal-advice question, when the member asks for a person, or after two failed teach-backs."""
        req = EscalationRequest(
            journey_id=self.journey.journey_id, reason_code=reason_code, context_summary=context_summary[:1000],
            value_ids=[v.value_id for v in self.trace.values.values] if self.trace.values else [],
        )
        self.trace.escalations.append(req)
        return json.dumps({"escalation_id": f"ESC-{uuid.uuid4().hex[:6].upper()}", "status": "offered"})

    # ---- tool: write_explainability_record (append-only) ----
    def write_explainability_record(self, strategy_id: str, numeric_fidelity_passed: bool = True,
                                    disclosure_given: bool = True, response_kind: str = "explanation") -> str:
        """Record which approved strategy, content versions and value sources were used for this answer."""
        ranked = self.trace.ranked
        allowed = {r.strategy_id: r.version for r in ranked.ranked} if ranked else {}
        if strategy_id == ESCALATION_STRATEGY:
            allowed[ESCALATION_STRATEGY] = "1.0"
        if strategy_id not in allowed:
            raise ValueError(f"strategy {strategy_id} is not in the served snapshot (B3) - logged as defect")
        vals = self.trace.values.values if self.trace.values else []
        rec = ExplainabilityRecord(
            journey_id=self.journey.journey_id, member_ref=self.journey.member_ref,
            strategy_id=strategy_id, strategy_version=allowed[strategy_id],
            snapshot_id=ranked.snapshot_id if ranked else "none", arm=ranked.arm if ranked else "n/a",
            content_versions=list(self.trace.content_versions),
            value_ids=[v.value_id for v in vals], value_sources=sorted({f"{v.source_system}:{v.calculation_run_id}" for v in vals}),
            model_deployment=self.model_deployment, template_version=self.template_version,
            agent_name=self.agent_name, agent_version=self.agent_version,
            disclosure_given=disclosure_given, escalation_offered=True,
            request_key=self.journey.request_key(), numeric_fidelity_passed=numeric_fidelity_passed,
            response_id=self.response_id or uuid.uuid4().hex, response_kind=response_kind,
            decision_evidence=list(self.trace.decision_evidence),
        )
        try:
            self.store.append(rec)
        except Exception as error:
            log.error("explainability_record_write_failed",
                      extra={"journey_id": self.journey.journey_id, "error_type": type(error).__name__})
            raise
        self.trace.audit_record = rec
        return json.dumps({"recorded": True, "journey_id": rec.journey_id})

    def as_functions(self):
        return [self.get_member_values, self.get_ranked_strategies, self.search_approved_content,
                self.escalate_to_advisor, self.write_explainability_record]
