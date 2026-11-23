"""Batch review of declined interactions: reference-only improvement proposals for human review.

A member question that reached no explanation, or whose answer a review flagged, can be a false negative of the approved
content and strategies, or point to approved content or a strategy that does not exist yet. This module groups those
interactions by closed situation and drafts proposals for a human owner.

Boundaries:
- B4 (owner decision IMP-GAP-REVIEW-2): the inputs are the immutable response record (closed fields) and the member's
  declined question, kept briefly by ``explainability.declined_questions``. A few questions appear as examples inside a
  proposal and reach a drafting agent; nothing reaches a prompt, template, model or catalogue without an owner approving
  a passage or style. Drafted passages may add general facts, which require recorded owner source verification
  before publication.
- B7: the situation holds journey stage, topic, channel, accessibility mode and language only; no demographic field exists.
- B3/B8: a proposal is inert. Strategy drafts reuse the approved pattern library through ``StyleChange``; a content
  suggestion is a draft the owner edits. Activation needs an owner decision (Tier 2) through the existing paths.
- B1/B2: a suggestion may not contain a figure that no source passage contains, and no value placeholder.
- B1/B2: nothing is calculated or written as a member value; drafts contain no figures.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone

from governedgraph.common.contracts import (
    ACCESSIBILITY_MODES, CHANNELS, DECLINE_REASONS, RESPONSE_LANGUAGES, REVIEW_ISSUES, ContractError, ExplainabilityRecord,
    RETIRED_REVIEW_ISSUES, RequestKey, _strict, current_review_issues,
)
from governedgraph.common.io import canonical_json
from governedgraph.guardrails.advice_intent import response_steers_choice
from governedgraph.search.corpus import ContentDocument, approve_version, version_key
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY, Catalogue
from governedgraph.strategy_service.catalogue_release import StyleChange, patterns, stages

WATERMARK = "ILLUSTRATIVE - synthetic data"
RULES_VERSION = "1.0"
KINDS = ("content_gap", "content_revision", "strategy_gap", "false_negative_review")
OUTCOMES = ("unanswered", "review_flagged", "no_style", "escalated")
# Review findings that say the approved passage itself is incomplete or missing, versus a style or relevance defect.
CONTENT_ISSUES = frozenset({"grounding"})
QUALITY_ISSUES = frozenset({"relevance", "strategy"})
MIN_DECLINED = 5
MIN_JOURNEYS = 3
MAJORITY = 0.5
SAMPLE_JOURNEYS = 5
SAMPLE_QUESTIONS = 5
SAMPLE_CASES = 10
# Proposals stored before the review redesign still carry the retired labels; they stay readable.
STORED_REVIEW_ISSUES = frozenset(REVIEW_ISSUES | set(RETIRED_REVIEW_ISSUES))
CASE_FIELDS = {"journey_id", "created_at", "outcome", "decline_reason", "review_issues", "passages", "question"}
QUESTION_CHARS = 300
QUESTION_RETENTION_DAYS = 30  # the lifecycle rule on the declined-questions container
TOP_CONTENT = 5
NEW_CONTENT_PREFIX = "C-G"
PROPOSAL_ID = re.compile(r"GP-[a-f0-9]{24}")
DIGEST = re.compile(r"[a-f0-9]{64}")
JOURNEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")
STAGE = re.compile(r"[a-z][a-z0-9_]{0,60}")
STAGE_OR_ALL = re.compile(r"\*|[a-z][a-z0-9_]{0,60}")
TOPIC = re.compile(r"[a-z][a-z0-9_]{0,80}")
CONTENT_REF = re.compile(r"[A-Za-z0-9_.-]{1,100}")


@dataclass(frozen=True)
class Signal:
    """One declined interaction, derived in memory from an immutable record and never persisted."""

    journey_id: str
    created_at: str
    journey_state: str
    topic: str
    channel: str
    accessibility_mode: str
    language: str
    outcome: str
    review_issues: tuple[str, ...]
    content_versions: tuple[str, ...]
    decline_reason: str = ""
    response_id: str = ""
    question: str = ""

    @property
    def situation(self) -> tuple[str, str, str, str, str]:
        return self.journey_state, self.topic, self.channel, self.accessibility_mode, self.language


def signal_for(record: ExplainabilityRecord) -> Signal | None:
    """Classify a response record. Only a question that asked for an explanation can be a decline."""
    intent = next((item.decision for item in reversed(record.decision_evidence) if item.role == "intent"), None)
    if intent is None or intent["route"] != "explanation":
        return None
    reviews = [item.decision for item in record.decision_evidence if item.role == "answer-review"]
    # A delivered answer counts only by its final review; findings on a draft that was repaired and then passed do not.
    judged = reviews[-1:] if record.response_kind == "explanation" else reviews
    issues = sorted({issue for review in judged if review["verdict"] != "pass"
                     for issue in current_review_issues(review["issues"])})
    if record.response_kind == "acknowledgment" and record.strategy_id == ESCALATION_STRATEGY:
        outcome = "unanswered"
    elif record.response_kind == "escalation":
        # Only "no eligible style" says the approved styles could not explain; other escalations are context.
        outcome = "no_style" if record.decline_reason == "no_eligible_style" else "escalated"
    elif record.response_kind == "explanation" and issues:
        outcome = "review_flagged"
    else:
        return None
    key = record.request_key
    return Signal(record.journey_id, record.created_at, key["journey_state"], intent["reason"], key["channel"],
                  key["accessibility_mode"], intent["response_language"], outcome, tuple(issues),
                  tuple(sorted(set(record.content_versions))), record.decline_reason, record.response_id)


def signals(records: list[ExplainabilityRecord], *, excluded_journeys: set[str] = frozenset(),
            since: str = "", questions: dict[str, str] | None = None) -> tuple[list[Signal], int]:
    """Declined interactions of non-test journeys at or after ``since``; also the count of excluded test records.

    ``questions`` maps a response_id to the member's kept question text, attached when one was captured."""
    result, excluded = [], 0
    for record in records:
        if since and record.created_at < since:
            continue
        signal = signal_for(record)
        if signal is None:
            continue
        if record.journey_id in excluded_journeys:
            excluded += 1
            continue
        result.append(replace(signal, question=(questions or {}).get(signal.response_id, "")))
    return result, excluded


def _count_map(value, allowed=None, limit: int = 20) -> dict[str, int]:
    if (not isinstance(value, dict) or len(value) > limit
            or any(not isinstance(key, str) or not CONTENT_REF.fullmatch(key) or type(count) is not int or count < 1
                   for key, count in value.items())
            or (allowed is not None and not set(value) <= allowed)):
        raise ContractError("invalid closed count map")
    return value


@dataclass(frozen=True)
class Evidence:
    window_start: str
    window_end: str
    declined: int
    unanswered: int
    review_flagged: int
    no_style: int
    escalated: int
    journeys: int
    content_retrieved_share: float
    review_issues: dict
    decline_reasons: dict
    content_versions: dict
    eligible_styles: list
    sample_journey_ids: list
    examples: list
    cases: list
    questions_expire_at: str = ""  # when the oldest kept question copied into this proposal must be redacted

    def __post_init__(self):
        counts = (self.declined, self.unanswered, self.review_flagged, self.no_style, self.escalated, self.journeys)
        if (any(type(item) is not int or item < 0 for item in counts)
                or self.declined != self.unanswered + self.review_flagged + self.no_style
                or self.journeys > self.declined + self.escalated
                or not isinstance(self.content_retrieved_share, float) or not 0 <= self.content_retrieved_share <= 1
                or any(not isinstance(item, str) or len(item) > 40
                       for item in (self.window_start, self.window_end, self.questions_expire_at))):
            raise ContractError("invalid evidence counts")
        _count_map(self.review_issues, set(STORED_REVIEW_ISSUES))
        _count_map(self.decline_reasons, set(DECLINE_REASONS))
        _count_map(self.content_versions, limit=TOP_CONTENT)
        if (not isinstance(self.eligible_styles, list) or len(self.eligible_styles) > 50
                or any(not isinstance(item, str) or not CONTENT_REF.fullmatch(item) for item in self.eligible_styles)
                or not isinstance(self.sample_journey_ids, list) or len(self.sample_journey_ids) > SAMPLE_JOURNEYS
                or any(not isinstance(item, str) or not JOURNEY_ID.fullmatch(item) for item in self.sample_journey_ids)):
            raise ContractError("invalid evidence references")
        if (not isinstance(self.examples, list) or len(self.examples) > SAMPLE_QUESTIONS
                or any(not isinstance(item, str) or not item.strip() or len(item) > QUESTION_CHARS for item in self.examples)):
            raise ContractError("invalid example questions")
        if not isinstance(self.cases, list) or len(self.cases) > SAMPLE_CASES:
            raise ContractError("invalid example cases")
        for case in self.cases:
            if (not isinstance(case, dict) or set(case) != CASE_FIELDS or not JOURNEY_ID.fullmatch(str(case["journey_id"]))
                    or case["outcome"] not in OUTCOMES or not isinstance(case["created_at"], str) or len(case["created_at"]) > 40
                    or (case["decline_reason"] and case["decline_reason"] not in DECLINE_REASONS)
                    or not isinstance(case["review_issues"], list) or not set(case["review_issues"]) <= STORED_REVIEW_ISSUES
                    or not isinstance(case["passages"], list) or len(case["passages"]) > TOP_CONTENT
                    or any(not isinstance(item, str) or not CONTENT_REF.fullmatch(item) for item in case["passages"])
                    or not isinstance(case["question"], str) or len(case["question"]) > QUESTION_CHARS):
                raise ContractError("invalid example case")

    @classmethod
    def from_dict(cls, payload: dict) -> Evidence:
        return _strict(cls, payload)

    def digest_view(self) -> dict:
        view = asdict(self)
        for volatile in ("window_start", "window_end", "sample_journey_ids", "examples", "cases", "questions_expire_at"):
            view.pop(volatile)
        return view


def _situation(value) -> dict:
    keys = {"journey_state", "topic", "channel", "accessibility_mode", "language"}
    if (not isinstance(value, dict) or set(value) != keys or not STAGE.fullmatch(str(value["journey_state"]))
            or not TOPIC.fullmatch(str(value["topic"])) or value["channel"] not in CHANNELS
            or value["accessibility_mode"] not in ACCESSIBILITY_MODES or value["language"] not in RESPONSE_LANGUAGES):
        raise ContractError("invalid proposal situation")
    return value


def _text(value, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise ContractError("invalid draft text")
    return value


def validate_draft(kind: str, draft) -> dict:
    """Drafts are closed: text fields come from the domain vocabulary and catalogue, never from a conversation."""
    if not isinstance(draft, dict):
        raise ContractError("a closed draft is required")
    shapes = {
        "content_gap": {"action", "title", "language", "journey_states", "answers", "related_content_ids"},
        "content_revision": {"action", "base_id", "content_id", "base_version", "title", "language", "journey_states",
                             "answers"},
        "strategy_gap": {"action", "name", "pattern_id", "eligible_journey_states", "channels", "accessibility_modes"},
        "false_negative_review": {"action", "focus", "issues"},
    }
    suggested = draft.get("suggested") if kind in {"content_gap", "content_revision", "strategy_gap"} else None
    if set(draft) - {"suggested"} != shapes[kind] or ("suggested" in draft and suggested is None):
        raise ContractError("unexpected draft fields")
    if suggested is not None:
        (validate_style_suggestion if kind == "strategy_gap" else validate_suggestion)(suggested)
    if kind == "strategy_gap":
        StyleChange.from_dict({key: value for key, value in draft.items() if key != "suggested"})
    elif kind == "false_negative_review":
        if (draft["action"] != "review" or draft["focus"] not in {"output_gate", "answer_review"}
                or not isinstance(draft["issues"], list) or not set(draft["issues"]) <= STORED_REVIEW_ISSUES):
            raise ContractError("invalid review draft")
    else:
        _text(draft["title"], 200)
        _text(draft["answers"], 1000, allow_empty=True)
        if (draft["action"] != ("add" if kind == "content_gap" else "revise") or draft["language"] not in RESPONSE_LANGUAGES
                or not isinstance(draft["journey_states"], list) or not draft["journey_states"]
                or any(not isinstance(item, str) or not STAGE_OR_ALL.fullmatch(item) for item in draft["journey_states"])):
            raise ContractError("invalid content draft")
        refs = draft["related_content_ids"] if kind == "content_gap" else [
            draft["base_id"], draft["content_id"], draft["base_version"]]
        if not isinstance(refs, list) or any(not isinstance(item, str) or not CONTENT_REF.fullmatch(item) for item in refs):
            raise ContractError("invalid content references")
    return draft


# title/answers/body: the draft. sources: approved passages it stayed consistent with. claims: every factual statement, figure
# and date the owner must verify. gaps: what the owner must supply. themes/coverage: the analysis of the member questions.
SUGGESTION_FIELDS = {"title", "answers", "body", "sources", "claims", "gaps", "themes", "coverage", "agent", "context"}
# Set by this code, never by the model: the approved passage ids the agent was shown.
CODE_SET = {"agent", "context"}


def validate_suggestion(value) -> dict:
    if (not isinstance(value, dict) or set(value) != SUGGESTION_FIELDS or not isinstance(value["agent"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}(?::[0-9]{1,6})?", value["agent"])):
        raise ContractError("invalid drafted suggestion")
    _text(value["title"], 200)
    _text(value["answers"], 1000, allow_empty=True)
    _text(value["body"], 8000, allow_empty=True)
    for key in ("sources", "claims", "gaps", "themes", "coverage", "context"):
        if (not isinstance(value[key], list) or len(value[key]) > (30 if key == "claims" else 10)
                or any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in value[key])):
            raise ContractError("invalid drafted suggestion lists")
    return value


# What the drafting agent returns for a style gap. fit "existing": pattern_id is the approved pattern to add as a new style
# called name. fit "new": no approved pattern fits; pattern_id is only the closest one and new_style_* describe a way of
# explaining that does not exist yet, for people to build. Never applied by itself. context: the style ids the agent saw.
STYLE_FIELDS = {"fit", "pattern_id", "name", "why", "new_style_name", "new_style_idea"}
STYLE_SUGGESTION_FIELDS = STYLE_FIELDS | {"themes", "agent", "context"}
CONTENT_ONLY_FIELDS = {"title", "answers", "body", "sources", "claims", "gaps", "coverage"}


def validate_style_suggestion(value) -> dict:
    if (not isinstance(value, dict) or set(value) != STYLE_SUGGESTION_FIELDS or not isinstance(value["agent"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}(?::[0-9]{1,6})?", value["agent"])
            or value["fit"] not in {"existing", "new"}
            or value["pattern_id"] not in {style.strategy_id for style in patterns()}):
        raise ContractError("invalid drafted style suggestion")
    _text(value["name"], 120, allow_empty=value["fit"] == "new")
    _text(value["why"], 1000)
    _text(value["new_style_name"], 120, allow_empty=value["fit"] == "existing")
    _text(value["new_style_idea"], 1000, allow_empty=value["fit"] == "existing")
    for key in ("themes", "context"):
        if (not isinstance(value[key], list) or len(value[key]) > 10
                or any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in value[key])):
            raise ContractError("invalid drafted style suggestion lists")
    return value


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    kind: str
    batch_id: str
    created_at: str
    rules_version: str
    situation: dict
    evidence: dict
    rationale: str
    draft: dict
    evidence_digest: str
    watermark: str
    content_digest: str

    def __post_init__(self):
        if (not isinstance(self.proposal_id, str) or not PROPOSAL_ID.fullmatch(self.proposal_id)
                or self.kind not in KINDS or not isinstance(self.batch_id, str) or not re.fullmatch(r"GRB-[0-9TZ]{16}", self.batch_id)
                or not isinstance(self.created_at, str) or len(self.created_at) > 40
                or self.rules_version != RULES_VERSION or self.watermark != WATERMARK
                or not isinstance(self.evidence_digest, str) or not DIGEST.fullmatch(self.evidence_digest)):
            raise ContractError("invalid proposal envelope")
        _situation(self.situation)
        evidence = Evidence.from_dict(self.evidence)
        validate_draft(self.kind, self.draft)
        _text(self.rationale, 1000)
        if self.evidence_digest != digest_of(evidence, self.draft):
            raise ContractError("proposal evidence digest mismatch")
        if not isinstance(self.content_digest, str) or self.content_digest != content_digest_of(asdict(self)):
            raise ContractError("proposal content digest mismatch")

    @classmethod
    def from_dict(cls, payload: dict) -> Proposal:
        return _strict(cls, payload)

    @property
    def blob_name(self) -> str:
        return f"proposals/{self.proposal_id}/{self.evidence_digest}.json"

    def encode(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True).encode()


def content_digest_of(payload: dict) -> str:
    """Seals everything an owner sees, including example questions and a drafted suggestion, so a decision is bound to it."""
    body = {key: value for key, value in payload.items() if key not in {"content_digest", "batch_id", "created_at"}}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def sealed(fields: dict) -> Proposal:
    return Proposal.from_dict({**fields, "content_digest": content_digest_of(fields)})


def digest_of(evidence: Evidence, draft: dict) -> str:
    stable = {key: value for key, value in draft.items() if key != "suggested"}
    return hashlib.sha256(canonical_json({"evidence": evidence.digest_view(), "draft": stable})).hexdigest()


def proposal_id(kind: str, situation: dict, base_id: str = "") -> str:
    identity = "|".join((RULES_VERSION, kind, *(situation[key] for key in (
        "journey_state", "topic", "channel", "accessibility_mode", "language")), *([base_id] if base_id else [])))
    return "GP-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def batch_id(now: datetime) -> str:
    return "GRB-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def humanize(value: str) -> str:
    return value.replace("_", " ")


# ----- content helpers shared with the admin console and the publisher -----
def approved_for(corpus: list[ContentDocument], state: str, language: str) -> list[ContentDocument]:
    return [doc for doc in corpus if doc.status == "approved" and doc.language == language
            and ("*" in doc.journey_states or state in doc.journey_states)]


def effective_corpus(seed: list[ContentDocument], approvals: list[ContentDocument]) -> list[ContentDocument]:
    """The seed feed with owner-approved documents applied in version order, as the publisher will apply them."""
    docs = list(seed)
    for candidate in sorted(approvals, key=lambda doc: (doc.content_id, version_key(doc.version), doc.language)):
        docs = approve_version(docs, candidate)
    return docs


def next_version(version: str) -> str:
    major, minor = (version_key(version) + (0,))[:2]
    return f"{major}.{minor + 1}"


def next_content_id(corpus: list[ContentDocument], reserved=()) -> str:
    """The next unused new-content id. ``reserved`` holds the content ids and document ids of every stored approval,
    including refused ones, whose create-only approval blob still exists even though the passage left the corpus."""
    ids = [*(doc.content_id for doc in corpus), *reserved]
    used = [int(match[1]) for item in ids if (match := re.fullmatch(re.escape(NEW_CONTENT_PREFIX) + r"(\d{3,6})", item))]
    return f"{NEW_CONTENT_PREFIX}{max(used, default=0) + 1:03d}"


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{4,}", text.lower())}


def related_content(corpus: list[ContentDocument], state: str, language: str, description: str) -> list[str]:
    wanted = _words(description)
    ranked = sorted(((len(wanted & _words(doc.title + " " + doc.answers)), doc.id)
                     for doc in approved_for(corpus, state, language)), key=lambda item: (-item[0], item[1]))
    return [doc_id for score, doc_id in ranked if score][:3]


def content_document(*, draft: dict, content: dict, corpus: list[ContentDocument], approval_ref: str,
                     reserved=()) -> ContentDocument:
    """Build the approved document an owner decided on; raises ContractError when it breaks a content rule."""
    if set(content) != {"title", "journey_states", "answers", "body"}:
        raise ContractError("content needs a title, stages, questions answered and a body")
    body = _text(content["body"], 8000)
    # Title and questions answered are indexed and searched like the body, so they follow the same rules.
    for text in (body, _text(content["title"], 200), _text(content["answers"], 1000, allow_empty=True)):
        if "{{" in text or "}}" in text or response_steers_choice(text) or STEERING_EXTRA.search(text):
            raise ContractError("content may not hold value placeholders or steer a choice")
    states = content["journey_states"]
    if not isinstance(states, list) or not states or any(not isinstance(item, str) or item not in stages() | {"*"} for item in states):
        raise ContractError("choose approved journey stages")
    if draft["action"] == "add":
        content_id, version = next_content_id(corpus, reserved), "1.0"
    else:
        base = next((doc for doc in corpus if doc.id == draft["base_id"] and doc.status == "approved"), None)
        if base is None:
            raise ContractError("the passage changed since this proposal; refresh before deciding")
        if (_text(content["body"], 8000).strip() == base.body.strip()
                and _text(content["answers"], 1000, allow_empty=True) == base.answers and content["title"] == base.title
                and list(states) == list(base.journey_states)):
            raise ContractError("a new version must change the passage")
        content_id, version = base.content_id, next_version(base.version)
        while f"{content_id}-v{version}" in reserved:  # a refused approval still holds that create-only name
            version = next_version(version)
    return ContentDocument(id=f"{content_id}-v{version}", content_id=content_id, version=version, status="approved",
                    title=_text(content["title"], 200).strip(), language=draft["language"], journey_states=list(states),
                    approval_ref=approval_ref, body=body.strip(),
                    watermark="ILLUSTRATIVE - synthetic content, not legal or financial information",
                    answers=_text(content["answers"], 1000, allow_empty=True).strip())


# ----- drafting with an agent: closed request, grounded and bounded answer -----
FIGURE = re.compile(r"\d+(?:[.,]\d+)*")
# The shared steering check is narrow and covers one language; suggestions also get this wider two-language screen.
STEERING_EXTRA = re.compile(
    r"\byou (?:should|ought to|must|need to|had better)\b|\bi (?:would )?(?:suggest|recommend|advise)\b|"
    r"\bthe (?:best|better|right) (?:option|choice)\b|\bwe (?:advise|recommend)\b|"
    r"\bu (?:moet|moeten|dient|zou moeten|kunt het beste)\b|\bje (?:moet|zou moeten|kunt het beste)\b|"
    r"\bik (?:raad|adviseer|beveel)\b|\bde (?:beste|betere|juiste) (?:optie|keuze)\b|\braden wij\b", re.IGNORECASE)


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
HAS_DIGIT = re.compile(r"\S*\d\S*")
NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-ZÀ-Ý][a-zà-ÿ'-]+(?:\s+(?:van|de|der|den|ten|ter|von)?\s*[A-ZÀ-Ý][a-zà-ÿ'-]+)*")


def redact_for_model(text: str) -> str:
    """Strip what could identify a member before wording leaves the platform for a model: addresses, links, anything
    holding a digit (amounts, dates, account, phone and reference numbers) and capitalised words inside a sentence
    (likely names). It is a heuristic, so the owner still sees the real example questions and the checks still run on them."""
    text = HAS_DIGIT.sub("[number]", URL.sub("[link]", EMAIL.sub("[email]", text)))
    return NAME.sub("[name]", text)


def for_model(request: dict) -> dict:
    """The request as the drafting agent receives it: member wording redacted; checks keep using the original."""
    return {**request, "member_questions": [redact_for_model(item) for item in request["member_questions"]]}


def drafting_request(proposal: Proposal, corpus: list[ContentDocument], vocabulary: dict,
                     catalogue: Catalogue | None = None) -> dict | None:
    """What the drafting agent may see: the situation, the kept example questions and approved passages. Nothing else."""
    if proposal.kind not in {"content_gap", "content_revision", "strategy_gap"} or not proposal.evidence["examples"]:
        return None
    draft = proposal.draft
    if proposal.kind == "strategy_gap":
        return style_request(proposal, vocabulary, catalogue or Catalogue.load())
    wanted = [draft["base_id"]] if proposal.kind == "content_revision" else draft["related_content_ids"]
    passages = [{"id": doc.id, "title": doc.title, "body": doc.body} for doc in corpus
                if doc.id in wanted and doc.status == "approved"]
    topic = proposal.situation["topic"]
    return {"task": "revise_passage" if proposal.kind == "content_revision" else "new_passage",
            "language": draft["language"], "topic": topic,
            "topic_description": vocabulary["decision_criteria"]["intent"].get(topic, humanize(topic)),
            "member_questions": proposal.evidence["examples"], "approved_passages": passages}


def style_request(proposal: Proposal, vocabulary: dict, active: Catalogue) -> dict:
    """For a style gap the agent sees the situation, the kept questions, why answers were declined, the styles that can
    explain here and every approved pattern it may choose from. Styles change how an answer is explained, never its facts.
    The styles come from the active catalogue, so owner-created styles count; a pattern is offered only when no style that
    can already explain here uses its template."""
    situation, evidence = proposal.situation, proposal.evidence
    catalogue = {style.strategy_id: style for style in active.all()}
    represented = {catalogue[key].template_id for key in evidence["eligible_styles"] if key in catalogue}

    def describe(style) -> dict:
        return {"id": style.strategy_id, "name": style.name, "ends_with_check_back_question": style.teach_back}
    topic = situation["topic"]
    return {"task": "choose_style", "language": situation["language"], "topic": topic,
            "topic_description": vocabulary["decision_criteria"]["intent"].get(topic, humanize(topic)),
            "journey_stage": situation["journey_state"], "channel": situation["channel"],
            "accessibility_mode": situation["accessibility_mode"], "member_questions": evidence["examples"],
            "decline_reasons": evidence["decline_reasons"], "review_issues": evidence["review_issues"],
            "styles_that_can_explain_here": [describe(catalogue[key]) for key in evidence["eligible_styles"] if key in catalogue],
            "approved_patterns": [describe(style) for style in patterns()
                                  if style.status == "active" and style.template_id not in represented]}


def _own_fields(raw, own: set, other: set) -> dict:
    """The agent returns one closed shape for every task; fields of the other task must be empty and are dropped."""
    if not isinstance(raw, dict) or not own <= set(raw) <= own | other:
        raise ContractError("the drafting agent returned an unexpected shape")
    if any(raw[key] not in ("", [], None) for key in set(raw) & other):
        raise ContractError("the drafting agent filled fields that belong to another task")
    return {key: raw[key] for key in own}


def accept_style_suggestion(raw, request: dict, agent: str) -> dict:
    """Admit a style suggestion only if it is closed, names an approved pattern, holds no placeholder, steering, member
    figure or copied question. It stays a proposal: an owner decides, and only an approved pattern can be released."""
    fields = _own_fields(raw, STYLE_SUGGESTION_FIELDS - CODE_SET, CONTENT_ONLY_FIELDS)
    context = [item["id"] for item in request["styles_that_can_explain_here"]]
    suggestion = validate_style_suggestion({**fields, "agent": agent, "context": context})
    if suggestion["fit"] == "existing" and suggestion["pattern_id"] not in {item["id"] for item in request["approved_patterns"]}:
        raise ContractError("a style on a pattern that already explains here adds no alternative")
    written = (suggestion["name"], suggestion["why"], suggestion["new_style_name"], suggestion["new_style_idea"],
               *suggestion["themes"])
    for text in written:
        if "{{" in text or "}}" in text or response_steers_choice(text) or STEERING_EXTRA.search(text):
            raise ContractError("a suggestion may not hold value placeholders or steer a choice")
    if _figures(*request["member_questions"]) & _figures(*written):
        raise ContractError("a suggestion may not carry a figure from a member's question")
    copied = set().union(*(_shingles(text) for text in written))
    if any(_shingles(question) & copied for question in request["member_questions"]):
        raise ContractError("a suggestion may not copy a member's question")
    return suggestion


def _figures(*texts: str) -> set[str]:
    return {token.replace(",", ".") for text in texts for token in FIGURE.findall(text)}


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-zà-ÿ]{4,}", text.lower())}


def _shingles(text: str, size: int = 5) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[index:index + size]) for index in range(len(words) - size + 1)}


def accept_suggestion(raw, request: dict, agent: str) -> dict:
    """Admit a drafted suggestion only if it is closed, free of placeholders, steering and member carry-over, and lists
    its claims. Facts and figures are the owner's to verify against a recorded source; this does not vouch for them."""
    if isinstance(raw, dict) and "fit" in raw:
        raw = _own_fields(raw, SUGGESTION_FIELDS - CODE_SET, STYLE_FIELDS)
    elif not isinstance(raw, dict) or set(raw) != SUGGESTION_FIELDS - CODE_SET:
        raise ContractError("the drafting agent returned an unexpected shape")
    passages = {item["id"]: item for item in request["approved_passages"]}
    suggestion = validate_suggestion({**raw, "agent": agent, "context": list(passages)})
    if not set(suggestion["sources"]) <= set(passages):
        raise ContractError("the suggestion cites a passage it was not given")
    written = (suggestion["title"], suggestion["answers"], suggestion["body"], *suggestion["claims"], *suggestion["gaps"],
               *suggestion["themes"], *suggestion["coverage"])
    for text in written:
        if "{{" in text or "}}" in text or response_steers_choice(text) or STEERING_EXTRA.search(text):
            raise ContractError("a suggestion may not hold value placeholders or steer a choice")
    # A figure a member typed is that member's own value (B1/B2): it may not travel into content unless a passage has it.
    carried = _figures(*request["member_questions"]) - _figures(*(item["body"] for item in passages.values()))
    if carried & _figures(*written):
        raise ContractError("a suggestion may not carry a figure from a member's question")
    body = suggestion["body"].strip()
    if body and not suggestion["claims"]:
        raise ContractError("a drafted passage must list the claims the owner has to verify")
    if _figures(body) - _figures(*suggestion["claims"]):
        raise ContractError("every figure in a drafted passage must appear in a listed claim")
    if not body and not suggestion["gaps"]:
        raise ContractError("a suggestion without text must say what the owner has to supply")
    copied = set().union(*(_shingles(text) for text in written))
    if any(_shingles(question) & copied for question in request["member_questions"]):
        raise ContractError("a suggestion may not copy a member's question")
    return suggestion


# ----- the batch -----
def _share(part: int, whole: int) -> float:
    return round(part / whole, 2) if whole else 0.0


def cases(items: list[Signal]) -> list[dict]:
    """The newest declined interactions, one per journey, with what is known about each. The owner can open the journey."""
    seen: dict[str, dict] = {}
    for item in sorted(items, key=lambda signal: (signal.created_at, signal.journey_id), reverse=True):
        seen.setdefault(item.journey_id, {
            "journey_id": item.journey_id, "created_at": item.created_at, "outcome": item.outcome,
            "decline_reason": item.decline_reason, "review_issues": list(item.review_issues),
            "passages": list(item.content_versions)[:TOP_CONTENT], "question": " ".join(item.question.split())[:QUESTION_CHARS]})
    return list(seen.values())[:SAMPLE_CASES]


def question_deadline(created_at: str) -> str:
    """A kept question lives 30 days from its capture, which follows its response record by moments."""
    return (datetime.fromisoformat(created_at) + timedelta(days=QUESTION_RETENTION_DAYS)).isoformat(timespec="seconds")


def questions_expire_at(items: list[Signal]) -> str:
    return min((question_deadline(item.created_at) for item in items if item.question), default="")


def redacted(proposal: Proposal, now: datetime) -> Proposal | None:
    """The proposal without member wording once its oldest kept question is past the original 30 days, else None."""
    evidence = proposal.evidence
    if not evidence["examples"] and not any(case["question"] for case in evidence["cases"]):
        return None
    deadline = evidence.get("questions_expire_at") or question_deadline(min(
        (case["created_at"] for case in evidence["cases"] if case["question"]), default=proposal.created_at))
    if now < datetime.fromisoformat(deadline):
        return None
    fields = {key: value for key, value in asdict(proposal).items() if key != "content_digest"}
    fields["evidence"] = {**evidence, "examples": [], "questions_expire_at": "",
                          "cases": [{**case, "question": ""} for case in evidence["cases"]]}
    return sealed(fields)


def examples(items: list[Signal]) -> list[str]:
    """Up to five distinct kept questions, newest first, for the owner to read; never more than the first 300 characters."""
    seen: dict[str, str] = {}
    for item in sorted(items, key=lambda signal: (signal.created_at, signal.journey_id), reverse=True):
        text = " ".join(item.question.split())[:QUESTION_CHARS]
        if text and text.lower() not in seen:
            seen[text.lower()] = text
    return list(seen.values())[:SAMPLE_QUESTIONS]


def _evidence(items: list[Signal], escalated: list[Signal], eligible: list[str], window: tuple[str, str]) -> Evidence:
    declined = len(items)
    unanswered = sum(item.outcome == "unanswered" for item in items)
    no_style = sum(item.outcome == "no_style" for item in items)
    versions = Counter(version for item in items for version in item.content_versions)
    issues = Counter(issue for item in items for issue in item.review_issues)
    reasons = Counter(item.decline_reason for item in (*items, *escalated) if item.decline_reason)
    journeys = {item.journey_id for item in (*items, *escalated)}
    sample = sorted({item.journey_id: item.created_at for item in items}.items(), key=lambda row: (row[1], row[0]), reverse=True)
    return Evidence(
        window_start=window[0], window_end=window[1], declined=declined, unanswered=unanswered,
        review_flagged=declined - unanswered - no_style, no_style=no_style, escalated=len(escalated),
        journeys=len(journeys),
        content_retrieved_share=_share(sum(bool(item.content_versions) for item in items), declined),
        review_issues=dict(sorted(issues.items())), decline_reasons=dict(sorted(reasons.items())),
        content_versions=dict(sorted(versions.most_common(TOP_CONTENT), key=lambda row: (-row[1], row[0]))),
        eligible_styles=eligible, sample_journey_ids=[row[0] for row in sample[:SAMPLE_JOURNEYS]],
        examples=examples(items), cases=cases(items), questions_expire_at=questions_expire_at(items))


def _strategy_draft(catalogue: Catalogue, state: str, channel: str, mode: str, eligible: list[str]) -> dict | None:
    if state not in stages():
        return None
    candidates = [style for style in patterns() if style.ranked_for_resolution and style.status == "active"]
    # A different pattern from the styles that already explain here, so the new style adds a way to explain.
    candidates = [style for style in candidates if style.strategy_id not in eligible] or candidates
    if not candidates:
        return None

    def fit(style) -> tuple[int, str]:
        matches = (("*" in style.eligible_journey_states or state in style.eligible_journey_states)
                   + (channel in style.channels) + (mode in style.accessibility_modes))
        return -matches, style.strategy_id

    style = min(candidates, key=fit)
    # Scoped to the situation that has the gap, not to everything the pattern could cover: the smallest change.
    draft = {
        "action": "add", "name": f"{style.name} ({humanize(state)}, {humanize(channel)}, {humanize(mode)})"[:120],
        "pattern_id": style.strategy_id, "eligible_journey_states": [state], "channels": [channel],
        "accessibility_modes": [mode],
    }
    try:
        StyleChange.from_dict(draft)
    except ContractError:
        return None
    return draft


def review(records: list[ExplainabilityRecord], *, catalogue: Catalogue, corpus: list[ContentDocument], vocabulary: dict,
           excluded_journeys: set[str] = frozenset(), since: str = "", now: datetime | None = None,
           min_declined: int = MIN_DECLINED, min_journeys: int = MIN_JOURNEYS,
           questions: dict[str, str] | None = None) -> tuple[list[Proposal], dict]:
    """Propose improvements for every situation whose declined interactions reach the thresholds."""
    now = now or datetime.now(timezone.utc)
    found, excluded = signals(records, excluded_journeys=excluded_journeys, since=since, questions=questions)
    clusters: dict[tuple, list[Signal]] = {}
    for signal in found:
        clusters.setdefault(signal.situation, []).append(signal)
    window = (since or min((item.created_at for item in found), default=""), now.isoformat(timespec="seconds"))
    descriptions = vocabulary["decision_criteria"]["intent"]
    batch, created = batch_id(now), now.isoformat(timespec="seconds")
    proposals: list[Proposal] = []

    def add(kind: str, situation: dict, evidence: Evidence, rationale: str, draft: dict) -> None:
        proposals.append(sealed({
            "proposal_id": proposal_id(kind, situation, draft.get("base_id", "")), "kind": kind, "batch_id": batch, "created_at": created,
            "rules_version": RULES_VERSION, "situation": situation, "evidence": asdict(evidence), "rationale": rationale,
            "draft": draft, "evidence_digest": digest_of(evidence, draft), "watermark": WATERMARK}))

    for (state, topic, channel, mode, language), items in sorted(clusters.items()):
        declined = [item for item in items if item.outcome != "escalated"]
        escalated = [item for item in items if item.outcome == "escalated"]
        if len(declined) < min_declined or len({item.journey_id for item in declined}) < min_journeys:
            continue
        situation = {"journey_state": state, "topic": topic, "channel": channel, "accessibility_mode": mode,
                     "language": language}
        key = RequestKey(state, topic, channel, mode)
        eligible = sorted(style.strategy_id for style in catalogue.rankable_for(key))
        evidence = _evidence(declined, escalated, eligible, window)
        where = f"{humanize(topic)} questions at {humanize(state)} over {humanize(channel)} ({humanize(mode)}, {language})"
        counts = (f"{evidence.declined} declined interactions from {evidence.journeys} journeys "
                  f"({evidence.unanswered} unanswered, {evidence.review_flagged} flagged by the answer review, "
                  f"{evidence.no_style} with no style able to explain)")
        description = descriptions.get(topic, humanize(topic))
        without_content = 1 - evidence.content_retrieved_share
        if without_content >= MAJORITY:
            add("content_gap", situation, evidence,
                f"{counts} for {where}. In {round(without_content * 100)}% no approved passage was retrieved, so approved "
                "content may be missing for this question. An owner must write and approve the passage.",
                {"action": "add", "title": humanize(topic).capitalize(), "language": language, "journey_states": [state],
                 "answers": f"Questions about: {description}",
                 "related_content_ids": related_content(corpus, state, language, description)})
            continue
        if (len(eligible) <= 1 or evidence.no_style / len(declined) >= MAJORITY) and (
                draft := _strategy_draft(catalogue, state, channel, mode, eligible)) is not None:
            reason = (f"In {evidence.no_style} no eligible style was left to explain." if evidence.no_style / len(declined) >= MAJORITY
                      else f"Only {len(eligible)} approved style can explain here, so one refused draft leaves no alternative.")
            add("strategy_gap", situation, evidence,
                f"{counts} for {where}. {reason} A new style on an approved pattern would add cover.", draft)
        content_flagged = sum(bool(CONTENT_ISSUES & set(item.review_issues)) for item in declined)
        if content_flagged / len(declined) >= MAJORITY:
            base = next((doc for version_id, _ in evidence.content_versions.items() for doc in corpus
                         if doc.id == version_id and doc.status == "approved" and doc.language == language), None)
            if base is not None:
                add("content_revision", situation, evidence,
                    f"{counts} for {where}. In {content_flagged} the review found the answer not grounded in the approved "
                    f"passage {base.id}. The passage may need a new version that covers this question.",
                    {"action": "revise", "base_id": base.id, "content_id": base.content_id, "base_version": base.version,
                     "title": base.title, "language": language, "journey_states": list(base.journey_states),
                     "answers": base.answers})
                continue
        quality = sum(bool(item.review_issues) and set(item.review_issues) <= QUALITY_ISSUES for item in declined)
        gate_blocked = sum(item.outcome == "unanswered" and not item.review_issues
                           and item.decline_reason in {"", "output_gate"} for item in declined)
        if (quality + gate_blocked) / len(declined) >= MAJORITY:
            focus = "answer_review" if quality >= gate_blocked else "output_gate"
            issues = sorted(evidence.review_issues)
            add("false_negative_review", situation, evidence,
                f"{counts} for {where}. Approved content was found and no content problem was reported, so these answers "
                "may have been refused although they were legitimate. Review a few examples before changing anything.",
                {"action": "review", "focus": focus, "issues": issues})
    summary = {"batch_id": batch, "created_at": created, "rules_version": RULES_VERSION, "watermark": WATERMARK,
               "records_read": len(records), "declined_signals": len(found), "test_records_excluded": excluded,
               "situations": len(clusters), "proposals": len(proposals), "window_start": window[0], "window_end": window[1],
               "questions_read": 0, "suggestions_drafted": 0,
               "current": {item.proposal_id: item.evidence_digest for item in proposals}}
    return proposals, summary
