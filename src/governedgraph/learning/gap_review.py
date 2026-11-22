"""Batch review of declined interactions: reference-only improvement proposals for human review.

A member question that reached no explanation, or whose answer a review flagged, can be a false negative of the approved
content and strategies, or point to approved content or a strategy that does not exist yet. This module groups those
interactions by closed situation and drafts proposals for a human owner.

Boundaries:
- B4 (owner decision IMP-GAP-REVIEW-2): the inputs are the immutable response record (closed fields) and the member's
  declined question, kept briefly by ``explainability.declined_questions``. A few questions appear as examples inside a
  proposal and reach a drafting agent; nothing reaches a prompt, template, model or catalogue without an owner approving
  a passage or style, and every drafted statement is grounded in approved passages or refused.
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
from datetime import datetime, timezone

from governedgraph.common.contracts import (
    ACCESSIBILITY_MODES, CHANNELS, DECLINE_REASONS, RESPONSE_LANGUAGES, REVIEW_ISSUES, ContractError, ExplainabilityRecord,
    RequestKey, _strict,
)
from governedgraph.common.io import canonical_json
from governedgraph.guardrails.advice_intent import response_steers_choice
from governedgraph.search.corpus import ContentDocument, approve_version
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY, Catalogue
from governedgraph.strategy_service.catalogue_release import StyleChange, patterns, stages

WATERMARK = "ILLUSTRATIVE - synthetic data"
RULES_VERSION = "1.0"
KINDS = ("content_gap", "content_revision", "strategy_gap", "false_negative_review")
OUTCOMES = ("unanswered", "review_flagged", "no_style", "escalated")
# Review findings that say the approved passage itself is incomplete or missing, versus a style or relevance defect.
CONTENT_ISSUES = frozenset({"grounding", "document_context", "uncertainty"})
QUALITY_ISSUES = frozenset({"relevance", "strategy"})
MIN_DECLINED = 5
MIN_JOURNEYS = 3
MAJORITY = 0.5
SAMPLE_JOURNEYS = 5
SAMPLE_QUESTIONS = 5
QUESTION_CHARS = 300
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
    issues = sorted({issue for review in reviews if review["verdict"] != "pass" for issue in review["issues"]})
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

    def __post_init__(self):
        counts = (self.declined, self.unanswered, self.review_flagged, self.no_style, self.escalated, self.journeys)
        if (any(type(item) is not int or item < 0 for item in counts)
                or self.declined != self.unanswered + self.review_flagged + self.no_style
                or self.journeys > self.declined + self.escalated
                or not isinstance(self.content_retrieved_share, float) or not 0 <= self.content_retrieved_share <= 1
                or any(not isinstance(item, str) or len(item) > 40 for item in (self.window_start, self.window_end))):
            raise ContractError("invalid evidence counts")
        _count_map(self.review_issues, set(REVIEW_ISSUES))
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

    @classmethod
    def from_dict(cls, payload: dict) -> Evidence:
        return _strict(cls, payload)

    def digest_view(self) -> dict:
        view = asdict(self)
        for volatile in ("window_start", "window_end", "sample_journey_ids", "examples"):
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
    suggested = draft.get("suggested") if kind in {"content_gap", "content_revision"} else None
    if set(draft) - {"suggested"} != shapes[kind] or ("suggested" in draft and suggested is None):
        raise ContractError("unexpected draft fields")
    if suggested is not None:
        validate_suggestion(suggested)
    if kind == "strategy_gap":
        StyleChange.from_dict(draft)
    elif kind == "false_negative_review":
        if (draft["action"] != "review" or draft["focus"] not in {"output_gate", "answer_review"}
                or not isinstance(draft["issues"], list) or not set(draft["issues"]) <= set(REVIEW_ISSUES)):
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


SUGGESTION_FIELDS = {"title", "answers", "body", "sources", "gaps", "agent"}


def validate_suggestion(value) -> dict:
    if (not isinstance(value, dict) or set(value) != SUGGESTION_FIELDS or not isinstance(value["agent"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}:[0-9]{1,6}", value["agent"])):
        raise ContractError("invalid drafted suggestion")
    _text(value["title"], 200)
    _text(value["answers"], 1000, allow_empty=True)
    _text(value["body"], 8000, allow_empty=True)
    for key in ("sources", "gaps"):
        if (not isinstance(value[key], list) or len(value[key]) > 10
                or any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in value[key])):
            raise ContractError("invalid drafted suggestion lists")
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


def proposal_id(kind: str, situation: dict) -> str:
    identity = "|".join((RULES_VERSION, kind, *(situation[key] for key in (
        "journey_state", "topic", "channel", "accessibility_mode", "language"))))
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


def version_key(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d{1,4}(\.\d{1,4}){1,2}", version):
        raise ContractError("content versions are numeric, such as 1.4")
    return tuple(int(part) for part in version.split("."))


def next_version(version: str) -> str:
    major, minor = (version_key(version) + (0,))[:2]
    return f"{major}.{minor + 1}"


def next_content_id(corpus: list[ContentDocument]) -> str:
    used = [int(match[1]) for doc in corpus if (match := re.fullmatch(re.escape(NEW_CONTENT_PREFIX) + r"(\d{3,6})", doc.content_id))]
    return f"{NEW_CONTENT_PREFIX}{max(used, default=0) + 1:03d}"


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{4,}", text.lower())}


def related_content(corpus: list[ContentDocument], state: str, language: str, description: str) -> list[str]:
    wanted = _words(description)
    ranked = sorted(((len(wanted & _words(doc.title + " " + doc.answers)), doc.id)
                     for doc in approved_for(corpus, state, language)), key=lambda item: (-item[0], item[1]))
    return [doc_id for score, doc_id in ranked if score][:3]


def content_document(*, draft: dict, content: dict, corpus: list[ContentDocument], approval_ref: str) -> ContentDocument:
    """Build the approved document an owner decided on; raises ContractError when it breaks a content rule."""
    if set(content) != {"title", "journey_states", "answers", "body"}:
        raise ContractError("content needs a title, stages, questions answered and a body")
    body = _text(content["body"], 8000)
    # Title and questions answered are indexed and searched like the body, so they follow the same rules.
    for text in (body, _text(content["title"], 200), _text(content["answers"], 1000, allow_empty=True)):
        if "{{" in text or "}}" in text or response_steers_choice(text):
            raise ContractError("content may not hold value placeholders or steer a choice")
    states = content["journey_states"]
    if not isinstance(states, list) or not states or any(not isinstance(item, str) or item not in stages() | {"*"} for item in states):
        raise ContractError("choose approved journey stages")
    if draft["action"] == "add":
        content_id, version = next_content_id(corpus), "1.0"
    else:
        base = next((doc for doc in corpus if doc.id == draft["base_id"] and doc.status == "approved"), None)
        if base is None:
            raise ContractError("the passage changed since this proposal; refresh before deciding")
        if (_text(content["body"], 8000).strip() == base.body.strip()
                and _text(content["answers"], 1000, allow_empty=True) == base.answers and content["title"] == base.title
                and list(states) == list(base.journey_states)):
            raise ContractError("a new version must change the passage")
        content_id, version = base.content_id, next_version(base.version)
    return ContentDocument(id=f"{content_id}-v{version}", content_id=content_id, version=version, status="approved",
                    title=_text(content["title"], 200).strip(), language=draft["language"], journey_states=list(states),
                    approval_ref=approval_ref, body=body.strip(),
                    watermark="ILLUSTRATIVE - synthetic content, not legal or financial information",
                    answers=_text(content["answers"], 1000, allow_empty=True).strip())


# ----- drafting with an agent: closed request, grounded and bounded answer -----
FIGURE = re.compile(r"\d+(?:[.,]\d+)*")
# Numbers written as words are figures too. "one" and "een" are left out: they are ordinary articles.
NUMBER_WORDS = frozenset(
    "two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen "
    "twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million percent half double triple "
    "twee drie vier vijf zes zeven acht negen tien elf twaalf dertien veertien vijftien zestien zeventien achttien negentien "
    "twintig dertig veertig vijftig zestig zeventig tachtig negentig honderd duizend miljoen procent helft dubbel".split())
# The shared steering check is narrow and covers one language; suggestions also get this wider two-language screen.
STEERING_EXTRA = re.compile(
    r"\byou (?:should|ought to|must|need to|had better)\b|\bi (?:would )?(?:suggest|recommend|advise)\b|"
    r"\bthe (?:best|better|right) (?:option|choice)\b|\bwe (?:advise|recommend)\b|"
    r"\bu (?:moet|moeten|dient|zou moeten|kunt het beste)\b|\bje (?:moet|zou moeten|kunt het beste)\b|"
    r"\bik (?:raad|adviseer|beveel)\b|\bde (?:beste|betere|juiste) (?:optie|keuze)\b|\braden wij\b", re.IGNORECASE)


def drafting_request(proposal: Proposal, corpus: list[ContentDocument], vocabulary: dict) -> dict | None:
    """What the drafting agent may see: the situation, the kept example questions and approved passages. Nothing else."""
    if proposal.kind not in {"content_gap", "content_revision"} or not proposal.evidence["examples"]:
        return None
    draft = proposal.draft
    wanted = [draft["base_id"]] if proposal.kind == "content_revision" else draft["related_content_ids"]
    passages = [{"id": doc.id, "title": doc.title, "body": doc.body} for doc in corpus
                if doc.id in wanted and doc.status == "approved"]
    topic = proposal.situation["topic"]
    return {"task": "revise_passage" if proposal.kind == "content_revision" else "new_passage",
            "language": draft["language"], "topic": topic,
            "topic_description": vocabulary["decision_criteria"]["intent"].get(topic, humanize(topic)),
            "member_questions": proposal.evidence["examples"], "approved_passages": passages}


def _figures(*texts: str) -> set[str]:
    found = {token.replace(",", ".") for text in texts for token in FIGURE.findall(text)}
    return found | {word for text in texts for word in re.findall(r"[a-zà-ÿ]+", text.lower()) if word in NUMBER_WORDS}


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?;])\s+|\n+", text) if item.strip()]


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-zà-ÿ]{4,}", text.lower())}


def _shingles(text: str, size: int = 5) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[index:index + size]) for index in range(len(words) - size + 1)}


def _sentence_grounded(sentence: str, sources: list[str]) -> bool:
    """A sentence holding a figure must be close to one source sentence that holds the same figures, not just any text."""
    figures = _figures(sentence)
    if not figures:
        return True
    mine = _words(sentence)
    for source in sources:
        for candidate in _sentences(source):
            if figures <= _figures(candidate) and len(mine & _words(candidate)) >= 0.6 * len(mine):
                return True
    return False


def accept_suggestion(raw, request: dict, agent: str) -> dict:
    """Admit a drafted suggestion only if it is closed, rule-abiding and grounded; otherwise raise ContractError."""
    if not isinstance(raw, dict) or set(raw) != SUGGESTION_FIELDS - {"agent"}:
        raise ContractError("the drafting agent returned an unexpected shape")
    suggestion = validate_suggestion({**raw, "agent": agent})
    passages = {item["id"]: item for item in request["approved_passages"]}
    if not set(suggestion["sources"]) <= set(passages):
        raise ContractError("the suggestion cites a passage it was not given")
    written = (suggestion["title"], suggestion["answers"], suggestion["body"], *suggestion["gaps"])
    for text in written:
        if "{{" in text or "}}" in text or response_steers_choice(text) or STEERING_EXTRA.search(text):
            raise ContractError("a suggestion may not hold value placeholders or steer a choice")
    texts = [item["body"] + " " + item["title"] for item in passages.values()]
    if _figures(*written) - _figures(*texts):
        raise ContractError("a suggestion may not contain a figure that no source passage contains")
    if any(not _sentence_grounded(sentence, texts) for text in written for sentence in _sentences(text)):
        raise ContractError("a sentence with a figure must stay close to a source sentence with the same figures")
    if suggestion["body"].strip() and not suggestion["sources"]:
        raise ContractError("a suggested passage must cite the approved passages it is grounded in")
    if not suggestion["body"].strip() and not suggestion["gaps"]:
        raise ContractError("a suggestion without text must say what the owner has to supply")
    copied = set().union(*(_shingles(text) for text in written))
    if any(_shingles(question) & copied for question in request["member_questions"]):
        raise ContractError("a suggestion may not copy a member's question")
    return suggestion


# ----- the batch -----
def _share(part: int, whole: int) -> float:
    return round(part / whole, 2) if whole else 0.0


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
        examples=examples(items))


def _strategy_draft(catalogue: Catalogue, state: str, channel: str, mode: str) -> dict | None:
    if state not in stages():
        return None
    candidates = [style for style in patterns() if style.ranked_for_resolution and style.status == "active"]
    if not candidates:
        return None

    def fit(style) -> tuple[int, str]:
        matches = (("*" in style.eligible_journey_states or state in style.eligible_journey_states)
                   + (channel in style.channels) + (mode in style.accessibility_modes))
        return -matches, style.strategy_id

    style = min(candidates, key=fit)
    open_stages = [item for item in style.eligible_journey_states if item != "*"] or sorted(stages())
    draft = {
        "action": "add", "name": f"{style.name} ({humanize(state)}, {humanize(channel)}, {humanize(mode)})"[:120],
        "pattern_id": style.strategy_id,
        "eligible_journey_states": sorted({*open_stages, state}),
        "channels": sorted({*style.channels, channel}),
        "accessibility_modes": sorted({*style.accessibility_modes, mode}),
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
            "proposal_id": proposal_id(kind, situation), "kind": kind, "batch_id": batch, "created_at": created,
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
                draft := _strategy_draft(catalogue, state, channel, mode)) is not None:
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
               "questions_read": 0, "suggestions_drafted": 0}
    return proposals, summary
