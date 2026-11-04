"""Versioned contracts.

Pure-Python dataclasses so the core runs without third-party packages.
Every inbound contract is parsed with ``from_dict`` which REJECTS unknown
fields. This mirrors the APIM schema-validation policy and is the code-level
enforcement point for B7: a demographic attribute can never ride along in a
StrategyRequest, because there is no field to put it in.
"""
from __future__ import annotations
import re

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from governedgraph.common import domain

CONTRACT_VERSION = "1.0"

# Attribute names that must never appear in a runtime request (B7).
# Used by tests and by output audits; the primary control is the closed schema.
DEMOGRAPHIC_FIELD_NAMES = frozenset(
    {
        "age", "age_band", "ageband", "dob", "date_of_birth", "birth_year",
        "gender", "sex", "salary", "salary_band", "income", "nationality",
        "ethnicity", "religion", "disability", "marital_status", "cohort",
    }
)

CHANNELS = frozenset({"voice", "chat", "portal", "letter_follow_up"})
ACCESSIBILITY_MODES = frozenset(
    {"standard", "plain_language", "english", "slower_pace", "large_text"}
)


class ContractError(ValueError):
    """Raised when a payload violates a contract (unknown or invalid field)."""


def _strict(cls, payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise ContractError(f"{cls.__name__}: payload must be an object")
    allowed = {f.name for f in fields(cls)}
    unknown = set(payload) - allowed
    if unknown:
        demo = {u for u in unknown if u.lower() in DEMOGRAPHIC_FIELD_NAMES}
        if demo:
            raise ContractError(
                f"{cls.__name__}: demographic field(s) {sorted(demo)} rejected (B7)"
            )
        raise ContractError(f"{cls.__name__}: unknown field(s) {sorted(unknown)} rejected")
    return cls(**payload)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_VOCABULARY = domain.vocabulary()

# Routes the framework itself owns: option-level questions escalate (B9), humans are always reachable, and the
# comprehension loop is host-controlled. A domain pack can add explanation intents but cannot remove these rows.
FRAMEWORK_INTENT_DECISIONS = frozenset({
    ("clarification", "unclassified", "more_context_needed"),
    ("advice", "option_level_question", "personal_choice_or_comparison"),
    ("human", "unclassified", "explicit_human_request"),
    ("comprehension", "unclassified", "restating_previous_explanation"),
})
INTENT_DECISIONS = FRAMEWORK_INTENT_DECISIONS | {
    ("explanation", intent, reason) for intent, reason in _VOCABULARY["explanation_intents"]
}
HISTORICAL_REASONS = _VOCABULARY["historical_reasons"]
UNDERSTANDING_DECISIONS = {
    ("understood", "meaning_correct"),
    ("needs_explanation", "meaning_not_yet_understood"),
    ("uncertain", "insufficient_evidence"),
}
RESPONSE_LANGUAGES = _VOCABULARY["response_languages"]
EXPLANATION_CONCEPTS = _VOCABULARY["explanation_concepts"]
REVIEW_ISSUES = frozenset({
    "relevance", "language", "grounding", "uncertainty", "strategy", "advice", "document_context",
})


@dataclass(frozen=True)
class ExplanationContext:
    concepts: list[str] = field(default_factory=list)
    teach_back_concepts: list[str] = field(default_factory=list)

    def __post_init__(self):
        for items in (self.concepts, self.teach_back_concepts):
            if (not isinstance(items, list) or len(items) > len(EXPLANATION_CONCEPTS)
                    or any(not isinstance(item, str) or item not in EXPLANATION_CONCEPTS for item in items)
                    or len(set(items)) != len(items)):
                raise ContractError("invalid closed explanation concepts")
        if not set(self.teach_back_concepts) <= set(self.concepts):
            raise ContractError("teach-back cannot assess an untaught concept")

    @classmethod
    def from_dict(cls, payload: dict) -> "ExplanationContext":
        return _strict(cls, payload)


@dataclass(frozen=True)
class ReviewFeedback:
    issue: str
    quote: str

    def __post_init__(self):
        if (not isinstance(self.issue, str) or self.issue not in REVIEW_ISSUES
                or not isinstance(self.quote, str) or not self.quote.strip() or len(self.quote) > 1600):
            raise ContractError("invalid closed answer-review feedback")

    @classmethod
    def from_dict(cls, payload: dict) -> "ReviewFeedback":
        return _strict(cls, payload)


@dataclass(frozen=True)
class AnswerReview:
    verdict: str
    issues: list[str]
    concepts: list[str]
    teach_back_concepts: list[str]

    def __post_init__(self):
        if not isinstance(self.verdict, str) or self.verdict not in {"pass", "revise", "unassessable", "fail"}:
            raise ContractError("invalid answer-review verdict")
        if (not isinstance(self.issues, list) or len(self.issues) > len(REVIEW_ISSUES)
                or any(not isinstance(item, str) or item not in REVIEW_ISSUES for item in self.issues)
                or len(set(self.issues)) != len(self.issues)
                or (self.verdict == "pass") != (not self.issues)):
            raise ContractError("invalid closed answer-review issues")
        ExplanationContext(self.concepts, self.teach_back_concepts)
        if self.verdict != "pass" and (self.concepts or self.teach_back_concepts):
            raise ContractError("only an accepted explanation supplies taught concepts")

    @classmethod
    def from_dict(cls, payload: dict) -> "AnswerReview":
        return _strict(cls, payload)


@dataclass(frozen=True)
class IntentDecision:
    route: str
    intent: str
    reason: str
    response_language: str = "en"

    def __post_init__(self):
        if (not all(isinstance(value, str) for value in (self.route, self.intent, self.reason))
                or (self.route, self.intent, self.reason) not in INTENT_DECISIONS
                or not isinstance(self.response_language, str) or self.response_language not in RESPONSE_LANGUAGES):
            raise ContractError("invalid intent-agent decision")

    @classmethod
    def from_dict(cls, payload: dict) -> "IntentDecision":
        return _strict(cls, payload)


@dataclass(frozen=True)
class UnderstandingDecision:
    outcome: str
    reason: str

    def __post_init__(self):
        if (not all(isinstance(value, str) for value in (self.outcome, self.reason))
                or (self.outcome, self.reason) not in UNDERSTANDING_DECISIONS):
            raise ContractError("invalid understanding-agent decision")

    @classmethod
    def from_dict(cls, payload: dict) -> "UnderstandingDecision":
        return _strict(cls, payload)


@dataclass(frozen=True)
class AnswerDraft:
    text: str
    content_ids: list[str]

    def __post_init__(self):
        if (not isinstance(self.text, str) or not self.text.strip()
                or not isinstance(self.content_ids, list)
                or any(not isinstance(value, str) or not value for value in self.content_ids)):
            raise ContractError("invalid grounded answer draft")

    @classmethod
    def from_dict(cls, payload: dict) -> "AnswerDraft":
        return _strict(cls, payload)


@dataclass(frozen=True)
class DecisionEvidence:
    role: str
    agent_name: str
    agent_version: str
    decision: dict[str, Any]

    def __post_init__(self):
        if not isinstance(self.role, str) or self.role not in {"intent", "understanding", "answer-review"}:
            raise ContractError("unknown decision-agent role")
        if not isinstance(self.agent_name, str) or self.agent_name not in {
            f"gg-{self.role}-agent", f"offline-fixture-{self.role}",
        }:
            raise ContractError("unknown decision-agent identity")
        if not isinstance(self.agent_version, str) or not (
            self.agent_version.isdecimal() or self.agent_version == "fixture-1"
        ):
            raise ContractError("decision evidence requires an immutable agent version")
        parser = {"intent": IntentDecision, "understanding": UnderstandingDecision,
                  "answer-review": AnswerReview}[self.role]
        parser.from_dict(self.decision)

    @classmethod
    def from_dict(cls, payload: dict) -> "DecisionEvidence":
        return _strict(cls, payload)


@dataclass(frozen=True)
class RequestKey:
    """Runtime strategy request key: journey state + intent + channel + accessibility mode.

    No demographics (B7, P-6). Accessibility mode is member-selected, never inferred.
    """

    journey_state: str
    intent: str
    channel: str
    accessibility_mode: str = "standard"

    def __post_init__(self):
        if self.channel not in CHANNELS:
            raise ContractError(f"unknown channel {self.channel!r}")
        if self.accessibility_mode not in ACCESSIBILITY_MODES:
            raise ContractError(f"unknown accessibility mode {self.accessibility_mode!r}")

    @property
    def id(self) -> str:
        return f"{self.journey_state}|{self.intent}|{self.channel}|{self.accessibility_mode}"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RequestKey":
        return _strict(cls, payload)


@dataclass(frozen=True)
class StrategyRequest:
    journey_id: str
    journey_state: str
    intent: str
    channel: str
    accessibility_mode: str = "standard"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StrategyRequest":
        return _strict(cls, payload)

    @property
    def key(self) -> RequestKey:
        return RequestKey(self.journey_state, self.intent, self.channel, self.accessibility_mode)


@dataclass(frozen=True)
class RankedStrategy:
    strategy_id: str
    version: str
    rank: int


@dataclass(frozen=True)
class StrategyResponse:
    snapshot_id: str
    signature_valid: bool
    catalogue_version: str
    arm: str  # "treatment" (learned ranking) or "control" (static expert priors, B10)
    ranked: list[RankedStrategy]
    suspended: list[str]
    always_available: list[str]
    source: str  # "snapshot" | "expert_priors_fallback"
    exploration: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemberValue:
    """One official value from the deterministic system of record (B1).

    The AI side never stores, computes or rounds these; it binds them by value_id (B2).
    """

    value_id: str
    label: str
    value: float
    unit: str
    source_system: str
    calculation_run_id: str
    as_of: str
    display: str  # pre-formatted by the system of record; agents quote this verbatim


@dataclass(frozen=True)
class MemberValues:
    member_ref: str
    values: list[MemberValue]

    def by_id(self) -> dict[str, MemberValue]:
        return {v.value_id: v for v in self.values}


@dataclass
class ExplainabilityRecord:
    journey_id: str
    member_ref: str  # pseudonymous
    strategy_id: str
    strategy_version: str
    snapshot_id: str
    arm: str
    content_versions: list[str]
    value_ids: list[str]
    value_sources: list[str]
    model_deployment: str
    agent_name: str
    agent_version: str
    template_version: str
    disclosure_given: bool
    escalation_offered: bool
    request_key: dict[str, str]
    numeric_fidelity_passed: bool
    created_at: str = field(default_factory=utcnow)
    contract_version: str = "1.3"
    response_id: str = ""
    response_kind: str = "legacy"
    decision_evidence: list[DecisionEvidence] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.decision_evidence, list) or len(self.decision_evidence) > 4:
            raise ContractError("invalid decision evidence")
        self.decision_evidence = [
            item if isinstance(item, DecisionEvidence) else DecisionEvidence.from_dict(item)
            for item in self.decision_evidence
        ]
        routing = [item for item in self.decision_evidence if item.role != "answer-review"]
        reviews = [item for item in self.decision_evidence if item.role == "answer-review"]
        if len({item.role for item in routing}) != len(routing):
            raise ContractError("duplicate decision-agent evidence")
        if len(reviews) > 2 or (len(reviews) == 2 and (
                reviews[0].decision["verdict"] == "pass"
                or "advice" in reviews[0].decision["issues"]
                or reviews[0].agent_version != reviews[1].agent_version)):
            raise ContractError("unbounded answer-review evidence")
        if self.decision_evidence and self.contract_version not in {"1.2", "1.3"}:
            raise ContractError("decision evidence requires a supported explainability contract")
        if reviews and self.contract_version != "1.3":
            raise ContractError("answer review requires explainability contract 1.3")
        if not isinstance(self.response_kind, str) or self.response_kind not in {
            "legacy", "explanation", "disclosure", "acknowledgment", "escalation",
        }:
            raise ContractError("invalid explainability response kind")
        if not isinstance(self.response_id, str) or (
            self.response_id and not re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{64})", self.response_id)
        ):
            raise ContractError("response_id must be an opaque host-generated identifier")
        if any(not isinstance(v, str) or not v.strip() or v.lower() == "latest"
               for v in (self.agent_name, self.agent_version, self.model_deployment)):
            raise ContractError("explainability requires a pinned agent name/version and model deployment")
        leaked = {k for k in self.request_key if k.lower() in DEMOGRAPHIC_FIELD_NAMES}
        if leaked:
            raise ContractError(f"explainability record request key contains demographics {leaked} (B7)")
        if set(self.request_key) != {"journey_state", "intent", "channel", "accessibility_mode"}:
            raise ContractError("request_key must be exactly journey_state/intent/channel/accessibility_mode")

    @classmethod
    def from_dict(cls, d: dict) -> "ExplainabilityRecord":
        return _strict(cls, d)


@dataclass(frozen=True)
class EscalationRequest:
    journey_id: str
    reason_code: str  # advice_intent | member_request | teach_back_failed | component_failure | conversation_limit | safety
    context_summary: str
    value_ids: list[str]
    callback_preference: str = "none"
    created_at: str = field(default_factory=utcnow)
    escalation_id: str = field(default_factory=lambda: uuid4().hex)

    @classmethod
    def from_dict(cls, d: dict) -> "EscalationRequest":
        return _strict(cls, d)


@dataclass(frozen=True)
class JourneyEvent:
    journey_id: str
    from_state: str
    to_state: str
    event: str
    strategy_id: str | None = None
    strategy_version: str | None = None
    outcome: str | None = None  # resolved | repeat_contact | escalation | complaint | comprehension_check
    created_at: str = field(default_factory=utcnow)
    event_id: str = field(default_factory=lambda: uuid4().hex)
    sequence: int = 0
    response_id: str | None = None
    response_withheld: bool = False

    def __post_init__(self):
        if type(self.response_withheld) is not bool or (self.response_withheld and self.response_id is not None):
            raise ContractError("a withheld response cannot be marked delivered")
        if self.response_id is not None and (
            not isinstance(self.response_id, str)
            or not re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{64})", self.response_id)
        ):
            raise ContractError("event response_id must be an opaque host-generated identifier")

    @classmethod
    def from_dict(cls, d: dict) -> "JourneyEvent":
        return _strict(cls, d)


@dataclass(frozen=True)
class SuspensionEvent:
    strategy_id: str
    strategy_version: str
    journey_state: str
    audit_id: str
    gap_metric: float
    threshold_version: str
    governance_case_id: str
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def from_dict(cls, d: dict) -> "SuspensionEvent":
        return _strict(cls, d)
