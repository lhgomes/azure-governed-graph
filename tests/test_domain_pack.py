"""Domain pack: the framework core carries no sample-domain wording and a pack fails closed when malformed."""
import json
import re
from pathlib import Path
from shutil import copytree

import pytest

from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import FRAMEWORK_INTENT_DECISIONS, INTENT_DECISIONS

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "governedgraph"
SAMPLE_TERMS = re.compile(
    r"\bpension|\bwtp\b|invaren|annelies|\bkees\b|dutch|\bAFM\b|zorgplicht|actuar|\bfund\b|\bfund's|"
    r"MBR-7Q2K|MBR-3H8D|MBR-9C1M|transition_impact|approved-wtp",
    re.IGNORECASE,
)
DEFAULT_WIRING = {"common/config.py"}


def test_framework_core_has_no_sample_domain_wording():
    offenders = [
        f"{path.relative_to(CORE)}:{number}: {line.strip()[:80]}"
        for path in sorted(CORE.rglob("*.py")) if path.relative_to(CORE).as_posix() not in DEFAULT_WIRING
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if SAMPLE_TERMS.search(line)
    ]
    assert offenders == []


@pytest.fixture
def pack(tmp_path):
    copytree(DATA_DIR / "catalogue", tmp_path / "catalogue")
    domain.vocabulary.cache_clear()
    yield tmp_path
    domain.vocabulary.cache_clear()
    domain.prompt.cache_clear()


def _write(pack, mutate):
    path = pack / "catalogue" / "vocabulary_v1.json"
    body = json.loads(path.read_text())
    mutate(body)
    path.write_text(json.dumps(body))


def test_the_shipped_pack_loads_and_framework_routes_cannot_be_removed(pack):
    vocabulary = domain.vocabulary(pack)
    assert vocabulary["explanation_intents"] and vocabulary["answer_goals"]
    assert {row[0] for row in FRAMEWORK_INTENT_DECISIONS} == {"clarification", "advice", "human", "comprehension"}
    assert FRAMEWORK_INTENT_DECISIONS <= INTENT_DECISIONS


@pytest.mark.parametrize("mutate", [
    lambda body: body.pop("messages"),
    lambda body: body["messages"].pop("handover_arranged"),
    lambda body: body["messages"].pop("answer_unavailable"),
    lambda body: body.update(explanation_concepts=[]),
    lambda body: body.update(advice_patterns=["("]),
    lambda body: body["answer_goals"]["payment_continuity"].update(excluded=["not_a_concept"]),
    lambda body: body.update(answer_goals={"unknown_reason": {"goal": "x", "excluded": []}}),
    lambda body: body.update(default_language="fr"),
    lambda body: body["demo"].pop("member_ref"),
    lambda body: body.update(explanation_intents=[["a", "same"], ["b", "same"]]),
])
def test_a_malformed_pack_fails_closed(pack, mutate):
    _write(pack, mutate)
    with pytest.raises(domain.DomainPackError):
        domain.vocabulary(pack)


def test_missing_or_empty_prompts_fail_closed(pack):
    (pack / "catalogue" / "prompts" / "intent.md").write_text("  \n")
    (pack / "catalogue" / "prompts" / "counsellor.md").unlink()
    with pytest.raises(domain.DomainPackError):
        domain.prompt("intent", pack)
    with pytest.raises(domain.DomainPackError):
        domain.prompt("counsellor", pack)
    with pytest.raises(domain.DomainPackError):
        domain.prompt("not-a-prompt", pack)
