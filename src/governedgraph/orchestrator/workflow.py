"""Agent-first member routing; code owns authorization, transitions and output gates."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from governedgraph.agents.conversation_context import screening_batches
from governedgraph.common.contracts import IntentDecision, UnderstandingDecision
from governedgraph.common.domain import message
from governedgraph.common.workflow import run_sync
from governedgraph.observability import operation
from governedgraph.orchestrator.state_machine import Event, State

if TYPE_CHECKING:
    from governedgraph.agents.counsellor import Counsellor, TurnResult


@dataclass
class MemberTurn:
    utterance: str
    result: TurnResult | None = None
    route: str = "screen"
    decision: IntentDecision | None = None
    steps: list[str] = field(default_factory=list)
    quota_exhausted: bool = False


def _screen_input(c, turn: MemberTurn) -> None:
    from governedgraph.agents.counsellor import dependency

    with c.tools.trace.measure("screening"):
        screen = dependency("content_safety", c.screen.screen, turn.utterance, c.tools.auth_ref)
        if screen.allowed and c.tools.conversation_history:
            for documents in screening_batches(c.tools.conversation_history):
                screen = dependency("content_safety", c.screen.screen, "", c.tools.auth_ref, documents)
                if not screen.allowed:
                    break
    if screen.allowed:
        c.tools.conversation_screened = True
        turn.route = "intent"
    else:
        turn.result = c._escalate(f"input blocked: {screen.reason}", "safety")


def _intent_agent(c, turn: MemberTurn) -> None:
    from governedgraph.agents.counsellor import DependencyFailure

    j = c.journey
    decision = c.decide("intent", turn.utterance)
    if not isinstance(decision, IntentDecision):
        raise DependencyFailure("invalid intent-agent contract")
    c.tools.trace.note("intent", decision.reason)
    turn.decision, turn.route = decision, decision.route
    if decision.route == "comprehension":
        # A restatement is only an acknowledgment of a resolved answer; mid-check it answers the teach-back.
        if j.state == State.RESOLVED:
            turn.route = "acknowledgment"
        elif j.state != State.COMPREHENSION_CHECK:
            turn.route = "clarification"


def _understanding_agent(c, turn: MemberTurn) -> None:
    from governedgraph.agents.counsellor import TEMPLATES, DependencyFailure, TurnResult

    j = c.journey
    decision = c.decide("understanding", turn.utterance)
    if not isinstance(decision, UnderstandingDecision):
        raise DependencyFailure("invalid understanding-agent contract")
    if decision.outcome == "understood":
        strategy = c.catalogue.get(c._current_strategy) if c._current_strategy else None
        j.fire(Event.TEACH_BACK_PASSED, outcome="comprehension_check",
               strategy=(strategy.strategy_id, strategy.version) if strategy else None)
        c.tools.trace.note("outcome", "comprehension_confirmed")
        turn.result = TurnResult(TEMPLATES["acknowledgements"]["comprehension_confirmed"][j.response_language],
                                 j.state.value, c._current_strategy)
    elif decision.outcome == "uncertain":
        turn.route = "clarification"
    else:
        j.fire(Event.TEACH_BACK_FAILED)
        if j.state == State.ESCALATION_REQUESTED:
            turn.result = c._escalate("teach-back failed twice", "teach_back_failed", already_requested=True)
        else:
            c._turn_exclusions = set(j.strategies_tried)
            turn.route = "explanation"


def _explain(c, turn: MemberTurn) -> None:
    j = c.journey
    if turn.decision is not None and turn.decision.route == "explanation":
        j.intent = turn.decision.intent
    if j.state in (State.IDENTIFIED, State.RESOLVED, State.COMPREHENSION_CHECK):
        j.fire(Event.INTENT_IN_SCOPE)
    turn.result = c._explain(turn.utterance, exclude=c._turn_exclusions)


def _acknowledge(c, turn: MemberTurn) -> None:
    from governedgraph.agents.counsellor import TEMPLATES, TurnResult

    c.tools.trace.note("outcome", "conversation_acknowledged")
    turn.result = TurnResult(TEMPLATES["acknowledgements"]["conversation_complete"][c.journey.response_language],
                             c.journey.state.value)


def _clarify(c, turn: MemberTurn) -> None:
    from governedgraph.agents.counsellor import TEMPLATES, TurnResult

    j = c.journey
    c.tools.trace.note("outcome", "follow_up_question", ["clarification_route"])
    if j.state == State.COMPREHENSION_CHECK:
        # An unclear reply to a teach-back is about the last explanation, not a new question.
        turn.result = TurnResult(TEMPLATES["acknowledgements"]["comprehension_retry"][j.response_language],
                                 j.state.value)
    else:
        turn.result = c._clarify(turn.utterance)


def _human_handoff(c, turn: MemberTurn) -> None:
    turn.result = c._escalate("member requested a person", "member_request")


def _advice_handoff(c, turn: MemberTurn) -> None:
    c.journey.intent = "option_level_question"
    turn.result = c._escalate(message("advice_escalation_summary"), "advice_intent")


def _service_failure_handoff(c, turn: MemberTurn) -> None:
    if turn.quota_exhausted:
        turn.result = c._escalate("conversation token quota reached", "conversation_limit")
    else:
        turn.result = c._escalate("decision or required service unavailable", "component_failure")


STEPS = {
    "screen-input": _screen_input, "intent-agent": _intent_agent, "understanding-agent": _understanding_agent,
    "explain-with-approved-tools": _explain, "acknowledge-explanation": _acknowledge, "clarify-question": _clarify,
    "requested-human-handoff": _human_handoff, "personal-advice-handoff": _advice_handoff,
    "service-failure-handoff": _service_failure_handoff,
}


class JourneyStep(Executor):
    def __init__(self, step: str, counsellor: Counsellor, previous_count: int):
        super().__init__(id=step)
        self.counsellor = counsellor
        self.previous_count = previous_count

    @handler
    async def process(self, turn: MemberTurn, ctx: WorkflowContext[MemberTurn, object]) -> None:
        from governedgraph.agents.counsellor import ConversationQuotaExceeded, DependencyFailure
        from governedgraph.guardrails.prompt_safety import ContentSafetyError

        c, j = self.counsellor, self.counsellor.journey
        # Downstream executors must not mutate messages still used by sibling edge conditions.
        turn = replace(turn, steps=[*turn.steps, self.id])
        with operation(j.journey_id, f"governedgraph.workflow.{self.id}", agent_name=c.tools.agent_name,
                       parent=c.tools.trace_parent) as span:
            try:
                if self.id == "commit-checked-evidence":
                    if turn.result is None:
                        raise RuntimeError("workflow cannot commit an absent result")
                    turn.result = c._complete_turn(turn.result, self.previous_count)
                    turn.result.workflow_steps = list(turn.steps)
                    await ctx.yield_output(turn.result)
                    return
                if self.id not in STEPS:
                    raise RuntimeError("unapproved member workflow step")
                STEPS[self.id](c, turn)
            except DependencyFailure as error:
                import logging
                from opentelemetry.trace import Status, StatusCode

                logging.getLogger("governedgraph.workflow").error(
                    "workflow_dependency_failed", extra={"journey_id": j.journey_id, "failure_domain": str(error)})
                if span is not None:
                    span.set_status(Status(StatusCode.ERROR))
                    span.set_attribute("error.type", type(error).__name__)
                turn.route = "technical_failure"
                turn.quota_exhausted = isinstance(error, ConversationQuotaExceeded)
            except ContentSafetyError:
                turn.result = c._escalate("content screening unavailable or blocked", "safety")
        await ctx.send_message(turn)


async def member_turn(counsellor: Counsellor, utterance: str) -> TurnResult:
    previous_count = len(counsellor.journey.events)
    names = (
        "screen-input", "intent-agent", "understanding-agent", "explain-with-approved-tools",
        "acknowledge-explanation", "clarify-question", "requested-human-handoff", "personal-advice-handoff",
        "service-failure-handoff", "commit-checked-evidence",
    )
    steps = {name: JourneyStep(name, counsellor, previous_count) for name in names}
    screen, intent, understanding, explain, acknowledge, clarify, human, advice, failure, commit = steps.values()
    builder = WorkflowBuilder(start_executor=screen, name="governed-member-journey")
    builder.add_edge(screen, intent, condition=lambda turn: turn.result is None and turn.route == "intent")
    for source in (screen, intent, understanding, explain):
        builder.add_edge(source, failure,
                         condition=lambda turn: turn.result is None and turn.route == "technical_failure")
        builder.add_edge(source, commit, condition=lambda turn: turn.result is not None)
    for route, target in (("explanation", explain), ("comprehension", understanding),
                          ("acknowledgment", acknowledge), ("clarification", clarify),
                          ("human", human), ("advice", advice)):
        builder.add_edge(intent, target, condition=lambda turn, route=route: turn.route == route)
    builder.add_edge(understanding, explain,
                     condition=lambda turn: turn.result is None and turn.route == "explanation")
    builder.add_edge(understanding, clarify,
                     condition=lambda turn: turn.result is None and turn.route == "clarification")
    for source in (acknowledge, clarify, human, advice, failure):
        builder.add_edge(source, commit)
    result = await builder.build().run(MemberTurn(utterance))
    outputs = result.get_outputs()
    from governedgraph.agents.counsellor import TurnResult

    if len(outputs) != 1 or not isinstance(outputs[0], TurnResult):
        raise RuntimeError(f"member workflow must produce exactly one checked result; received {len(outputs)}")
    return outputs[0]


def run_member_turn(counsellor: Counsellor, utterance: str) -> TurnResult:
    return run_sync(member_turn(counsellor, utterance))
