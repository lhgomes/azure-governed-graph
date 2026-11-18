"""Counsellor = Member Communication Agent (L3) over the host-owned guardrail pipeline.

Implements the runtime sequence described in docs/architecture.md using a pinned
Foundry prompt agent with journey-bound function tools and placeholder-only drafts.
Post-processing: bind figures by reference -> numeric-fidelity gate ->
steering check -> explainability record. A placeholder-contract rejection may use
the single bounded repair before independent review. Otherwise release only an
approved task-specific fallback or an explicit answer-failure acknowledgment with human help.
"""
from __future__ import annotations

import re
import json
import logging
import uuid
import time
import os
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field

from governedgraph.agents.clients import default_strategy_client, default_values_client
from governedgraph.agents.tools import CounsellorTools
from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.contracts import AnswerDraft, AnswerReview, ExplanationContext, IntentDecision, UnderstandingDecision
from governedgraph.common.domain import message, prompt as domain_prompt
from governedgraph.common.io import load_json
from governedgraph.explainability.store import default_store
from governedgraph.guardrails import numeric_fidelity
from governedgraph.guardrails.advice_intent import response_steers_choice
from governedgraph.guardrails.binding import BindingError, bind, referenced_value_ids
from governedgraph.guardrails.prompt_safety import ContentSafetyError
from governedgraph.orchestrator.state_machine import Event, Journey, State
from governedgraph.search.approved_content import default_search
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY, Catalogue

TEMPLATES = load_json(DATA_DIR / "catalogue" / "templates_v1.json")
log = logging.getLogger("governedgraph.counsellor")
DISCLOSURE = TEMPLATES["disclosure"]["en"]


class DependencyFailure(RuntimeError):
    pass


class ConversationQuotaExceeded(DependencyFailure):
    """The gateway refused a model call because this journey spent its daily token quota."""


# Set by the APIM policy on-error handler (infra/policies/model-responses.xml).
QUOTA_HEADER, QUOTA_HEADER_VALUE = "X-GG-Limit", "conversation-token-quota"


def is_token_quota_exceeded(error: BaseException | None) -> bool:
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
        if getattr(error, "status_code", None) == 403 and (
                headers.get(QUOTA_HEADER) == QUOTA_HEADER_VALUE
                or "quota" in str(getattr(error, "message", "")).lower()):
            return True
        error = error.__cause__ or error.__context__
    return False


def dependency(domain, operation, *args):
    try:
        return operation(*args)
    except ContentSafetyError:
        raise
    except Exception as error:
        if is_token_quota_exceeded(error):
            raise ConversationQuotaExceeded(domain) from error
        raise DependencyFailure(domain) from error

INSTRUCTIONS = domain_prompt("counsellor")


def _task_template(tools, strategy) -> str:
    intent = next((item.decision for item in reversed(tools.trace.decision_evidence)
                   if item.role == "intent"), None)
    if intent is not None and strategy.strategy_id != ESCALATION_STRATEGY:
        goal = TEMPLATES.get("answer_goals", {}).get(intent["reason"])
        if goal is not None:
            return goal["text"][tools.journey.response_language]
    return ""


@dataclass
class TurnResult:
    text: str
    state: str
    strategy_id: str | None = None
    escalated: bool = False
    blocked_reason: str | None = None
    fidelity: numeric_fidelity.FidelityResult | None = None
    ranked: list[str] = field(default_factory=list)
    suspended: list[str] = field(default_factory=list)
    arm: str | None = None
    snapshot_id: str | None = None
    audit_status: str | None = None
    stage_ms: dict[str, float] = field(default_factory=dict)
    model_rounds: int = 0
    composition_source: str = "template"
    numeric_fidelity_passed: bool = True
    no_option_recommendation: bool = True
    workflow_steps: list[str] = field(default_factory=list)
    turn_trace: list[dict] = field(default_factory=list)


class Counsellor:
    def __init__(self, journey: Journey, authenticated_member_ref: str, strategy_service=None,
                 composer: str | None = None, member_token: str = "", step_up_verified: bool = True,
                 audit_enqueue=None, audit_store=None, voice_model: str = ""):
        selected_voice_model = voice_model or os.getenv("GOVERNEDGRAPH_VOICE_DRAFT_MODEL", "gpt-counsellor")
        if selected_voice_model not in ("gpt-counsellor", "gpt-compact"):
            raise ValueError("voice model must be an approved pinned candidate")
        self.compact_voice = journey.channel == "voice" and selected_voice_model == "gpt-compact"
        self.journey = journey
        self.catalogue = Catalogue.load()
        from governedgraph.agents.azure_safety import AzurePromptShields

        self.screen = AzurePromptShields(settings.content_safety_endpoint)
        self.screen.journey_id = journey.journey_id
        self.decision_agents = {}
        if audit_store is None:
            audit_store = default_store(journey, audit_enqueue)
        elif audit_enqueue is not None:
            from governedgraph.explainability.store import QueuedExplainabilityStore

            audit_store = QueuedExplainabilityStore(audit_store, audit_enqueue)
        self.tools = CounsellorTools(
            journey, authenticated_member_ref, default_values_client(member_token), default_strategy_client(strategy_service),
            default_search(), audit_store,
            model_deployment=settings.model_deployment,
            template_version=TEMPLATES["template_set_version"],
            agent_name=settings.agent_name,
            agent_version=settings.agent_version,
            prompt_screen=self.screen,
            step_up_verified=step_up_verified,
        )
        if composer not in (None, "foundry"):
            raise ValueError("only the hosted Foundry composer is supported")
        self.composer = "foundry"
        if self.compact_voice:
            self.tools.model_deployment = settings.compact_deployment
            self.tools.agent_name = "gg-counsellor-voice-compact"
            self.tools.agent_version = os.getenv("AZURE_AI_VOICE_COMPACT_VERSION", "")
        self._current_strategy: str | None = None
        self.step_up_verified = step_up_verified
        self._prefetched = False

    def _parallel_explanation_sources(self, utterance: str):
        """Parallel read-only tools, only after screening and the intent-agent decision."""
        t, j = self.tools, self.journey

        def measured(stage, domain, operation, *args):
            with t.trace.measure(stage):
                return dependency(domain, operation, *args)

        with ThreadPoolExecutor(max_workers=3) as pool:
            def submit(*args):
                return pool.submit(copy_context().run, measured, *args)

            reads = [
                submit("values_lookup", "member_values", t.get_member_values),
                submit("strategy_lookup", "strategy_service", t.get_ranked_strategies),
                submit("content_lookup", "search", t.search_approved_content,
                       utterance),
            ]
            errors = []
            for future in reads:
                try:
                    future.result()
                except (DependencyFailure, ContentSafetyError) as error:
                    errors.append(error)
                    log.error("explanation_source_failed", extra={"journey_id": j.journey_id,
                                                                 "error_type": type(error).__name__})
            if errors:
                raise errors[0]
            self._prefetched = True

    def decide(self, role: str, utterance: str) -> IntentDecision | UnderstandingDecision:
        from governedgraph.agents.decision_agents import DecisionContext, run_decision

        selected = self.tools.related_answer_context if role == "intent" else None
        context = DecisionContext(
            self.journey.state.value, selected.previous_intent if selected else self.journey.intent,
            (selected.previous_template_id if selected else
             self.catalogue.get(self._current_strategy).template_id if self._current_strategy else None),
            self.journey.response_language, selected.last_explanation if selected else self.journey.last_explanation,
        )
        agent = self.decision_agents.get(role)
        operation = agent.run if agent is not None else lambda *args: run_decision(role, *args)
        with self.tools.trace.measure("intent_routing" if role == "intent" else "understanding_assessment"):
            return dependency(f"{role}_agent", operation, self.tools, utterance, context)

    # ---- session start: Art. 50 disclosure + authentication ----
    def start(self) -> str:
        self.journey.fire(Event.DISCLOSED_AND_AUTHENTICATED)
        return DISCLOSURE

    # ---- one member turn ----
    def handle(self, utterance: str) -> TurnResult:
        from governedgraph.agents.foundry_tracing import journey_span
        from governedgraph.agents.azure_safety import AzurePromptShields

        if not isinstance(self.screen, AzurePromptShields):
            raise RuntimeError("hosted runtime requires fail-closed Prompt Shields")
        started = time.perf_counter()
        first_screen = self.screen.total_duration_ms
        with journey_span(self.tools) as span:
            try:
                return self._safe_handle(utterance)
            finally:
                trace = self.tools.trace
                trace.turn_duration_ms = (time.perf_counter() - started) * 1000
                trace.screen_duration_ms = self.screen.total_duration_ms - first_screen
                span.set_attributes({"governedgraph.turn.duration_ms": trace.turn_duration_ms,
                                     "governedgraph.prompt_shields.duration_ms": trace.screen_duration_ms})
                if self.journey.channel == "voice":
                    span.set_attribute("governedgraph.voice_turn.slo_met", trace.turn_duration_ms < 2000)
                    span.set_attribute("governedgraph.voice_turn.measurement", "guarded_processing_not_audio_slo")

    def _safe_handle(self, utterance: str) -> TurnResult:
        from governedgraph.explainability.store import AuditUnavailable

        previous_count = len(self.journey.events)
        self.tools.response_id = self.tools.response_id or uuid.uuid4().hex
        self.tools.trace.audit_record = None
        self.tools.trace.decision_evidence = []
        self.tools.trace.turn_trace = []
        self.tools.trace.rate_limit_wait = 0.0
        self.tools.conversation_screened = False
        self._turn_exclusions: set[str] = set()
        self._prefetched = False
        try:
            try:
                result = self._handle(utterance)
            except DependencyFailure as error:
                log.error("counsellor_dependency_failed", extra={"journey_id": self.journey.journey_id,
                                                                "failure_domain": str(error)})
                if isinstance(error, ConversationQuotaExceeded):
                    result = self._escalate("conversation token quota reached", "conversation_limit")
                else:
                    result = self._escalate("dependent service unavailable", "component_failure")
                return self._complete_turn(result, previous_count)
            except ContentSafetyError:
                result = self._escalate("content screening unavailable or blocked", "safety")
                return self._complete_turn(result, previous_count)
            return result
        except AuditUnavailable:
            log.error("generative_responses_paused", extra={"journey_id": self.journey.journey_id})
            if self.journey.state != State.ESCALATION_REQUESTED:
                self.journey.fire(Event.ESCALATE)
            if not self.tools.trace.escalations:
                self.tools.escalate_to_advisor("component_failure", "Audit service unavailable.")
            self.journey.withhold_response()
            return TurnResult(message("audit_unavailable"),
                              self.journey.state.value, ESCALATION_STRATEGY,
                              escalated=True, audit_status="unavailable")
        finally:
            self.tools.response_id = ""
            self.tools.related_answer_context = None

    def _handle(self, utterance: str) -> TurnResult:
        from governedgraph.orchestrator.workflow import run_member_turn

        return run_member_turn(self, utterance)

    def _complete_turn(self, result: TurnResult, previous_count: int) -> TurnResult:
        if self.tools.trace.audit_record is None:
            ranked = self.tools.trace.ranked
            served = {item.strategy_id for item in ranked.ranked} if ranked else set()
            current = self._current_strategy
            context_strategy = current if current is not None and current in served else ESCALATION_STRATEGY
            self.tools.write_explainability_record(context_strategy, response_kind="acknowledgment")
        self.journey.record_response(self.tools.response_id, previous_count)
        result.turn_trace = list(self.tools.trace.turn_trace)
        result.audit_status = getattr(self.tools.store, "last_status", "committed")
        result.stage_ms = dict(self.tools.trace.stage_ms)
        result.model_rounds = self.tools.trace.model_rounds
        result.composition_source = self.tools.trace.composition_source
        result.numeric_fidelity_passed = numeric_fidelity.check(
            result.text, self.tools.trace.values, self.tools.trace.content_texts).passed
        result.no_option_recommendation = not response_steers_choice(result.text)
        return result

    # ---- explanation path ----
    def _explain(self, utterance: str, exclude: set[str]) -> TurnResult:
        t = self.tools
        if not self.step_up_verified:
            try:
                dependency("search", t.search_approved_content, utterance)
            except ContentSafetyError:
                return self._escalate("approved content screening unavailable or blocked", "safety")
            passages = [text for text in t.trace.content_texts
                        if numeric_fidelity.check(text, None, []).passed and not response_steers_choice(text)]
            text = " ".join(passages)
            text += " Please complete step-up verification to see your personal figures, or ask for a human advisor."
            t.trace.note("outcome", "step_up_required")
            return TurnResult(text.strip(), self.journey.state.value)
        if self.journey.channel == "voice":
            self._parallel_explanation_sources(utterance)
        try:
            if not self._prefetched:
                with t.trace.measure("values_lookup"):
                    t.get_member_values()
        except Exception as error:
            log.error("member_values_unavailable", extra={"journey_id": self.journey.journey_id,
                                                         "error_type": type(error).__name__})
            # circuit open / admin API down: explain generally, never estimate
            raise DependencyFailure("administrator values unavailable") from error
        if not self._prefetched:
            with t.trace.measure("strategy_lookup"):
                dependency("strategy_service", t.get_ranked_strategies)
        if (t.trace.ranked is None or (t.trace.ranked.source == "expert_priors_fallback"
                and not t.trace.ranked.static_fallback_authorized)):
            raise DependencyFailure("strategy snapshot unavailable")
        if t.trace.ranked.catalogue_version != self.catalogue.version:
            self.catalogue, _ = dependency("strategy_catalogue", t.strategy_client.get_catalogue,
                                           t.trace.ranked.catalogue_version)
        try:
            if not self._prefetched:
                with t.trace.measure("content_lookup"):
                    dependency("search", t.search_approved_content, utterance)
        except ContentSafetyError:
            return self._escalate("approved content screening unavailable or blocked", "safety")
        ranked = [r for r in t.trace.ranked.ranked if r.strategy_id not in exclude]
        if self.journey.test_strategy_id:
            ranked = [r for r in ranked if r.strategy_id == self.journey.test_strategy_id]
        if not ranked:
            return self._escalate("selected test style is not currently eligible or available"
                                  if self.journey.test_strategy_id else "no eligible approved strategy left",
                                  "teach_back_failed")
        strategy = self.catalogue.get(ranked[0].strategy_id)
        t.explanation_pattern = self._pattern_for(strategy)
        t.template_version = strategy.template_version

        escalation_count = len(t.trace.escalations)
        from governedgraph.agents.foundry_runtime import ToolUnavailable

        try:
            with t.trace.measure("foundry_draft"):
                draft = dependency("model", self._compose, strategy, utterance)
        except ContentSafetyError:
            return self._escalate("approved content screening unavailable or blocked", "safety")
        except ToolUnavailable as error:
            raise DependencyFailure("journey-bound component unavailable") from error
        if len(t.trace.escalations) > escalation_count:
            return self._escalate("model requested a human advisor", "advice_intent")
        if strategy.strategy_id not in {r.strategy_id for r in t.trace.ranked.ranked}:
            raise DependencyFailure("selected strategy no longer served")
        with t.trace.measure("post_processing"):
            text, fid, reason = self._post_process(draft, strategy)
        repaired = reason == "placeholder_contract"
        if repaired:
            log.warning("answer_output_repair", extra={
                "journey_id": self.journey.journey_id, "failure_domain": reason,
            })
            with t.trace.measure("foundry_review"):
                draft = dependency("model", self._foundry_composer().revise, t, utterance, strategy,
                                   AnswerDraft.from_dict(json.loads(draft)), ["placeholder_contract"])
            with t.trace.measure("post_processing"):
                text, fid, reason = self._post_process(draft, strategy)
            t.trace.note("answer_repair", "unusable" if reason else "revised")
        if reason:
            return self._unanswered(reason)

        last_explanation = None
        if t.trace.composition_source == "foundry":
            with t.trace.measure("foundry_review"):
                draft, review = self._review_answer(utterance, strategy, draft, allow_repair=not repaired)
            with t.trace.measure("post_processing"):
                text, fid, reason = self._post_process(draft, strategy)
            if reason:
                return self._unanswered(reason)
            intent = next((item.decision for item in reversed(t.trace.decision_evidence)
                           if item.role == "intent" and item.decision["route"] == "explanation"), None)
            focus = intent["reason"] if intent else (
                self.journey.last_explanation.question_focus if self.journey.last_explanation else None)
            last_explanation = (
                ExplanationContext(review.concepts, review.teach_back_concepts, focus)
                if review.verdict == "pass" and t.trace.composition_source == "foundry" else None
            )

        self._current_strategy = strategy.strategy_id
        self.journey.unanswered_streak = 0
        t.trace.note("outcome", "answered")
        self.journey.strategies_tried.append(strategy.strategy_id)
        with t.trace.measure("audit_commit"):
            t.write_explainability_record(strategy.strategy_id, numeric_fidelity_passed=fid.passed)
        # A task-specific fallback is not a reviewed teach-back.
        fallback_goal = t.trace.composition_source == "template_fallback" and bool(_task_template(t, strategy))
        ev = Event.RESPONSE_DELIVERED if strategy.teach_back and not fallback_goal else Event.NO_TEACH_BACK
        self.journey.fire(ev, strategy=(strategy.strategy_id, strategy.version))
        self.journey.last_explanation = last_explanation
        r = t.trace.ranked
        return TurnResult(text, self.journey.state.value, strategy.strategy_id, fidelity=fid,
                          ranked=[x.strategy_id for x in r.ranked], suspended=r.suspended, arm=r.arm, snapshot_id=r.snapshot_id)

    def _review_answer(self, utterance: str, strategy, raw_draft: str, *,
                       allow_repair: bool = True) -> tuple[str, AnswerReview]:
        from governedgraph.agents.decision_agents import review_with_decision_model

        judge = self.decision_agents.get("answer-review")
        review_draft = judge.judge if judge is not None else review_with_decision_model
        draft = AnswerDraft.from_dict(json.loads(raw_draft))
        review = dependency("answer_review_agent", review_draft, self.tools, utterance, strategy, draft)
        self.tools.trace.note("answer_review", review.verdict, review.issues)
        if allow_repair and review.verdict == "revise" and "advice" not in review.issues:
            log.warning("answer_review_repair", extra={
                "journey_id": self.journey.journey_id, "verdict": review.verdict, "issues": review.issues,
            })
            revised = dependency("model", self._foundry_composer().revise,
                                 self.tools, utterance, strategy, draft, review.issues)
            _, _, reason = self._post_process(revised, strategy)
            if reason or self.tools.trace.composition_source != "foundry":
                self.tools.trace.note("answer_repair", "unusable")
                return "", review
            self.tools.trace.note("answer_repair", "revised")
            draft = AnswerDraft.from_dict(json.loads(revised))
            raw_draft = revised
            review = dependency("answer_review_agent", review_draft, self.tools, utterance, strategy, draft)
            self.tools.trace.note("answer_review", review.verdict, review.issues)
        if review.verdict != "pass":
            log.warning("answer_review_nonpass", extra={
                "journey_id": self.journey.journey_id, "verdict": review.verdict, "issues": review.issues,
            })
            self.tools.trace.composition_source = "template_fallback"
            return "", review
        return raw_draft, review

    def _template_for(self, strategy) -> str:
        """Approved task-specific wording, otherwise a variant with available official values."""
        task_template = _task_template(self.tools, strategy)
        if task_template:
            return task_template
        return self._pattern_for(strategy)

    def _pattern_for(self, strategy) -> str:
        have = set(self.tools.trace.values.by_id()) if self.tools.trace.values else set()
        for variant in TEMPLATES["templates"][strategy.template_id].values():
            if set(referenced_value_ids(variant)) <= have:
                return variant
        return ""

    def _compose(self, strategy, utterance: str) -> str:
        return self._foundry_composer().draft(self.tools, utterance, strategy)

    def _foundry_composer(self):
        from governedgraph.agents.foundry_runtime import FoundryComposer

        return (FoundryComposer.compact_voice(INSTRUCTIONS) if self.compact_voice
                else FoundryComposer.shared(INSTRUCTIONS))

    def _post_process(self, draft: str, strategy) -> tuple[str, numeric_fidelity.FidelityResult, str | None]:
        from governedgraph.observability import emit

        def numeric_block():
            emit("numeric_fidelity_block", event_id=f"{self.tools.response_id}:numeric-block",
                 journey_id=self.journey.journey_id, agent_name=self.tools.agent_name)

        template = _task_template(self.tools, strategy)
        approved = self.tools.trace.content_texts
        failure_reason = "numeric-fidelity or steering gate failed"
        if self.tools.trace.composition_source == "foundry":
            try:
                answer = AnswerDraft.from_dict(json.loads(draft))
                references = set(referenced_value_ids(answer.text))
                available = set(self.tools.trace.values.by_id()) if self.tools.trace.values else set()
                if (not references <= available
                        or not set(answer.content_ids) <= set(self.tools.trace.content_versions)
                        or (not references and not answer.content_ids)):
                    raise ValueError("answer has no approved grounding")
                draft = answer.text
            except (ValueError, TypeError) as error:
                log.warning("model_grounding_contract_blocked", extra={
                    "journey_id": self.journey.journey_id, "error_type": type(error).__name__,
                })
                self.tools.trace.note("output_gate", "blocked", ["grounding_contract"])
                draft = ""
        for index, candidate in enumerate((draft, template)):
            if not candidate:
                continue
            if index == 0 and self.tools.trace.composition_source == "foundry":
                unbound = re.sub(r"\{\{(?:value|asof):[^{}]+\}\}", "", candidate)
                if re.search(r"\d", numeric_fidelity.ORDINAL.sub("", unbound)):
                    failure_reason = "placeholder_contract"
                    log.warning("model_placeholder_contract_blocked", extra={"journey_id": self.journey.journey_id})
                    self.tools.trace.note("output_gate", "blocked", ["placeholder_contract"])
                    numeric_block()
                    continue
            if not numeric_fidelity.check(candidate, None, []).passed:
                log.warning("unbound_figure_blocked", extra={"journey_id": self.journey.journey_id})
                self.tools.trace.note("output_gate", "blocked", ["unbound_figure"])
                numeric_block()
                continue
            try:
                text = bind(candidate, self.tools.trace.values)
            except BindingError:
                numeric_block()
                continue
            if "{{" in text or "}}" in text:
                numeric_block()
                continue
            fid = numeric_fidelity.check(text, self.tools.trace.values, approved)
            if not fid.passed:
                self.tools.trace.note("output_gate", "blocked", ["numeric_fidelity"])
                numeric_block()
            if fid.passed and not response_steers_choice(text):
                if index:
                    self.tools.trace.composition_source = "template_fallback"
                    self.tools.trace.note("output_gate", "template_fallback")
                    log.warning("output_gate_template_fallback", extra={"journey_id": self.journey.journey_id})
                return text, fid, None
            if fid.passed:
                self.tools.trace.note("output_gate", "blocked", ["steers_choice"])
        if not template:
            self.tools.trace.note("output_gate", "no_approved_fallback")
        return "", numeric_fidelity.FidelityResult(False, [], ["<blocked>"]), failure_reason

    def _unanswered(self, reason: str) -> TurnResult:
        """A failed answer is a host failure, not a request for the member to repeat their question."""
        log.warning("answer_unavailable_without_relevant_fallback",
                    extra={"journey_id": self.journey.journey_id, "failure_domain": reason})
        offer = self._template_for(self.catalogue.get(ESCALATION_STRATEGY))
        text = message("answer_unavailable") + offer
        fidelity = numeric_fidelity.check(text, None, [])
        if not offer or not fidelity.passed or response_steers_choice(text) or "{{" in text or "}}" in text:
            return self._escalate("no safe answer or advisor offer available", "safety")
        self.journey.unanswered_streak = min(self.journey.unanswered_streak + 1, 100)
        self.tools.trace.note("outcome", "advisor_offer")
        self.tools.trace.composition_source = "unanswered"
        self.tools.write_explainability_record(ESCALATION_STRATEGY, response_kind="acknowledgment")
        self.journey.last_explanation = None
        return TurnResult(text, self.journey.state.value, fidelity=fidelity)

    def _escalate(self, summary: str, reason_code: str, already_requested: bool = False) -> TurnResult:
        if not already_requested:
            self.journey.fire(Event.ESCALATE, event_id=f"esc-{uuid.uuid4().hex[:6]}")
        self.tools.escalate_to_advisor(reason_code, summary)
        self.tools.write_explainability_record(ESCALATION_STRATEGY, response_kind="escalation")
        self.tools.trace.note("outcome", "escalated", [reason_code])
        msg = message("advice_declined") if reason_code == "advice_intent" else ""
        if reason_code == "safety":
            msg = message("safety_declined")
        elif reason_code == "component_failure":
            msg = message("component_failure")
        elif reason_code == "conversation_limit":
            msg = message("conversation_limit")
        return TurnResult(msg + message("handover_arranged"),
                          self.journey.state.value, ESCALATION_STRATEGY, escalated=True, blocked_reason=None if reason_code != "safety" else summary)


def new_session(member_ref: str, channel: str = "voice", accessibility_mode: str = "standard", strategy_service=None,
                journey_id: str | None = None, member_token: str = "",
                step_up_verified: bool | None = None) -> Counsellor:
    j = Journey(journey_id=journey_id or f"J-{uuid.uuid4().hex[:10]}", member_ref=member_ref, channel=channel,
                accessibility_mode=accessibility_mode)
    return Counsellor(j, authenticated_member_ref=member_ref, strategy_service=strategy_service,
                            member_token=member_token,
                            step_up_verified=False if step_up_verified is None else step_up_verified)
