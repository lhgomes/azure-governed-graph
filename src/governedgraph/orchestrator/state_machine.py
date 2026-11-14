"""Member Journey Orchestrator state machine.

Owns journey state; agents are stateless and receive the state with each
request. Rules: every transition emits a JourneyEvent; transitions are
idempotent (event_id); ESCALATION IS REACHABLE FROM EVERY STATE (B9); the
orchestrator holds no member values, only references.

Azure: hosted as Durable Functions / Container Apps with Service Bus as the
event backbone (prompt 06). This class is the pure domain core.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from uuid import uuid4

from governedgraph.common.contracts import (
    ContractError, ExplanationContext, JourneyEvent, OutcomeSubmission, RequestKey,
    ResponseReference, RESPONSE_LANGUAGES, _strict, event_timestamp,
)


class State(str, Enum):
    INITIATED = "initiated"
    IDENTIFIED = "identified"
    EXPLAINING = "explaining"
    COMPREHENSION_CHECK = "comprehension_check"
    RESOLVED = "resolved"
    ESCALATION_REQUESTED = "escalation_requested"
    HANDED_OVER = "handed_over"
    CLOSED = "closed"


class Event(str, Enum):
    DISCLOSED_AND_AUTHENTICATED = "disclosed_and_authenticated"
    INTENT_IN_SCOPE = "intent_in_scope"
    INTENT_ADVICE = "intent_advice"
    RESPONSE_DELIVERED = "response_delivered"
    TEACH_BACK_PASSED = "teach_back_passed"
    TEACH_BACK_FAILED = "teach_back_failed"
    NO_TEACH_BACK = "no_teach_back"
    ESCALATE = "escalate"  # member asks for a human, safety trigger, component failure
    ADVISOR_ACCEPTED = "advisor_accepted"
    OPEN_ITEMS_CLOSED = "open_items_closed"
    OUTPUT_DELIVERED = "output_delivered"
    OUTCOME_REPORTED = "outcome_reported"
    PLAYBACK_CONFIRMED = "playback_confirmed"
    CONTACT_LINKED = "contact_linked"


TRANSITIONS: dict[tuple[State, Event], State] = {
    (State.INITIATED, Event.DISCLOSED_AND_AUTHENTICATED): State.IDENTIFIED,
    (State.IDENTIFIED, Event.INTENT_IN_SCOPE): State.EXPLAINING,
    (State.RESOLVED, Event.INTENT_IN_SCOPE): State.EXPLAINING,
    (State.COMPREHENSION_CHECK, Event.INTENT_IN_SCOPE): State.EXPLAINING,
    (State.IDENTIFIED, Event.INTENT_ADVICE): State.ESCALATION_REQUESTED,
    (State.EXPLAINING, Event.RESPONSE_DELIVERED): State.COMPREHENSION_CHECK,
    (State.EXPLAINING, Event.NO_TEACH_BACK): State.RESOLVED,
    (State.COMPREHENSION_CHECK, Event.TEACH_BACK_PASSED): State.RESOLVED,
    (State.ESCALATION_REQUESTED, Event.ADVISOR_ACCEPTED): State.HANDED_OVER,
    (State.RESOLVED, Event.OPEN_ITEMS_CLOSED): State.CLOSED,
    (State.HANDED_OVER, Event.OPEN_ITEMS_CLOSED): State.CLOSED,
}


class InvalidTransition(Exception):
    pass


@dataclass
class Journey:
    journey_id: str
    member_ref: str  # pseudonymous reference only
    intent: str = "unclassified"
    stage: str = "first_contact"
    channel: str = "voice"
    accessibility_mode: str = "standard"
    state: State = State.INITIATED
    teach_back_failures: int = 0
    strategies_tried: list[str] = field(default_factory=list)
    escalation_status: str = "none"
    events: list[JourneyEvent] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)
    response_language: str = "en"
    last_explanation: ExplanationContext | None = None
    test_strategy_id: str = ""
    unanswered_streak: int = 0

    def __post_init__(self):
        if type(self.unanswered_streak) is not int or not 0 <= self.unanswered_streak <= 100:
            raise ContractError("invalid unanswered-turn counter")
        if (not isinstance(self.test_strategy_id, str) or len(self.test_strategy_id) > 100
                or self.test_strategy_id and not self.test_strategy_id.isascii()
                or any(not (char.isalnum() or char in "_-") for char in self.test_strategy_id)):
            raise ContractError("invalid test strategy identifier")
        if not isinstance(self.response_language, str) or self.response_language not in RESPONSE_LANGUAGES:
            raise ContractError("unsupported response language")
        if isinstance(self.last_explanation, dict):
            self.last_explanation = ExplanationContext.from_dict(self.last_explanation)
        if self.last_explanation is not None and not isinstance(self.last_explanation, ExplanationContext):
            raise ContractError("invalid closed previous explanation")

    def request_key(self) -> dict[str, str]:
        return {"journey_state": self.stage, "intent": self.intent, "channel": self.channel,
                "accessibility_mode": self.accessibility_mode}

    def to_dict(self) -> dict:
        result = asdict(self)
        result["state"] = self.state.value
        result["_seen"] = sorted(self._seen)
        if not self.test_strategy_id:
            result.pop("test_strategy_id")
        if not self.unanswered_streak:
            result.pop("unanswered_streak")
        return result

    @classmethod
    def from_dict(cls, payload: dict) -> "Journey":
        result = _strict(cls, payload)
        result.state = State(result.state)
        result.events = [JourneyEvent.from_dict(event) for event in result.events]
        result._seen = set(result._seen)
        RequestKey.from_dict(result.request_key())
        return result

    def fire(self, event: Event, event_id: str | None = None, strategy: tuple[str, str] | None = None,
             outcome: str | None = None) -> State:
        if event_id and event_id in self._seen:
            return self.state  # idempotent retry
        prev = self.state
        if event == Event.OUTPUT_DELIVERED:
            nxt = prev
        elif event == Event.ESCALATE:
            if self.state in (State.CLOSED,):
                raise InvalidTransition("journey closed")
            nxt = State.ESCALATION_REQUESTED
            self.escalation_status = "requested"
        elif event == Event.TEACH_BACK_FAILED and self.state == State.COMPREHENSION_CHECK:
            self.teach_back_failures += 1
            nxt = State.ESCALATION_REQUESTED if self.teach_back_failures >= 2 else State.EXPLAINING
            if nxt == State.ESCALATION_REQUESTED:
                self.escalation_status = "requested"
        else:
            nxt = TRANSITIONS.get((self.state, event))
            if nxt is None:
                raise InvalidTransition(f"{self.state.value} --{event.value}--> ?")
            if nxt == State.HANDED_OVER:
                self.escalation_status = "handed_over"
            elif nxt == State.ESCALATION_REQUESTED:
                self.escalation_status = "requested"
        self.state = nxt
        if event_id:
            self._seen.add(event_id)
        self.events.append(JourneyEvent(
            journey_id=self.journey_id, from_state=prev.value, to_state=nxt.value, event=event.value,
            strategy_id=strategy[0] if strategy else None, strategy_version=strategy[1] if strategy else None,
            outcome=outcome,
            sequence=len(self.events) + 1, event_id=event_id or uuid4().hex,
        ))
        return nxt

    def record_response(self, response_id: str, previous_count: int) -> None:
        if not response_id:
            raise ValueError("delivered responses require an audit identifier")
        if len(self.events) == previous_count:
            self.fire(Event.OUTPUT_DELIVERED, event_id=response_id)
        self.events[-1] = replace(self.events[-1], response_id=response_id, created_at=event_timestamp())

    def withhold_response(self) -> None:
        self.events[-1] = replace(self.events[-1], response_id=None, response_withheld=True,
                                  outcome_offered=False, outcome_version=None)

    def offer_outcome(self, response_id: str, version: str) -> None:
        matches = [index for index, event in enumerate(self.events)
                   if event.response_id == response_id and not event.response_withheld]
        if len(matches) != 1:
            raise ContractError("outcome offer requires exactly one delivered answer")
        index = matches[0]
        self.events[index] = replace(self.events[index], outcome_offered=True, outcome_version=version)

    def record_outcome(self, submission: OutcomeSubmission, event_id: str, *, received_at: str | None = None) -> None:
        prev = self.state.value
        if event_id in self._seen:
            raise ContractError("outcome event ID already used")
        self._seen.add(event_id)
        self.events.append(JourneyEvent(
            self.journey_id, prev, prev, Event.OUTCOME_REPORTED.value,
            event_id=event_id, sequence=len(self.events) + 1, created_at=received_at or event_timestamp(),
            outcome_response_id=submission.response_id, outcome_choice=submission.choice,
        ))

    def record_playback(self, response_id: str, event_id: str, *, received_at: str | None = None) -> None:
        prev = self.state.value
        if event_id in self._seen:
            raise ContractError("playback event ID already used")
        self._seen.add(event_id)
        self.events.append(JourneyEvent(
            self.journey_id, prev, prev, Event.PLAYBACK_CONFIRMED.value,
            event_id=event_id, sequence=len(self.events) + 1, created_at=received_at or event_timestamp(),
            playback_response_id=response_id,
        ))

    def record_linked_contact(self, reference: ResponseReference, event_id: str, *,
                              received_at: str | None = None) -> None:
        prev = self.state.value
        if event_id in self._seen:
            raise ContractError("contact event ID already used")
        self._seen.add(event_id)
        self.events.append(JourneyEvent(
            self.journey_id, prev, prev, Event.CONTACT_LINKED.value,
            event_id=event_id, sequence=len(self.events) + 1, created_at=received_at or event_timestamp(),
            related_journey_id=reference.journey_id, related_response_id=reference.response_id,
        ))
