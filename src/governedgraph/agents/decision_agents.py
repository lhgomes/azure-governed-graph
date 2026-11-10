"""Versioned hosted semantic agents; closed schemas and pinned Foundry versions."""
from __future__ import annotations

import json
import logging
import re
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from functools import lru_cache

from governedgraph.common import domain
from governedgraph.common.config import settings
from governedgraph.common.contracts import (
    EXPLANATION_CONCEPTS, HISTORICAL_REASONS, INTENT_DECISIONS, RESPONSE_LANGUAGES, REVIEW_ISSUES,
    UNDERSTANDING_DECISIONS, AnswerReview, ContractError, DecisionEvidence,
    ExplanationContext, IntentDecision, ReviewFeedback, UnderstandingDecision, _strict,
)
from governedgraph.observability import headers, model_usage, operation

log = logging.getLogger("governedgraph.decision_agents")
REQUEST_TIMEOUT = 20
REVIEW_CHECKS = {
    "answers_question": "relevance",
    "keeps_only_relevant_detail": "relevance",
    "uses_member_facing_language": "relevance",
    "uses_requested_language": "language",
    "all_claims_supported": "grounding",
    "preserves_uncertainty": "uncertainty",
    "follows_selected_strategy": "strategy",
    "avoids_personal_advice": "advice",
    "respects_document_availability": "document_context",
}
MAX_REVIEW_FEEDBACK = 16
INSTRUCTIONS = {role: domain.prompt(role) for role in ("intent", "understanding", "answer-review")}


def deployment_for(role: str) -> str:
    if role not in INSTRUCTIONS:
        raise ValueError("unknown decision-agent role")
    return settings.compact_deployment if role == "understanding" else settings.model_deployment


def decision_schema(role: str) -> dict:
    if role == "intent":
        names = ("reason",)
        examples = {(row[2],) for row in INTENT_DECISIONS if row[2] not in HISTORICAL_REASONS}
    elif role == "understanding":
        names, examples = ("outcome", "reason"), UNDERSTANDING_DECISIONS
    elif role == "answer-review":
        return {
            "type": "object",
            "properties": {
                "checks": {
                    "type": "object",
                    "properties": {check: {"type": "boolean"} for check in REVIEW_CHECKS},
                    "required": list(REVIEW_CHECKS), "additionalProperties": False,
                },
                "evidence_status": {"type": "string", "enum": ["sufficient", "insufficient"]},
                "feedback": {
                    "type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "issue": {"type": "string", "enum": sorted(REVIEW_ISSUES)},
                            "quote": {"type": "string"},
                        },
                        "required": ["issue", "quote"], "additionalProperties": False,
                    },
                },
                "concepts": {"type": "array", "items": {"type": "string", "enum": sorted(EXPLANATION_CONCEPTS)}},
                "teach_back_concepts": {
                    "type": "array", "items": {"type": "string", "enum": sorted(EXPLANATION_CONCEPTS)},
                },
            },
            "required": ["checks", "evidence_status", "feedback", "concepts", "teach_back_concepts"],
            "additionalProperties": False,
        }
    else:
        raise ValueError("unknown decision-agent role")
    schema = {
        "type": "object",
        "properties": {name: {"type": "string", "enum": sorted({row[i] for row in examples})}
                       for i, name in enumerate(names)},
        "required": list(names), "additionalProperties": False,
    }
    if role == "intent":
        schema["properties"]["response_language"] = {"type": "string", "enum": sorted(RESPONSE_LANGUAGES)}
        schema["required"].append("response_language")
    return schema


def agent_definition(role: str):
    from azure.ai.projects.models import (
        PromptAgentDefinition, PromptAgentDefinitionTextOptions, TextResponseFormatJsonSchema,
    )

    return PromptAgentDefinition(model=deployment_for(role), instructions=INSTRUCTIONS[role],
                                 tools=[], temperature=0.0, text=PromptAgentDefinitionTextOptions(
                                     format=TextResponseFormatJsonSchema(
                                         name=f"{role}_decision", schema=decision_schema(role), strict=True,
                                     ),
                                 ))


@dataclass(frozen=True)
class DecisionContext:
    journey_state: str
    previous_intent: str
    previous_template_id: str | None
    response_language: str = "en"
    last_explanation: ExplanationContext | None = None

    def __post_init__(self):
        if self.journey_state not in {
            "initiated", "identified", "explaining", "comprehension_check", "resolved",
        } or self.previous_intent not in {row[1] for row in INTENT_DECISIONS}:
            raise ContractError("invalid closed agent context")
        if self.previous_template_id not in {None, *(f"T-S{i}" for i in range(1, 7))}:
            raise ContractError("unapproved context template")
        if not isinstance(self.response_language, str) or self.response_language not in RESPONSE_LANGUAGES:
            raise ContractError("unsupported response language")
        if isinstance(self.last_explanation, dict):
            object.__setattr__(self, "last_explanation", ExplanationContext.from_dict(self.last_explanation))
        if self.last_explanation is not None and not isinstance(self.last_explanation, ExplanationContext):
            raise ContractError("invalid closed explanation context")

    @classmethod
    def from_dict(cls, payload: dict) -> "DecisionContext":
        return _strict(cls, payload)


class DecisionUnavailable(RuntimeError):
    pass


class FoundryDecisionAgent:
    def __init__(self, role: str, *, client=None, agent_version: str | None = None):
        if role not in INSTRUCTIONS:
            raise ValueError("unknown decision-agent role")
        self.role = role
        self.agent_name = f"gg-{role}-agent"
        self.agent_version = agent_version if agent_version is not None else {
            "intent": settings.intent_agent_version,
            "understanding": settings.understanding_agent_version,
            "answer-review": settings.review_agent_version,
        }[role]
        if not self.agent_version.isdecimal():
            raise DecisionUnavailable("an explicit decision-agent version is required")
        self.client = client
        if client is None:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential(),
                                      connection_timeout=5, read_timeout=10, retry_total=0)
            version = project.agents.get_version(self.agent_name, self.agent_version)
            if (version.name != self.agent_name or version.version != self.agent_version
                    or version.definition.as_dict() != agent_definition(role).as_dict()):
                raise DecisionUnavailable("pinned decision agent does not match the approved definition")
            self.client = project.get_openai_client(
                max_retries=0, timeout=REQUEST_TIMEOUT,
                **({"base_url": settings.ai_gateway_url} if settings.ai_gateway_url else {}),
            )

    @classmethod
    def shared(cls, role: str) -> "FoundryDecisionAgent":
        from governedgraph.agents.connections import isolated, scoped

        if isolated():
            return scoped(role, lambda: cls(role))
        return cls._cached(role)

    @classmethod
    @lru_cache(maxsize=3)
    def _cached(cls, role: str) -> "FoundryDecisionAgent":
        return cls(role)

    def close(self):
        self.client.close()

    def run(self, tools, utterance: str, context: DecisionContext) -> IntentDecision | UnderstandingDecision:
        if self.role not in {"intent", "understanding"}:
            raise ValueError("answer-review requires grounded judge inputs")
        decision = self._request(tools, {"journey_context": asdict(context), "member_text": utterance})
        if not isinstance(decision, (IntentDecision, UnderstandingDecision)):
            raise ContractError("unexpected routing decision")
        return decision

    def judge(self, tools, utterance: str, strategy, draft) -> AnswerReview:
        from governedgraph.agents.foundry_runtime import task_context
        from governedgraph.guardrails.binding import referenced_value_ids

        tools.trace.review_feedback = []
        cited = set(draft.content_ids)
        references = set(referenced_value_ids(draft.text))
        decision = self._request(tools, {
            "task": task_context(tools, strategy),
            "member_text": re.sub(r"\d+(?:[.,:/% -]\d+)*", "[member-supplied number]", utterance),
            "draft": asdict(draft),
            "official_references": [
                {"value_id": value.value_id, "label": value.label}
                for value in tools.trace.values.values if value.value_id in references
            ],
            "cited_sources": [
                {"id": content_id, "body": body}
                for content_id, body in zip(tools.trace.content_versions, tools.trace.content_texts, strict=True)
                if content_id in cited
            ],
        })
        if not isinstance(decision, AnswerReview):
            raise ContractError("unexpected answer-review decision")
        return decision

    def _request(self, tools, payload: dict) -> IntentDecision | UnderstandingDecision | AnswerReview:
        from azure.core.exceptions import AzureError
        from openai import OpenAIError
        from governedgraph.observability import emit
        from uuid import uuid4

        pending = False
        validation_stage = "request"
        with operation(tools.journey.journey_id, f"invoke_agent {self.agent_name}",
                       agent_name=self.agent_name, parent=tools.trace_parent) as span:
            if span is not None:
                span.set_attributes({"gen_ai.operation.name": "invoke_agent",
                                     "gen_ai.agent.id": f"{self.agent_name}:{self.agent_version}",
                                     "gen_ai.agent.version": self.agent_version,
                                     "gen_ai.request.model": deployment_for(self.role)})
            try:
                pending = True
                measurement = (tools.trace.measure("foundry_requests") if self.role == "answer-review"
                               else nullcontext())
                with measurement:
                    response = self.client.responses.create(
                        input=[{"type": "message", "role": "user", "content": json.dumps(payload)}],
                        store=False, timeout=REQUEST_TIMEOUT,
                        max_output_tokens=600 if self.role == "answer-review" else 300,
                        extra_headers=headers(tools.journey.journey_id, tools.trace_parent),
                        extra_body={"agent_reference": {"type": "agent_reference", "name": self.agent_name,
                                                         "version": self.agent_version}},
                    )
                pending = False
                if self.role == "answer-review":
                    tools.trace.model_rounds += 1
                model_usage(response, journey_id=tools.journey.journey_id,
                            agent_name=self.agent_name, model=deployment_for(self.role))
                if span is not None:
                    span.set_attribute("gen_ai.response.id", response.id)
                validation_stage = "completion"
                if response.status != "completed" or any(
                    item.type == "function_call" for item in response.output
                ):
                    raise ContractError("decision agent did not return a completed decision")
                parser = {"intent": IntentDecision, "understanding": UnderstandingDecision,
                          "answer-review": AnswerReview}[self.role]
                validation_stage = "json"
                body = json.loads(response.output_text)
                if self.role == "intent" and (not isinstance(body, dict) or "response_language" not in body):
                    raise ContractError("intent agent omitted response language")
                if self.role == "intent":
                    if set(body) != {"reason", "response_language"}:
                        raise ContractError("invalid closed intent classification")
                    matches = [row for row in INTENT_DECISIONS if row[2] == body["reason"]
                               and row[2] not in HISTORICAL_REASONS]
                    if len(matches) != 1:
                        raise ContractError("invalid closed intent classification")
                    route, intent, _ = matches[0]
                    body = {"route": route, "intent": intent, **body}
                if self.role == "answer-review":
                    validation_stage = "review_checks"
                    if not isinstance(body, dict):
                        raise ContractError("answer review must be an object")
                    body = dict(body)
                    checks = body.pop("checks", None)
                    if (not isinstance(checks, dict) or set(checks) != set(REVIEW_CHECKS)
                            or any(type(value) is not bool for value in checks.values())):
                        raise ContractError("answer review omitted the complete closed checklist")
                    evidence_status = body.pop("evidence_status", None)
                    if not isinstance(evidence_status, str) or evidence_status not in {"sufficient", "insufficient"}:
                        raise ContractError("invalid closed answer-review evidence status")
                    if "verdict" in body or "issues" in body:
                        raise ContractError("answer review supplied host-derived fields")
                    failed = {REVIEW_CHECKS[check] for check, passed in checks.items() if not passed}
                    if evidence_status == "insufficient":
                        failed.add("grounding")
                    if set(body.get("concepts", [])) & set(payload["task"].get("excluded_concepts", [])):
                        failed.add("relevance")
                    if payload["task"]["selected_strategy"]["teach_back"] and "?" not in payload["draft"]["text"]:
                        failed.add("strategy")
                    validation_stage = "review_consistency"
                    feedback = body.pop("feedback", None)
                    if not isinstance(feedback, list) or len(feedback) > MAX_REVIEW_FEEDBACK:
                        raise ContractError("invalid closed answer-review feedback")
                    validated_feedback = [ReviewFeedback.from_dict(item) for item in feedback]
                    quoted_feedback = [item for item in validated_feedback if item.quote in payload["draft"]["text"]]
                    if len(quoted_feedback) != len(validated_feedback):
                        log.warning("answer_review_quote_discarded", extra={
                            "journey_id": tools.journey.journey_id,
                            "failure_domain": "answer-review:review_quote",
                        })
                    tools.trace.review_feedback = [asdict(item) for item in quoted_feedback]
                    failed.update(item.issue for item in validated_feedback)
                    body["issues"] = sorted(failed)
                    body["verdict"] = ("fail" if "advice" in failed
                                       else "unassessable" if evidence_status == "insufficient"
                                       else "revise" if failed else "pass")
                    if body.get("verdict") in {"revise", "fail", "unassessable"}:
                        if body.get("concepts") or body.get("teach_back_concepts"):
                            log.warning("rejected_answer_concepts_discarded", extra={
                                "journey_id": tools.journey.journey_id,
                                "failure_domain": "answer-review:nonpass_concepts",
                            })
                        body["concepts"], body["teach_back_concepts"] = [], []
                validation_stage = "decision_contract"
                decision = parser.from_dict(body)
                validation_stage = "audit_evidence"
                tools.trace.decision_evidence.append(DecisionEvidence(
                    self.role, self.agent_name, self.agent_version, asdict(decision),
                ))
                if span is not None:
                    span.set_attribute("governedgraph.decision.code", decision.route if isinstance(decision, IntentDecision)
                                       else decision.verdict if isinstance(decision, AnswerReview) else decision.outcome)
                    if isinstance(decision, AnswerReview):
                        span.set_attribute("governedgraph.decision.reason", ",".join(decision.issues) or "accepted")
                    else:
                        span.set_attribute("governedgraph.decision.reason", decision.reason)
                return decision
            except (AzureError, OpenAIError, TimeoutError, ValueError, TypeError, KeyError) as error:
                if pending:
                    emit("model_usage_missing", event_id=f"unknown:{uuid4().hex}",
                         journey_id=tools.journey.journey_id, agent_name=self.agent_name,
                         model_deployment=deployment_for(self.role), usage_complete=False)
                contract_codes = {
                    "invalid closed intent classification": "intent_classification",
                    "decision agent did not return a completed decision": "incomplete",
                    "answer review must be an object": "review_object",
                    "answer review omitted the complete closed checklist": "review_checks",
                    "answer review verdict disagrees with its checks": "review_consistency",
                    "invalid answer-review verdict": "review_verdict",
                    "invalid closed answer-review issues": "review_issues",
                    "invalid closed answer-review evidence status": "review_evidence",
                    "answer review supplied host-derived fields": "review_derived_fields",
                    "invalid closed answer-review feedback": "review_feedback",
                    "invalid closed explanation concepts": "review_concepts",
                    "teach-back cannot assess an untaught concept": "untaught_concept",
                    "only an accepted explanation supplies taught concepts": "nonpass_concepts",
                }
                failure_code = contract_codes.get(str(error), "schema_or_dependency")
                log.error("decision_agent_unavailable", extra={"journey_id": tools.journey.journey_id,
                                                             "agent_name": self.agent_name,
                                                             "error_type": type(error).__name__,
                                                             "failure_domain": (
                                                                 f"{self.role}:{validation_stage}:{failure_code}"),
                                                             "validation_stage": validation_stage,
                                                             "failure_code": failure_code})
                if span is not None:
                    from opentelemetry.trace import Status, StatusCode

                    span.set_status(Status(StatusCode.ERROR))
                    span.set_attribute("error.type", type(error).__name__)
                    span.set_attribute("governedgraph.decision.validation_stage", validation_stage)
                    span.set_attribute("governedgraph.decision.failure_code", failure_code)
                raise DecisionUnavailable(f"{self.role} agent unavailable") from error


def run_decision(role: str, tools, utterance: str,
                 context: DecisionContext) -> IntentDecision | UnderstandingDecision:
    return FoundryDecisionAgent.shared(role).run(tools, utterance, context)
