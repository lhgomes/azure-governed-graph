"""Batch review of declined interactions: reference-only improvement proposals for human review.

A member question that reached no explanation, or whose answer a review flagged, can be a false negative of the approved
content and strategies, or point to approved content or a strategy that does not exist yet. This module groups those
interactions by closed situation and drafts proposals for a human owner.

Boundaries:
- B4: the only input is the immutable response record (closed fields, no utterance, no draft, no member value). No
  conversation text reaches a proposal, so nothing here can change a prompt, template, model or catalogue.
- B7: the situation holds journey stage, topic, channel, accessibility mode and language only; no demographic field exists.
- B3/B8: a proposal is inert. Strategy drafts reuse the approved pattern library through ``StyleChange``; content drafts
  carry no body. Activation needs an owner decision (Tier 2) through the existing governed publication paths.
- B1/B2: nothing is calculated or written as a member value; drafts contain no figures.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from governedgraph.common.contracts import (
    ACCESSIBILITY_MODES, CHANNELS, RESPONSE_LANGUAGES, REVIEW_ISSUES, ContractError, ExplainabilityRecord, RequestKey, _strict,
)
from governedgraph.common.io import canonical_json
from governedgraph.guardrails.advice_intent import response_steers_choice
from governedgraph.search.corpus import ContentDocument, approve_version
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY, Catalogue
from governedgraph.strategy_service.catalogue_release import StyleChange, patterns, stages

WATERMARK = "ILLUSTRATIVE - synthetic data"
RULES_VERSION = "1.0"
KINDS = ("content_gap", "content_revision", "strategy_gap", "false_negative_review")
OUTCOMES = ("unanswered", "review_flagged", "escalated")
# Review findings that say the approved passage itself is incomplete or missing, versus a style or relevance defect.
CONTENT_ISSUES = frozenset({"grounding", "document_context", "uncertainty"})
QUALITY_ISSUES = frozenset({"relevance", "strategy"})
MIN_DECLINED = 5
MIN_JOURNEYS = 3
MAJORITY = 0.5
SAMPLE_JOURNEYS = 5
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
        outcome = "escalated"
    elif record.response_kind == "explanation" and issues:
        outcome = "review_flagged"
    else:
        return None
    key = record.request_key
    return Signal(record.journey_id, record.created_at, key["journey_state"], intent["reason"], key["channel"],
                  key["accessibility_mode"], intent["response_language"], outcome, tuple(issues),
                  tuple(sorted(set(record.content_versions))))


def signals(records: list[ExplainabilityRecord], *, excluded_journeys: set[str] = frozenset(),
            since: str = "") -> tuple[list[Signal], int]:
    """Declined interactions of non-test journeys at or after ``since``; also the count of excluded test records."""
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
        result.append(signal)
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
    escalated: int
    journeys: int
    content_retrieved_share: float
    review_issues: dict
    content_versions: dict
    eligible_styles: list
    sample_journey_ids: list

    def __post_init__(self):
        counts = (self.declined, self.unanswered, self.review_flagged, self.escalated, self.journeys)
        if (any(type(item) is not int or item < 0 for item in counts)
                or self.declined != self.unanswered + self.review_flagged or self.journeys > self.declined + self.escalated
                or not isinstance(self.content_retrieved_share, float) or not 0 <= self.content_retrieved_share <= 1
                or any(not isinstance(item, str) or len(item) > 40 for item in (self.window_start, self.window_end))):
            raise ContractError("invalid evidence counts")
        _count_map(self.review_issues, set(REVIEW_ISSUES))
        _count_map(self.content_versions, limit=TOP_CONTENT)
        if (not isinstance(self.eligible_styles, list) or len(self.eligible_styles) > 50
                or any(not isinstance(item, str) or not CONTENT_REF.fullmatch(item) for item in self.eligible_styles)
                or not isinstance(self.sample_journey_ids, list) or len(self.sample_journey_ids) > SAMPLE_JOURNEYS
                or any(not isinstance(item, str) or not JOURNEY_ID.fullmatch(item) for item in self.sample_journey_ids)):
            raise ContractError("invalid evidence references")

    @classmethod
    def from_dict(cls, payload: dict) -> Evidence:
        return _strict(cls, payload)

    def digest_view(self) -> dict:
        view = asdict(self)
        for volatile in ("window_start", "window_end", "sample_journey_ids"):
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
    if set(draft) != shapes[kind]:
        raise ContractError("unexpected draft fields")
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

    @classmethod
    def from_dict(cls, payload: dict) -> Proposal:
        return _strict(cls, payload)

    @property
    def blob_name(self) -> str:
        return f"proposals/{self.proposal_id}/{self.evidence_digest}.json"

    def encode(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True).encode()


def digest_of(evidence: Evidence, draft: dict) -> str:
    return hashlib.sha256(canonical_json({"evidence": evidence.digest_view(), "draft": draft})).hexdigest()


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


# ----- the batch -----
def _share(part: int, whole: int) -> float:
    return round(part / whole, 2) if whole else 0.0


def _evidence(items: list[Signal], escalated: list[Signal], eligible: list[str], window: tuple[str, str]) -> Evidence:
    declined = len(items)
    unanswered = sum(item.outcome == "unanswered" for item in items)
    versions = Counter(version for item in items for version in item.content_versions)
    issues = Counter(issue for item in items for issue in item.review_issues)
    journeys = {item.journey_id for item in (*items, *escalated)}
    sample = sorted({item.journey_id: item.created_at for item in items}.items(), key=lambda row: (row[1], row[0]), reverse=True)
    return Evidence(
        window_start=window[0], window_end=window[1], declined=declined, unanswered=unanswered,
        review_flagged=declined - unanswered, escalated=len(escalated), journeys=len(journeys),
        content_retrieved_share=_share(sum(bool(item.content_versions) for item in items), declined),
        review_issues=dict(sorted(issues.items())),
        content_versions=dict(sorted(versions.most_common(TOP_CONTENT), key=lambda row: (-row[1], row[0]))),
        eligible_styles=eligible, sample_journey_ids=[row[0] for row in sample[:SAMPLE_JOURNEYS]])


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
           min_declined: int = MIN_DECLINED, min_journeys: int = MIN_JOURNEYS) -> tuple[list[Proposal], dict]:
    """Propose improvements for every situation whose declined interactions reach the thresholds."""
    now = now or datetime.now(timezone.utc)
    found, excluded = signals(records, excluded_journeys=excluded_journeys, since=since)
    clusters: dict[tuple, list[Signal]] = {}
    for signal in found:
        clusters.setdefault(signal.situation, []).append(signal)
    window = (since or min((item.created_at for item in found), default=""), now.isoformat(timespec="seconds"))
    descriptions = vocabulary["decision_criteria"]["intent"]
    batch, created = batch_id(now), now.isoformat(timespec="seconds")
    proposals: list[Proposal] = []

    def add(kind: str, situation: dict, evidence: Evidence, rationale: str, draft: dict) -> None:
        proposals.append(Proposal(proposal_id(kind, situation), kind, batch, created, RULES_VERSION, situation,
                                  asdict(evidence), rationale, draft, digest_of(evidence, draft), WATERMARK))

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
                  f"({evidence.unanswered} unanswered, {evidence.review_flagged} flagged by the answer review)")
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
        if len(eligible) <= 1 and (draft := _strategy_draft(catalogue, state, channel, mode)) is not None:
            add("strategy_gap", situation, evidence,
                f"{counts} for {where}. Only {len(eligible)} approved style can explain here, so one refused draft leaves "
                "no alternative. A new style on an approved pattern would add cover.", draft)
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
        gate_blocked = sum(item.outcome == "unanswered" and not item.review_issues for item in declined)
        if (quality + gate_blocked) / len(declined) >= MAJORITY:
            focus = "answer_review" if quality >= gate_blocked else "output_gate"
            issues = sorted(evidence.review_issues)
            add("false_negative_review", situation, evidence,
                f"{counts} for {where}. Approved content was found and no content problem was reported, so these answers "
                "may have been refused although they were legitimate. Review a few examples before changing anything.",
                {"action": "review", "focus": focus, "issues": issues})
    summary = {"batch_id": batch, "created_at": created, "rules_version": RULES_VERSION, "watermark": WATERMARK,
               "records_read": len(records), "declined_signals": len(found), "test_records_excluded": excluded,
               "situations": len(clusters), "proposals": len(proposals), "window_start": window[0], "window_end": window[1]}
    return proposals, summary
