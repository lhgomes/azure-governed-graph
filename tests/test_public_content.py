"""Public-source passages stay governed: sourced, figure-free, paired and retrievable. ILLUSTRATIVE - synthetic data."""
import json

import pytest

from counsellor_doubles import LocalContentSearch
from governedgraph.common.config import DATA_DIR
from governedgraph.guardrails import numeric_fidelity
from governedgraph.search.corpus import load_corpus

REGISTER = json.loads((DATA_DIR / "content" / "public_sources_v1.json").read_text())
CORPUS = {doc.content_id: doc for doc in load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())
          if doc.status == "approved"}


def pair(content_id: str) -> str:
    return f"C-{int(content_id.removeprefix('C-')) + 100}"


def test_b3_every_public_source_passage_is_registered_with_a_source_and_licence_note():
    assert REGISTER["sources"] and REGISTER["passages"]
    for source_id, source in REGISTER["sources"].items():
        assert source["url"].startswith("https://") and source["licence"].strip(), source_id
    for content_id, source_ids in REGISTER["passages"].items():
        assert source_ids and set(source_ids) <= set(REGISTER["sources"]), content_id
    cited = {source for ids in REGISTER["passages"].values() for source in ids}
    assert cited == set(REGISTER["sources"]), "register lists a source no passage uses"


def test_b3_registered_passages_are_approved_in_both_languages_with_the_dev_watermark():
    for content_id in REGISTER["passages"]:
        english, dutch = CORPUS[content_id], CORPUS[pair(content_id)]
        assert (english.language, dutch.language) == ("en", "nl")
        assert english.approval_ref == f"CA-{content_id}-{english.version}"
        assert dutch.approval_ref == f"CA-{dutch.content_id}-{dutch.version}"
        assert english.answers.strip() and dutch.answers.strip()
        for doc in (english, dutch):
            assert doc.watermark.startswith("ILLUSTRATIVE - synthetic")


def test_b2_public_source_passages_contain_no_literal_figure_for_the_model_to_restate():
    # The Counsellor prompt allows no literal digits, so a figure in a passage would be unusable or invite a violation.
    for content_id in REGISTER["passages"]:
        for doc in (CORPUS[content_id], CORPUS[pair(content_id)]):
            assert numeric_fidelity.FIGURE.findall(f"{doc.title} {doc.body}") == [], doc.id
            assert not any(ch.isdigit() for ch in doc.body), doc.id


@pytest.mark.parametrize("language,question,expected", [
    ("en", "Who supervises pension funds?", "C-025"),
    ("en", "How do I complain about my pension provider?", "C-026"),
    ("en", "What is a solidarity premium scheme?", "C-019"),
    ("en", "Does the transition affect my allowances?", "C-028"),
    ("nl", "Wat is een solidaire premieregeling?", "C-119"),
    ("nl", "Wie houdt toezicht op pensioenfondsen?", "C-125"),
    ("nl", "Krijg ik compensatie voor de nieuwe pensioenregels?", "C-123"),
    ("nl", "Wat is de invaardatum?", "C-121"),
])
def test_b3_general_pension_questions_retrieve_the_approved_public_passage(language, question, expected):
    hits = LocalContentSearch().search(question, "identified", language=language, top=5)
    assert expected in [hit.content_id for hit in hits]
