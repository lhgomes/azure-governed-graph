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

PROMPT_NAMES = frozenset({"counsellor", "answer-review"})
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


@lru_cache(maxsize=None)
def vocabulary(data_dir: Path = DATA_DIR) -> dict:
    """Validated closed vocabulary from catalogue/vocabulary_v1.json."""
    raw = load_json(data_dir / "catalogue" / "vocabulary_v1.json")
    if not isinstance(raw, dict):
        raise DomainPackError("domain vocabulary must be an object")
    concepts = _strings(raw.get("explanation_concepts"), "explanation_concepts")
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
    goals = raw.get("answer_goals")
    if not isinstance(goals, dict) or not set(goals) <= set(reasons):
        raise DomainPackError("domain vocabulary: answer_goals must be keyed by declared reasons")
    parsed_goals = {}
    for reason, entry in goals.items():
        if not isinstance(entry, dict) or set(entry) != {"goal", "excluded"} or not isinstance(entry["goal"], str):
            raise DomainPackError(f"domain vocabulary: answer goal {reason!r} must have goal and excluded")
        excluded = _strings(entry["excluded"], f"answer goal {reason!r} excluded", allow_empty=True)
        if not set(excluded) <= set(concepts):
            raise DomainPackError(f"domain vocabulary: answer goal {reason!r} excludes an unknown concept")
        parsed_goals[reason] = (entry["goal"], list(excluded))
    key = _strings(raw.get("p2_acceptance_request_key"), "p2_acceptance_request_key")
    if len(key) != 4:
        raise DomainPackError("domain vocabulary: p2_acceptance_request_key must have four parts")
    patterns = _strings(raw.get("advice_patterns"), "advice_patterns", allow_empty=True)
    try:
        for pattern in patterns:
            re.compile(pattern)
    except re.error as error:
        raise DomainPackError("domain vocabulary: advice_patterns must be valid regular expressions") from error
    criteria = raw.get("decision_criteria")
    if (not isinstance(criteria, dict) or set(criteria) != {"intent", "concepts"}
            or any(not isinstance(group, dict) or not group
                   or any(not isinstance(name, str) or not isinstance(text, str) or not text.strip()
                          for name, text in group.items())
                   for group in criteria.values())):
        raise DomainPackError("domain vocabulary: decision_criteria must describe intent reasons and concepts")
    if set(criteria["concepts"]) != set(concepts):
        raise DomainPackError("domain vocabulary: decision_criteria must describe exactly the explanation concepts")
    if not (set(reasons) - set(historical)) <= set(criteria["intent"]):
        raise DomainPackError("domain vocabulary: decision_criteria must describe every explanation reason")
    messages = raw.get("messages")
    if (not isinstance(messages, dict) or set(messages) != MESSAGE_KEYS
            or any(not isinstance(text, str) or not text.strip() for text in messages.values())):
        raise DomainPackError(f"domain vocabulary: messages must provide exactly {sorted(MESSAGE_KEYS)}")
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
    probe = raw.get("search_probe_query")
    if not isinstance(probe, str) or not probe.strip():
        raise DomainPackError("domain vocabulary: search_probe_query must be a non-empty string")
    languages = frozenset(_strings(raw.get("response_languages"), "response_languages"))
    default_language = raw.get("default_language")
    if default_language not in languages or "en" not in languages:
        raise DomainPackError("domain vocabulary: default_language must be a response language and 'en' must be supported")
    labels = raw.get("step_labels")
    if (not isinstance(labels, dict) or set(labels) != languages
            or any(not isinstance(v, list) or len(v) != 3 or not all(isinstance(x, str) and x for x in v)
                   for v in labels.values())):
        raise DomainPackError("domain vocabulary: step_labels need three labels for every response language")
    version = raw.get("vocabulary_version")
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version) is None:
        raise DomainPackError("domain vocabulary: invalid version")
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
    outcome_approval = raw.get("outcome_check_approval_ref")
    if outcome_approval is not None and (
            not isinstance(outcome_approval, str) or not outcome_approval.strip()):
        raise DomainPackError("domain vocabulary: invalid outcome_check approval reference")
    return {
        "version": version,
        "outcome_check": {lang: dict(wording) for lang, wording in outcome.items()},
        "outcome_check_approval_ref": outcome_approval,
        "response_languages": languages,
        "default_language": default_language,
        "step_labels": {lang: tuple(v) for lang, v in labels.items()},
        "explanation_intents": tuple((row[0], row[1]) for row in intents),
        "historical_reasons": frozenset(historical),
        "explanation_concepts": frozenset(concepts),
        "answer_goals": parsed_goals,
        "decision_criteria": {name: dict(group) for name, group in criteria.items()},
        "p2_acceptance_request_key": key,
        "benchmark_questions": _strings(raw.get("benchmark_questions"), "benchmark_questions"),
        "advice_patterns": patterns,
        "speech_custom_vocabulary": _strings(raw.get("speech_custom_vocabulary"), "speech_custom_vocabulary"),
        "search_probe_query": probe,
        "messages": dict(messages),
        "demo": {**demo, "trace_members": frozenset(trace_members), "persona_voices": dict(voices)},
    }


def has_numeric_characters(text: str) -> bool:
    """Reject numerical characters in fixed outcome wording, including Unicode digits."""
    return any(character.isnumeric() for character in text)


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
