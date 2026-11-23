"""Domain pack accessors: prompts, closed vocabularies and benchmark questions come from the pack.

The pack is the directory named by GOVERNEDGRAPH_DATA_DIR. Nothing domain-specific lives in framework code.
Loading fails closed: a malformed or incomplete pack raises instead of falling back to permissive defaults.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

from governedgraph.common.config import DATA_DIR
from governedgraph.common.io import load_json

PROMPT_NAMES = frozenset({"counsellor"})
# The closed outcomes of the answer-review question, and the review issue each non-pass outcome records.
REVIEW_LABEL_ISSUES = {"unsupported": "grounding", "advice": "advice", "off_target": "relevance"}
REVIEW_LABELS = frozenset({"pass_draft", *REVIEW_LABEL_ISSUES})
# What a customer message asks for, and the framework routing decision each non-information label selects.
# `asks_information` is resolved by the topic question; an unclear reading is a clarification.
ROUTE_LABEL_DECISIONS = {
    "asks_person": ("human", "unclassified", "explicit_human_request"),
    "asks_choice": ("advice", "option_level_question", "personal_choice_or_comparison"),
    "only_acknowledges": ("comprehension", "unclassified", "restating_previous_explanation"),
}
CLARIFICATION_DECISION = ("clarification", "unclassified", "more_context_needed")
ROUTE_LABELS = frozenset({"asks_information", *ROUTE_LABEL_DECISIONS})
MESSAGE_KEYS = frozenset({
    "audit_unavailable", "advice_declined", "safety_declined", "component_failure", "conversation_limit",
    "handover_arranged", "clarify_question", "advice_escalation_summary", "journey_closed",
    "journey_unavailable", "audit_paused", "conversation_unavailable", "answer_unavailable",
})
DEMO_KEYS = frozenset({
    "member_ref", "other_member_ref", "trace_members", "persona", "persona_voices", "warmup_utterance",
    "proof_explanation_question", "proof_advice_question", "proof_injection_question",
    "proof_second_contact_question", "proof_recap_question",
})


class DomainPackError(RuntimeError):
    pass


def _strings(value, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(not isinstance(item, str) or not item for item in value) or len(set(value)) != len(value)):
        raise DomainPackError(f"domain vocabulary: {label} must be a list of unique non-empty strings")
    return tuple(value)


def _explanation_intents(raw: dict) -> tuple[list, list[str], tuple[str, ...]]:
    intents = raw.get("explanation_intents")
    if (not isinstance(intents, list) or not intents
            or any(not isinstance(row, list) or len(row) != 2 or not all(isinstance(x, str) and x for x in row)
                   for row in intents)):
        raise DomainPackError("domain vocabulary: explanation_intents must be [intent, reason] pairs")
    reasons = [row[1] for row in intents]
    if len(set(reasons)) != len(reasons):
        raise DomainPackError("domain vocabulary: explanation reasons must be unique")
    historical = _strings(raw.get("historical_reasons"), "historical_reasons", allow_empty=True)
    if not set(historical) <= set(reasons):
        raise DomainPackError("domain vocabulary: historical reasons must be declared reasons")
    return intents, reasons, historical


def _answer_goals(raw: dict, reasons: list[str], concepts: tuple[str, ...]) -> dict:
    goals = raw.get("answer_goals")
    if not isinstance(goals, dict) or not set(goals) <= set(reasons):
        raise DomainPackError("domain vocabulary: answer_goals must be keyed by declared reasons")
    parsed = {}
    for reason, entry in goals.items():
        if not isinstance(entry, dict) or set(entry) != {"goal", "excluded"} or not isinstance(entry["goal"], str):
            raise DomainPackError(f"domain vocabulary: answer goal {reason!r} must have goal and excluded")
        excluded = _strings(entry["excluded"], f"answer goal {reason!r} excluded", allow_empty=True)
        if not set(excluded) <= set(concepts):
            raise DomainPackError(f"domain vocabulary: answer goal {reason!r} excludes an unknown concept")
        parsed[reason] = (entry["goal"], list(excluded))
    return parsed


def _advice_patterns(raw: dict) -> tuple[str, ...]:
    patterns = _strings(raw.get("advice_patterns"), "advice_patterns", allow_empty=True)
    try:
        for pattern in patterns:
            re.compile(pattern)
    except re.error as error:
        raise DomainPackError("domain vocabulary: advice_patterns must be valid regular expressions") from error
    return patterns


def _decision_criteria(raw: dict, concepts: tuple[str, ...], reasons: list[str], historical: tuple[str, ...]) -> dict:
    criteria = raw.get("decision_criteria")
    if (not isinstance(criteria, dict) or set(criteria) != {"intent", "concepts", "review", "route"}
            or any(not isinstance(group, dict) or not group
                   or any(not isinstance(name, str) or not isinstance(text, str) or not text.strip()
                          for name, text in group.items())
                   for group in criteria.values())):
        raise DomainPackError(
            "domain vocabulary: decision_criteria must describe intent topics, concepts, review and route")
    if set(criteria["concepts"]) != set(concepts):
        raise DomainPackError("domain vocabulary: decision_criteria must describe exactly the explanation concepts")
    if set(criteria["review"]) != REVIEW_LABELS:
        raise DomainPackError(
            f"domain vocabulary: decision_criteria.review must describe exactly {sorted(REVIEW_LABELS)}")
    if set(criteria["route"]) != ROUTE_LABELS:
        raise DomainPackError(
            f"domain vocabulary: decision_criteria.route must describe exactly {sorted(ROUTE_LABELS)}")
    if set(criteria["intent"]) != set(reasons) - set(historical):
        raise DomainPackError("domain vocabulary: decision_criteria.intent must describe exactly the explanation topics")
    return criteria


def _messages(raw: dict) -> dict:
    messages = raw.get("messages")
    if (not isinstance(messages, dict) or set(messages) != MESSAGE_KEYS
            or any(not isinstance(text, str) or not text.strip() for text in messages.values())):
        raise DomainPackError(f"domain vocabulary: messages must provide exactly {sorted(MESSAGE_KEYS)}")
    return messages


def _demo_profile(raw: dict) -> dict:
    demo = raw.get("demo")
    if not isinstance(demo, dict) or set(demo) != DEMO_KEYS:
        raise DomainPackError(f"domain vocabulary: demo must provide exactly {sorted(DEMO_KEYS)}")
    voices = demo["persona_voices"]
    if (not isinstance(voices, dict) or "counsellor" not in voices or demo["persona"] not in voices
            or any(not isinstance(v, str) or not v for v in voices.values())):
        raise DomainPackError("domain vocabulary: demo persona_voices must name the counsellor and the demo persona")
    for name in ("member_ref", "other_member_ref", "warmup_utterance", "proof_explanation_question",
                 "proof_advice_question", "proof_injection_question", "proof_second_contact_question",
                 "proof_recap_question"):
        if not isinstance(demo[name], str) or not demo[name].strip():
            raise DomainPackError(f"domain vocabulary: demo {name} must be a non-empty string")
    trace_members = _strings(demo["trace_members"], "demo trace_members")
    return {**demo, "trace_members": frozenset(trace_members), "persona_voices": dict(voices)}


def _languages(raw: dict) -> tuple[frozenset[str], str]:
    languages = frozenset(_strings(raw.get("response_languages"), "response_languages"))
    default_language = raw.get("default_language")
    if default_language not in languages or "en" not in languages:
        raise DomainPackError("domain vocabulary: default_language must be a response language and 'en' must be supported")
    return languages, default_language


def _per_language(raw: dict, name: str, languages: frozenset[str], *, size: int | None, problem: str) -> dict:
    """`raw[name]` must hold a list of non-empty strings (exactly `size` of them, if given) for every language."""
    value = raw.get(name)
    if (not isinstance(value, dict) or set(value) != languages
            or any(not isinstance(items, list) or (len(items) != size if size else not items)
                   or not all(isinstance(item, str) and item.strip() for item in items)
                   for items in value.values())):
        raise DomainPackError(f"domain vocabulary: {problem}")
    return {language: tuple(items) for language, items in value.items()}


def _outcome_check(raw: dict, languages: frozenset[str]) -> tuple[dict, str | None]:
    outcome = raw.get("outcome_check")
    if outcome is None:
        # Legacy packs remain readable while the governed prompt feature is disabled.
        outcome = {}
    elif not isinstance(outcome, dict) or set(outcome) != languages:
        raise DomainPackError("domain vocabulary: outcome_check must cover every language")
    for language, wording in outcome.items():
        if (not isinstance(wording, dict)
                or set(wording) != {"question", "yes", "no", "skip"}
                or any(not isinstance(value, str) or not value.strip()
                       or any(character.isnumeric() for character in value)
                       for value in wording.values())
                or not wording["question"].endswith("?")
                or len({wording[key].casefold() for key in ("yes", "no", "skip")}) != 3):
            raise DomainPackError(f"domain vocabulary: invalid outcome_check wording for {language}")
    approval = raw.get("outcome_check_approval_ref")
    if approval is not None and (not isinstance(approval, str) or not approval.strip()):
        raise DomainPackError("domain vocabulary: invalid outcome_check approval reference")
    return {lang: dict(wording) for lang, wording in outcome.items()}, approval


@lru_cache(maxsize=None)
def vocabulary(data_dir: Path = DATA_DIR) -> dict:
    """Validated closed vocabulary from catalogue/vocabulary_v1.json."""
    raw = load_json(data_dir / "catalogue" / "vocabulary_v1.json")
    if not isinstance(raw, dict):
        raise DomainPackError("domain vocabulary must be an object")
    concepts = _strings(raw.get("explanation_concepts"), "explanation_concepts")
    intents, reasons, historical = _explanation_intents(raw)
    goals = _answer_goals(raw, reasons, concepts)
    key = _strings(raw.get("p2_acceptance_request_key"), "p2_acceptance_request_key")
    if len(key) != 4:
        raise DomainPackError("domain vocabulary: p2_acceptance_request_key must have four parts")
    patterns = _advice_patterns(raw)
    criteria = _decision_criteria(raw, concepts, reasons, historical)
    messages = _messages(raw)
    demo = _demo_profile(raw)
    probe = raw.get("search_probe_query")
    if not isinstance(probe, str) or not probe.strip():
        raise DomainPackError("domain vocabulary: search_probe_query must be a non-empty string")
    languages, default_language = _languages(raw)
    labels = _per_language(raw, "step_labels", languages, size=3,
                           problem="step_labels need three labels for every response language")
    talk = _per_language(raw, "source_talk", languages, size=None,
                         problem="source_talk needs a phrase list for every response language")
    version = raw.get("vocabulary_version")
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version) is None:
        raise DomainPackError("domain vocabulary: invalid version")
    outcome, outcome_approval = _outcome_check(raw, languages)
    return {
        "version": version,
        "outcome_check": outcome,
        "outcome_check_approval_ref": outcome_approval,
        "response_languages": languages,
        "default_language": default_language,
        "step_labels": labels,
        "source_talk": talk,
        "explanation_intents": tuple((row[0], row[1]) for row in intents),
        "historical_reasons": frozenset(historical),
        "explanation_concepts": frozenset(concepts),
        "answer_goals": goals,
        "decision_criteria": {name: dict(group) for name, group in criteria.items()},
        "p2_acceptance_request_key": key,
        "benchmark_questions": _strings(raw.get("benchmark_questions"), "benchmark_questions"),
        "advice_patterns": patterns,
        "speech_custom_vocabulary": _strings(raw.get("speech_custom_vocabulary"), "speech_custom_vocabulary"),
        "search_probe_query": probe,
        "messages": dict(messages),
        "demo": demo,
    }


def has_numeric_characters(text: str) -> bool:
    """Reject numerical characters in fixed outcome wording, including Unicode digits."""
    return any(character.isnumeric() for character in text)


@lru_cache(maxsize=None)
def _source_talk_pattern() -> re.Pattern:
    phrases = {phrase.casefold() for group in vocabulary()["source_talk"].values() for phrase in group}
    return re.compile("|".join(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
                               for phrase in sorted(phrases, key=len, reverse=True)), re.IGNORECASE)


def mentions_own_sources(text: str) -> str | None:
    """The first phrase with which a reply talks about its own sources instead of the topic, if any."""
    found = _source_talk_pattern().search(text)
    return found.group(0) if found else None


def message(key: str) -> str:
    """Fixed member-facing text supplied by the domain pack."""
    return vocabulary()["messages"][key]


def demo() -> dict:
    """Synthetic demo/proof profile: member refs, persona, voices and probe utterances."""
    return vocabulary()["demo"]


@lru_cache(maxsize=None)
def prompt(name: str, data_dir: Path = DATA_DIR) -> str:
    """Prompt text exactly as stored in catalogue/prompts/<name>.md."""
    if name not in PROMPT_NAMES:
        raise DomainPackError(f"unknown prompt {name!r}")
    path = data_dir / "catalogue" / "prompts" / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise DomainPackError(f"domain pack lacks prompt {name!r}") from error
    if not text.strip():
        raise DomainPackError(f"domain prompt {name!r} is empty")
    return text
