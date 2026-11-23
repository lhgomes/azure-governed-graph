"""Counsellor = Member Communication Agent (L3) over the host-owned guardrail pipeline.

Implements the runtime sequence described in docs/architecture.md using a named
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
from dataclasses import asdict, dataclass, field

from governedgraph.agents.clients import default_strategy_client, default_values_client
from governedgraph.agents.tools import CounsellorTools
from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.contracts import (
    AnswerDraft, AnswerReview, ExplanationContext, IntentDecision, UnderstandingDecision, event_timestamp,
)
from governedgraph.common.domain import (
    mentions_own_sources, message, prompt as domain_prompt, without_source_talk)
from governedgraph.common.io import load_json
from governedgraph.explainability.declined_questions import (
    CAPTURED_REASONS, MAX_CHARS, DeclinedQuestion, default_question_store,
)
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
START_GREETINGS = {
    "first_contact": DISCLOSURE,
    "returning": TEMPLATES["disclosure"]["returning"]["en"],
    "second_contact": TEMPLATES["disclosure"]["second_contact"]["en"],
}


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


# Default decline reason for each host escalation code; a call site that knows better passes its own.
ESCALATION_DECLINES = {
    "safety": "content_safety", "teach_back_failed": "teach_back_failed", "advice_intent": "advice_question",
    "member_request": "member_request", "component_failure": "component_failure", "conversation_limit": "conversation_limit",
}


# The approved wording that opens the reply for each host escalation code (the handover text always follows).
ESCALATION_MESSAGES = {
    "advice_intent": "advice_declined", "safety": "safety_declined",
    "component_failure": "component_failure", "conversation_limit": "conversation_limit",
}


def _quality_only(review: AnswerReview) -> bool:
    """Only relevance/style defects remain; every safety check (grounding, advice, document, language) passed."""
    return review.verdict == "revise" and bool(review.issues) and set(review.issues) <= {"relevance", "strategy"}


MAX_CLARIFICATION_CHARS = 300
MAX_PASSAGE_FALLBACK_CHARS = 700


def _clarifying_question_ok(text: str) -> bool:
    """One short question with no figures, placeholders or steering; anything else falls back to approved wording."""
    text = text.strip()
    return (bool(text) and len(text) <= MAX_CLARIFICATION_CHARS and text.endswith("?") and text.count("?") == 1
            and "{{" not in text and "}}" not in text
            and numeric_fidelity.check(text, None, []).passed and not response_steers_choice(text)
            and not mentions_own_sources(text))


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


class _Reply(Exception):
    """Ends the explanation path early with a finished reply."""

    def __init__(self, result: "TurnResult"):
        super().__init__("reply")
        self.result = result


def _judge(c, utterance: str, strategy, draft: AnswerDraft) -> AnswerReview:
    from governedgraph.agents.decision_agents import review_with_decision_model

    review = dependency("answer_review_agent", review_with_decision_model, c.tools, utterance, strategy, draft)
    c.tools.trace.note("answer_review", review.verdict, review.issues)
    return review


def _revise(c, utterance: str, strategy, draft: AnswerDraft, issues: list[str]) -> str:
    """One writer revision; '' when the revised draft does not pass the deterministic gate."""
    revised = dependency("model", c._foundry_composer().revise, c.tools, utterance, strategy, draft, issues)
    _, _, reason = c._post_process(revised, strategy)
    usable = not reason and c.tools.trace.composition_source == "foundry"
    c.tools.trace.note("answer_repair", "revised" if usable else "unusable")
    return revised if usable else ""


def _release_after_failed_review(c, raw_draft: str, draft: AnswerDraft, review: AnswerReview) -> tuple[str, AnswerReview]:
    """What is released when the review is not a pass: the draft itself for quality-only defects (no teach-back is
    claimed), the cited approved passages when grounding is the only safety defect, otherwise the approved fallback."""
    trace = c.tools.trace
    if _quality_only(review):
        log.warning("answer_review_quality_waived", extra={"journey_id": c.journey.journey_id, "issues": review.issues})
        trace.composition_source = "foundry"
        # No teach-back is claimed, so the member is not asked one: the journey does not wait for a restatement.
        if draft.closing_question and draft.text.endswith(draft.closing_question):
            draft = AnswerDraft(draft.text[:-len(draft.closing_question)].rstrip(), draft.content_ids)
            raw_draft = json.dumps(asdict(draft))
        return raw_draft, review
    passages = c._passage_fallback(draft, review)
    if passages:
        trace.composition_source = "foundry"
        return passages, review
    trace.composition_source = "template_fallback"
    return "", review


class Counsellor:
    question_store = None
    _utterance = ""

    def __init__(self, journey: Journey, authenticated_member_ref: str, strategy_service=None,
                 member_token: str = "", step_up_verified: bool = True,
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
            prompt_screen=self.screen,
            step_up_verified=step_up_verified,
        )
        if self.compact_voice:
            self.tools.model_deployment = settings.compact_deployment
            self.tools.agent_name = "gg-counsellor-voice-compact"
        self._current_strategy: str | None = None
        self.step_up_verified = step_up_verified
        self._prefetched = False
        self.question_store = default_question_store()
        self._utterance = ""

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
        with self.tools.trace.measure("understanding_assessment" if role == "understanding" else "intent_routing"):
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
        self._utterance = utterance
        self.tools.response_id = self.tools.response_id or uuid.uuid4().hex
        self.tools.trace.audit_record = None
        self.tools.trace.decision_evidence = []
        self.tools.trace.turn_trace = []
        self.tools.trace.rate_limit_wait = 0.0
        self.tools.trace.decline_reason = ""
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
        try:
            if not self.step_up_verified:
                return self._step_up_reply(utterance)
            self._load_sources(utterance)
            strategy = self._choose_strategy(exclude)
            draft = self._draft(utterance, strategy)
            text, fidelity, last_explanation = self._release(utterance, strategy, draft)
        except _Reply as done:
            return done.result
        return self._deliver(strategy, text, fidelity, last_explanation)

    def _step_up_reply(self, utterance: str) -> TurnResult:
        """Without step-up verification only the approved general passages are shown."""
        trace = self.tools.trace
        try:
            dependency("search", self.tools.search_approved_content, utterance)
        except ContentSafetyError:
            return self._escalate("approved content screening unavailable or blocked", "safety")
        passages = [text for text in trace.content_texts
                    if numeric_fidelity.check(text, None, []).passed and not response_steers_choice(text)]
        text = " ".join(passages) + " Please complete step-up verification to see your personal figures, or ask for a human advisor."
        trace.note("outcome", "step_up_required")
        return TurnResult(text.strip(), self.journey.state.value)

    def _load_sources(self, utterance: str) -> None:
        """Official values, the ranked strategies and the approved passages; a missing source ends the turn."""
        t = self.tools
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
        ranked = t.trace.ranked
        if ranked is None or (ranked.source == "expert_priors_fallback" and not ranked.static_fallback_authorized):
            raise DependencyFailure("strategy snapshot unavailable")
        if ranked.catalogue_version != self.catalogue.version:
            self.catalogue, _ = dependency("strategy_catalogue", t.strategy_client.get_catalogue,
                                           ranked.catalogue_version)
        try:
            if not self._prefetched:
                with t.trace.measure("content_lookup"):
                    dependency("search", t.search_approved_content, utterance)
        except ContentSafetyError:
            raise _Reply(self._escalate("approved content screening unavailable or blocked", "safety"))

    def _choose_strategy(self, exclude: set[str]):
        """The first ranked approved strategy that is not excluded (or the one an admin test selected)."""
        test_strategy = self.journey.test_strategy_id
        ranked = [r for r in self.tools.trace.ranked.ranked if r.strategy_id not in exclude
                  and (not test_strategy or r.strategy_id == test_strategy)]
        if not ranked:
            raise _Reply(self._escalate("selected test style is not currently eligible or available" if test_strategy
                                        else "no eligible approved strategy left",
                                        "teach_back_failed", decline_reason="no_eligible_style"))
        strategy = self.catalogue.get(ranked[0].strategy_id)
        self.tools.explanation_pattern = self._pattern_for(strategy)
        self.tools.template_version = strategy.template_version
        return strategy

    def _draft(self, utterance: str, strategy) -> str:
        """One writer draft, ended early when the model asks for a person or the strategy is no longer served."""
        from governedgraph.agents.foundry_runtime import ToolUnavailable

        trace = self.tools.trace
        escalations = len(trace.escalations)
        try:
            with trace.measure("foundry_draft"):
                draft = dependency("model", self._compose, strategy, utterance)
        except ContentSafetyError:
            raise _Reply(self._escalate("approved content screening unavailable or blocked", "safety"))
        except ToolUnavailable as error:
            raise DependencyFailure("journey-bound component unavailable") from error
        if len(trace.escalations) > escalations:
            raise _Reply(self._escalate("model requested a human advisor", "advice_intent",
                                        decline_reason="model_requested_advisor"))
        if strategy.strategy_id not in {r.strategy_id for r in trace.ranked.ranked}:
            raise DependencyFailure("selected strategy no longer served")
        return draft

    def _release(self, utterance: str, strategy, draft: str):
        """Gate the draft (one repair for a contract breach), review it, gate again: (text, fidelity, taught)."""
        trace = self.tools.trace
        with trace.measure("post_processing"):
            text, fidelity, reason = self._post_process(draft, strategy)
        repaired = reason == "placeholder_contract"
        if repaired:
            log.warning("answer_output_repair", extra={"journey_id": self.journey.journey_id, "failure_domain": reason})
            with trace.measure("foundry_review"):
                draft = dependency("model", self._foundry_composer().revise, self.tools, utterance, strategy,
                                   AnswerDraft.from_dict(json.loads(draft)), [reason])
            with trace.measure("post_processing"):
                text, fidelity, reason = self._post_process(draft, strategy)
            trace.note("answer_repair", "unusable" if reason else "revised")
        if reason:
            raise _Reply(self._unanswered(reason))
        if trace.composition_source != "foundry":
            return text, fidelity, None
        with trace.measure("foundry_review"):
            draft, review = self._review_answer(utterance, strategy, draft, allow_repair=not repaired)
        with trace.measure("post_processing"):
            text, fidelity, reason = self._post_process(draft, strategy)
        if reason:
            raise _Reply(self._unanswered(reason, "answer_review" if review.verdict != "pass" else "output_gate"))
        if review.verdict != "pass" or trace.composition_source != "foundry":
            return text, fidelity, None
        intent = next((item.decision for item in reversed(trace.decision_evidence)
                       if item.role == "intent" and item.decision["route"] == "explanation"), None)
        last = self.journey.last_explanation
        focus = intent["reason"] if intent else (last.question_focus if last else None)
        return text, fidelity, ExplanationContext(review.concepts, review.teach_back_concepts, focus)

    def _deliver(self, strategy, text: str, fidelity, last_explanation) -> TurnResult:
        """Record the answer, advance the journey and build the member-visible result."""
        t = self.tools
        self._current_strategy = strategy.strategy_id
        self.journey.unanswered_streak = 0
        source = t.trace.composition_source
        fallback_goal = source == "template_fallback" and bool(_task_template(t, strategy))
        clarification = fallback_goal and "?" in text
        t.trace.note("outcome", "follow_up_question" if clarification else "answered")
        self.journey.strategies_tried.append(strategy.strategy_id)
        with t.trace.measure("audit_commit"):
            t.write_explainability_record(
                strategy.strategy_id, numeric_fidelity_passed=fidelity.passed,
                response_kind="acknowledgment" if clarification else "explanation")
        reviews = [item for item in t.trace.decision_evidence if item.role == "answer-review"]
        if reviews and reviews[-1].decision["verdict"] != "pass":
            self._keep_declined_question(flagged=True)
        # A task-specific fallback is not a reviewed teach-back.
        teach_back_skipped = source == "foundry" and last_explanation is None
        event = (Event.OUTPUT_DELIVERED if clarification else
                 Event.RESPONSE_DELIVERED if strategy.teach_back and not fallback_goal and not teach_back_skipped
                 else Event.NO_TEACH_BACK)
        self.journey.fire(event, strategy=(strategy.strategy_id, strategy.version))
        self.journey.last_explanation = last_explanation
        ranked = t.trace.ranked
        return TurnResult(text, self.journey.state.value, strategy.strategy_id, fidelity=fidelity,
                          ranked=[x.strategy_id for x in ranked.ranked], suspended=ranked.suspended,
                          arm=ranked.arm, snapshot_id=ranked.snapshot_id)

    def _review_answer(self, utterance: str, strategy, raw_draft: str, *,
                       allow_repair: bool = True) -> tuple[str, AnswerReview]:
        """Judge the draft; a defect the writer can fix gets one revision. Returns the text to release ("" = none)."""
        draft = AnswerDraft.from_dict(json.loads(raw_draft))
        review = _judge(self, utterance, strategy, draft)
        if allow_repair and review.verdict == "revise" and "advice" not in review.issues:
            log.warning("answer_review_repair", extra={
                "journey_id": self.journey.journey_id, "verdict": review.verdict, "issues": review.issues})
            revised = _revise(self, utterance, strategy, draft, review.issues)
            if not revised:
                return _release_after_failed_review(self, raw_draft, draft, review)
            raw_draft, draft = revised, AnswerDraft.from_dict(json.loads(revised))
            review = _judge(self, utterance, strategy, draft)
        if review.verdict == "pass":
            return raw_draft, review
        log.warning("answer_review_nonpass", extra={
            "journey_id": self.journey.journey_id, "verdict": review.verdict, "issues": review.issues})
        return _release_after_failed_review(self, raw_draft, draft, review)

    def _passage_fallback(self, draft: AnswerDraft, review: AnswerReview) -> str:
        """Grounding is the only defect that two drafts could not fix: answer with the approved passages the draft
        cited, word for word. Approved text cannot be unsupported; every deterministic gate still applies to it."""
        if review.verdict != "revise" or "grounding" not in review.issues or not set(review.issues) <= {
                "grounding", "strategy"}:
            return ""
        trace = self.tools.trace
        approved = dict(zip(trace.content_versions, trace.content_texts, strict=True))
        used = [content_id for content_id in dict.fromkeys(draft.content_ids) if content_id in approved][:2]
        text = " ".join(approved[content_id] for content_id in used)
        if (not text or len(text) > MAX_PASSAGE_FALLBACK_CHARS or not numeric_fidelity.check(text, None, []).passed
                or response_steers_choice(text) or mentions_own_sources(text)):
            return ""
        log.warning("answer_passage_fallback", extra={"journey_id": self.journey.journey_id})
        trace.note("answer_review", "passage_fallback")
        return json.dumps({"text": text, "content_ids": used})

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
        """The deterministic output gate: (text, fidelity, None) when released, or ("", failed, reason)."""
        trace = self.tools.trace
        template = _task_template(self.tools, strategy)
        from_model = trace.composition_source == "foundry"
        reason = "numeric-fidelity or steering gate failed"
        if from_model:
            draft = self._grounded_text(draft)
        for index, candidate in enumerate((draft, template)):
            if not candidate:
                continue
            if index == 0 and from_model:
                breach = self._contract_breach(candidate)
                if breach:
                    reason = breach
                    continue
            gated = self._gate(candidate, "template" if index else "draft")
            if gated:
                if index:
                    trace.composition_source = "template_fallback"
                    trace.note("output_gate", "template_fallback")
                    log.warning("output_gate_template_fallback", extra={"journey_id": self.journey.journey_id})
                return gated[0], gated[1], None
        if not template:
            log.warning("output_gate_blocked", extra={
                "journey_id": self.journey.journey_id, "failure_domain": "template:none_approved"})
            trace.note("output_gate", "no_approved_fallback")
        return "", numeric_fidelity.FidelityResult(False, [], ["<blocked>"]), reason

    def _grounded_text(self, draft: str) -> str:
        """The model draft's text when it cites approved content or official values only, otherwise ''."""
        trace = self.tools.trace
        try:
            answer = AnswerDraft.from_dict(json.loads(draft))
            references = set(referenced_value_ids(answer.text))
            available = set(trace.values.by_id()) if trace.values else set()
            if (not references <= available or not set(answer.content_ids) <= set(trace.content_versions)
                    or (not references and not answer.content_ids)):
                raise ValueError("answer has no approved grounding")
            text = without_source_talk(answer.text)
            if not text:
                raise ValueError("answer only talks about its own sources")
            if text != answer.text:
                log.warning("model_source_talk_removed", extra={"journey_id": self.journey.journey_id})
            return text
        except (ValueError, TypeError) as error:
            log.warning("model_grounding_contract_blocked", extra={
                "journey_id": self.journey.journey_id, "error_type": type(error).__name__})
            trace.note("output_gate", "blocked", ["grounding_contract"])
            return ""

    def _contract_breach(self, text: str) -> str | None:
        """What a model draft breaks before binding: a literal figure."""
        unbound = re.sub(r"\{\{(?:value|asof):[^{}]+\}\}", "", text)
        if re.search(r"\d", numeric_fidelity.ORDINAL.sub("", unbound)):
            log.warning("model_placeholder_contract_blocked", extra={"journey_id": self.journey.journey_id})
            self.tools.trace.note("output_gate", "blocked", ["placeholder_contract"])
            self._numeric_block()
            return "placeholder_contract"
        return None

    def _gate(self, candidate: str, source: str):
        """Bind the placeholders and run the numeric and steering gates: (text, fidelity), or None when refused."""
        trace, journey_id = self.tools.trace, self.journey.journey_id

        def refuse(domain: str, issue: str | None = None, *, numeric: bool = True):
            log.warning("output_gate_blocked", extra={"journey_id": journey_id, "failure_domain": f"{source}:{domain}"})
            if issue:
                trace.note("output_gate", "blocked", [issue])
            if numeric:
                self._numeric_block()

        if not numeric_fidelity.check(candidate, None, []).passed:
            log.warning("unbound_figure_blocked", extra={"journey_id": journey_id})
            trace.note("output_gate", "blocked", ["unbound_figure"])
            self._numeric_block()
            return None
        try:
            text = bind(candidate, trace.values)
        except BindingError:
            return refuse("binding")
        if "{{" in text or "}}" in text:
            return refuse("unbound_placeholder")
        fidelity = numeric_fidelity.check(text, trace.values, trace.content_texts)
        if not fidelity.passed:
            return refuse("numeric_fidelity", "numeric_fidelity")
        if response_steers_choice(text):
            return refuse("steers_choice", "steers_choice", numeric=False)
        return text, fidelity

    def _numeric_block(self) -> None:
        from governedgraph.observability import emit

        emit("numeric_fidelity_block", event_id=f"{self.tools.response_id}:numeric-block",
             journey_id=self.journey.journey_id, agent_name=self.tools.agent_name)

    def _keep_declined_question(self, flagged: bool = False) -> None:
        """Best effort, after the audit record: a storage problem here never changes what the member receives.

        ``flagged`` keeps the question of an answer that was delivered after the answer review raised an issue."""
        if (self.question_store is None or self.journey.test_strategy_id or not self._utterance.strip()
                or not (flagged or self.tools.trace.decline_reason in CAPTURED_REASONS)):
            return
        try:
            self.question_store.append(DeclinedQuestion(
                self.journey.journey_id, self.tools.response_id, event_timestamp(), self._utterance[:MAX_CHARS]))
        except Exception as error:
            log.error("declined_question_not_kept", extra={"journey_id": self.journey.journey_id,
                                                           "error_type": type(error).__name__})

    def _clarify(self, utterance: str) -> TurnResult:
        """An unclear request gets one writer-drafted clarifying question; any failure uses the approved wording."""
        text = ""
        try:
            with self.tools.trace.measure("foundry_draft"):
                text = dependency("model", lambda: self._foundry_composer().clarify(self.tools, utterance))
        except ConversationQuotaExceeded:
            raise
        except DependencyFailure:
            log.warning("clarification_draft_unavailable", extra={"journey_id": self.journey.journey_id})
        if not _clarifying_question_ok(text):
            log.warning("clarification_draft_replaced", extra={"journey_id": self.journey.journey_id})
            text = message("clarify_question")
        return TurnResult(text, self.journey.state.value)

    def _unanswered(self, reason: str, decline_reason: str = "output_gate") -> TurnResult:
        """A failed answer is a host failure, not a request for the member to repeat their question."""
        log.warning("answer_unavailable_without_relevant_fallback",
                    extra={"journey_id": self.journey.journey_id, "failure_domain": reason})
        offer = self._template_for(self.catalogue.get(ESCALATION_STRATEGY))
        text = message("answer_unavailable") + offer
        fidelity = numeric_fidelity.check(text, None, [])
        if not offer or not fidelity.passed or response_steers_choice(text) or "{{" in text or "}}" in text:
            return self._escalate("no safe answer or advisor offer available", "safety", decline_reason="no_safe_answer")
        self.journey.unanswered_streak = min(self.journey.unanswered_streak + 1, 100)
        self.tools.trace.note("outcome", "advisor_offer")
        self.tools.trace.composition_source = "unanswered"
        self.tools.trace.decline_reason = decline_reason
        self.tools.write_explainability_record(ESCALATION_STRATEGY, response_kind="acknowledgment")
        self._keep_declined_question()
        self.journey.last_explanation = None
        return TurnResult(text, self.journey.state.value, fidelity=fidelity)

    def _escalate(self, summary: str, reason_code: str, already_requested: bool = False,
                  decline_reason: str = "") -> TurnResult:
        if not already_requested:
            self.journey.fire(Event.ESCALATE, event_id=f"esc-{uuid.uuid4().hex[:6]}")
        self.tools.escalate_to_advisor(reason_code, summary)
        self.tools.trace.decline_reason = decline_reason or ESCALATION_DECLINES.get(reason_code, "")
        self.tools.write_explainability_record(ESCALATION_STRATEGY, response_kind="escalation")
        self._keep_declined_question()
        self.tools.trace.note("outcome", "escalated", [reason_code])
        declined = ESCALATION_MESSAGES.get(reason_code)
        return TurnResult((message(declined) if declined else "") + message("handover_arranged"),
                          self.journey.state.value, ESCALATION_STRATEGY, escalated=True,
                          blocked_reason=summary if reason_code == "safety" else None)


def new_session(member_ref: str, channel: str = "voice", accessibility_mode: str = "standard", strategy_service=None,
                journey_id: str | None = None, member_token: str = "",
                step_up_verified: bool | None = None) -> Counsellor:
    j = Journey(journey_id=journey_id or f"J-{uuid.uuid4().hex[:10]}", member_ref=member_ref, channel=channel,
                accessibility_mode=accessibility_mode)
    return Counsellor(j, authenticated_member_ref=member_ref, strategy_service=strategy_service,
                            member_token=member_token,
                            step_up_verified=False if step_up_verified is None else step_up_verified)
