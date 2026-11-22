"""B3/B4/B7/B8: the gap review reads closed response records only and drafts inert, human-decided proposals."""
import json
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from admin_doubles import MemoryContainer as Container
from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import (
    AnswerReview, ContractError, DecisionEvidence, ExplainabilityRecord, IntentDecision,
)
from governedgraph.learning import gap_review, gap_review_job, improvement_store
from governedgraph.search.corpus import load_corpus
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.catalogue_release import StyleChange

NOW = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)
CORPUS = load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())
VOCABULARY = domain.vocabulary()
REASON = "payment_continuity"


def record(index, *, journey=None, state="first_contact", channel="chat", mode="standard", reason=REASON, language="en",
           kind="acknowledgment", strategy="S6", content=(), issues=None, decline=""):
    evidence = [DecisionEvidence("intent", "offline-fixture-intent", "fixture-1", asdict(IntentDecision(
        "explanation", "transition_impact_question", reason, language)))]
    if issues is not None:
        evidence.append(DecisionEvidence("answer-review", "offline-fixture-answer-review", "fixture-1", asdict(AnswerReview(
            "revise" if issues else "pass", issues, [], []))))
    return ExplainabilityRecord(
        journey_id=journey or f"J-{index}", member_ref=f"M-{index}", strategy_id=strategy, strategy_version="1.0",
        snapshot_id="none", arm="n/a", content_versions=list(content), value_ids=[], value_sources=[],
        model_deployment="gpt-counsellor", agent_name="gg-counsellor", agent_version="1", template_version="1.0",
        disclosure_given=True, escalation_offered=True,
        request_key={"journey_state": state, "intent": "transition_impact_question", "channel": channel,
                     "accessibility_mode": mode},
        numeric_fidelity_passed=True, created_at=(NOW - timedelta(days=1, minutes=index)).isoformat(timespec="seconds"),
        contract_version="1.3", response_id=f"{index:032x}", response_kind=kind, decision_evidence=evidence, decline_reason=decline)


def run(records, **options):
    return gap_review.review(records, catalogue=Catalogue.load(), corpus=CORPUS, vocabulary=VOCABULARY, now=NOW, **options)


def unanswered(count, **options):
    return [record(index, **options) for index in range(count)]


def test_b4_a_proposal_is_built_from_closed_fields_and_carries_no_conversation_text():
    proposals, _ = run(unanswered(6))
    assert proposals
    banned = {"utterance", "text", "message", "transcript", "answer", "member_ref", "member_value", "response_id"}
    for proposal in proposals:
        assert not banned & set(asdict(proposal)) and not banned & set(proposal.evidence)
        assert gap_review.Proposal.from_dict(json.loads(proposal.encode())) == proposal
    assert not {field.name for field in fields(gap_review.Signal)} & {"utterance", "text", "member_ref", "response_id"}


def test_b7_the_situation_has_no_demographic_field_and_rejects_one():
    proposal = run(unanswered(6))[0][0]
    assert set(proposal.situation) == {"journey_state", "topic", "channel", "accessibility_mode", "language"}
    with pytest.raises(ContractError, match="B7"):
        gap_review.Proposal.from_dict({**asdict(proposal), "age_band": "60-65"})
    with pytest.raises(ContractError):
        gap_review.Proposal.from_dict({**asdict(proposal), "situation": {**proposal.situation, "gender": "x"}})


def test_only_explanation_questions_that_failed_count_and_answers_that_passed_do_not():
    assert gap_review.signal_for(record(1)).outcome == "unanswered"
    assert gap_review.signal_for(record(2, kind="escalation")).outcome == "escalated"
    assert gap_review.signal_for(record(3, kind="explanation", strategy="S1", content=["C-001-v1.3"], issues=["relevance"])
                                 ).outcome == "review_flagged"
    assert gap_review.signal_for(record(4, kind="explanation", strategy="S1", content=["C-001-v1.3"], issues=[])) is None
    assert gap_review.signal_for(record(5, kind="explanation", strategy="S1")) is None


def test_thresholds_keep_one_member_or_a_few_cases_from_driving_a_proposal():
    assert run(unanswered(4))[0] == []
    one_member = [record(index, journey="J-same") for index in range(8)]
    assert run(one_member)[0] == []
    assert run(unanswered(5))[0]


def test_missing_content_proposes_a_content_gap_with_no_body_and_related_passages():
    (proposal,), summary = run(unanswered(6))
    assert proposal.kind == "content_gap" and summary["proposals"] == 1
    assert proposal.draft["action"] == "add" and "body" not in proposal.draft
    assert proposal.draft["journey_states"] == ["first_contact"]
    assert proposal.evidence["content_retrieved_share"] == 0.0
    assert proposal.proposal_id.startswith("GP-") and proposal.evidence_digest
    assert all(item in {doc.id for doc in CORPUS} for item in proposal.draft["related_content_ids"])


def test_b3_a_strategy_gap_reuses_an_approved_pattern_and_validates_as_a_style_change():
    catalogue = Catalogue.load()
    state = "objection_raised"
    assert len(catalogue.rankable_for(gap_review.RequestKey(state, "transition_impact_question", "voice", "slower_pace"))) == 1
    records = unanswered(6, state=state, channel="voice", mode="slower_pace", content=["C-001-v1.3"], issues=None)
    proposal = next(item for item in run(records)[0] if item.kind == "strategy_gap")
    change = StyleChange.from_dict(proposal.draft)
    assert change.action == "add" and change.pattern_id in {style.strategy_id for style in catalogue.all()}
    assert state in change.eligible_journey_states and "slower_pace" in change.accessibility_modes
    assert "voice" in change.channels


def test_ungrounded_reviews_propose_a_revision_of_the_retrieved_passage():
    records = [record(index, kind="explanation", strategy="S1", content=["C-001-v1.3"], issues=["grounding"])
               for index in range(6)]
    proposals, _ = run(records)
    revision = next(item for item in proposals if item.kind == "content_revision")
    assert revision.draft["base_id"] == "C-001-v1.3" and revision.draft["base_version"] == "1.3"
    assert "body" not in revision.draft


def test_quality_only_flags_are_reported_as_a_possible_false_negative_never_a_guardrail_change():
    records = [record(index, kind="explanation", strategy="S1", content=["C-001-v1.3"], issues=["relevance"])
               for index in range(6)]
    proposals, _ = run(records)
    flagged = next(item for item in proposals if item.kind == "false_negative_review")
    assert flagged.draft == {"action": "review", "focus": "answer_review", "issues": ["relevance"]}
    assert "legitimate" in flagged.rationale


def test_the_batch_is_deterministic_and_unchanged_evidence_keeps_the_same_digest():
    records = unanswered(6)
    first, second = run(records)[0][0], run(list(reversed(records)))[0][0]
    assert (first.proposal_id, first.evidence_digest) == (second.proposal_id, second.evidence_digest)
    assert run(unanswered(7))[0][0].proposal_id == first.proposal_id
    assert run(unanswered(7))[0][0].evidence_digest != first.evidence_digest


def test_test_journeys_are_excluded_from_counts():
    records = unanswered(8)
    proposals, summary = run(records, excluded_journeys={f"J-{index}" for index in range(4)})
    assert proposals == [] and summary["test_records_excluded"] == 4 and summary["declined_signals"] == 4


def test_a_tampered_proposal_is_rejected():
    proposal = asdict(run(unanswered(6))[0][0])
    with pytest.raises(ContractError, match="digest"):
        gap_review.Proposal.from_dict({**proposal, "evidence": {**proposal["evidence"], "declined": 99, "unanswered": 99}})
    with pytest.raises(ContractError):
        gap_review.Proposal.from_dict({**proposal, "draft": {**proposal["draft"], "body": "x"}})


def test_new_content_gets_the_next_distinct_id_and_a_revision_the_next_version():
    draft = {"action": "add", "language": "en"}
    content = {"title": "Payment", "journey_states": ["*"], "answers": "Will my payment change?",
               "body": "Your payment is described in your official letter."}
    first = gap_review.content_document(draft=draft, content=content, corpus=CORPUS, approval_ref="GB-" + "a" * 32)
    assert first.content_id == "C-G001" and first.version == "1.0" and first.status == "approved"
    second = gap_review.content_document(draft=draft, content=content, corpus=gap_review.effective_corpus(CORPUS, [first]),
                                         approval_ref="GB-" + "b" * 32)
    assert second.content_id == "C-G002"
    base = next(doc for doc in CORPUS if doc.id == "C-001-v1.3")
    revised = gap_review.content_document(
        draft={"action": "revise", "base_id": base.id, "language": "en"},
        content={**content, "title": base.title, "journey_states": list(base.journey_states), "body": base.body + " More."},
        corpus=CORPUS, approval_ref="GB-" + "c" * 32)
    assert (revised.content_id, revised.version, revised.id) == ("C-001", "1.4", "C-001-v1.4")
    with pytest.raises(ContractError, match="change"):
        gap_review.content_document(
            draft={"action": "revise", "base_id": base.id, "language": "en"},
            content={"title": base.title, "journey_states": list(base.journey_states), "answers": base.answers,
                     "body": base.body}, corpus=CORPUS, approval_ref="GB-" + "d" * 32)


@pytest.mark.parametrize("body", ["{{value:capital}} is yours", "You should choose the new contract.", "  "])
def test_b1_b2_content_cannot_hold_placeholders_or_steer_a_choice(body):
    with pytest.raises(ContractError):
        gap_review.content_document(
            draft={"action": "add", "language": "en"}, corpus=CORPUS, approval_ref="GB-" + "a" * 32,
            content={"title": "t", "journey_states": ["*"], "answers": "", "body": body})


def batch(records, journeys, proposals=None):
    store = Container({f"members/{record.member_ref}/{record.response_id}.json": json.dumps(asdict(record)).encode()
                       for record in records})
    return store, Container(journeys), proposals if proposals is not None else Container()


def job(records, journeys, proposals, approvals=None, **options):
    return gap_review_job.run(records=records, journeys=journeys, proposals=proposals,
                              approvals=approvals if approvals is not None else Container(), catalogue=Catalogue.load(),
                              vocabulary=VOCABULARY, seed=CORPUS, **options)


def journey_blob(journey_id, test=""):
    from governedgraph.orchestrator.state_machine import Journey
    from governedgraph.orchestrator.store import JourneyRecord

    return JourneyRecord(Journey(journey_id, f"M-{journey_id}", test_strategy_id=test)).encode().encode()


def test_b8_the_job_writes_only_proposals_and_one_batch_summary_and_is_idempotent():
    records = unanswered(8)
    store, journeys, proposals = batch(records, {f"J-{index}.json": journey_blob(f"J-{index}") for index in range(8)})
    approvals = Container()
    result = job(store, journeys, proposals, approvals, now=NOW)
    assert result["proposals"] == 1 and result["new_revisions"] == 1
    assert {name.split("/")[0] for name in proposals.blobs} == {"proposals", "batches"}
    saved, _ = improvement_store.read_proposals(proposals)
    assert [item.kind for item in saved] == ["content_gap"]
    assert approvals.blobs == {}
    again = job(store, journeys, proposals, approvals, now=NOW + timedelta(minutes=5))
    assert again["new_revisions"] == 0 and len(improvement_store.read_batches(proposals)) == 2


def test_b4_test_and_unverifiable_journeys_never_reach_a_proposal():
    records = unanswered(8)
    known = {f"J-{index}.json": journey_blob(f"J-{index}", test="S1" if index < 4 else "") for index in range(6)}
    store, journeys, proposals = batch(records, known)
    result = job(store, journeys, proposals, now=NOW)
    # J-0..J-3 are tests, J-6 and J-7 have no journey state to verify: only J-4 and J-5 remain, below the threshold.
    assert result["test_records_excluded"] == 6 and result["proposals"] == 0


def test_the_job_ignores_records_older_than_its_lookback_window():
    records = unanswered(8)
    store, journeys, proposals = batch(records, {f"J-{index}.json": journey_blob(f"J-{index}") for index in range(8)})
    store.list_blobs = lambda name_starts_with="": [SimpleNamespace(name=name, last_modified=NOW - timedelta(days=60))
                                                    for name in store.blobs]
    result = job(store, journeys, proposals, now=NOW, lookback_days=30)
    assert result["records_read"] == 0 and result["proposals"] == 0


def test_approved_content_awaits_the_publisher_until_its_marker_exists():
    content = {"title": "Payment", "journey_states": ["*"], "answers": "", "body": "Your payment stays described in your letter."}
    ref = "GB-" + "e" * 32
    document = gap_review.content_document(draft={"action": "add", "language": "en"}, content=content, corpus=CORPUS,
                                           approval_ref=ref)
    store = Container()
    approval = improvement_store.ContentApproval.for_document(document, ref, "GP-" + "1" * 24)
    improvement_store.write_approval(store, approval)
    with pytest.raises(FileExistsError):
        improvement_store.write_approval(store, approval)
    assert improvement_store.read_approvals(store) == [approval]
    assert improvement_store.published_names(store) == set()
    improvement_store.mark_published(store, approval, "job-1")
    assert improvement_store.published_names(store) == {approval.name}
    with pytest.raises(ContractError):
        improvement_store.ContentApproval.from_dict({**asdict(approval), "decision_ref": "GB-" + "f" * 32})


def journal_of(*approvals):
    """The console's journal as it holds an owner decision: one create-only blob per step."""
    return Container({f"{item.decision_ref}/requested.json": json.dumps({
        "action_id": item.decision_ref, "action": "proposal_content_approve", "document_id": item.content.id,
        "content_name": item.name, "proposal": {"proposal_id": item.proposal_id, "evidence_digest": "0" * 64},
        "status": "requested"}).encode() for item in approvals})


def two_approvals():
    base = next(doc for doc in CORPUS if doc.id == "C-001-v1.3")
    content = {"title": base.title, "journey_states": list(base.journey_states), "answers": base.answers}
    first = gap_review.content_document(
        draft={"action": "revise", "base_id": base.id, "language": "en"}, content={**content, "body": base.body + " More."},
        corpus=CORPUS, approval_ref="GB-" + "1" * 32)
    second = gap_review.content_document(
        draft={"action": "revise", "base_id": first.id, "language": "en"}, content={**content, "body": base.body + " More still."},
        corpus=gap_review.effective_corpus(CORPUS, [first]), approval_ref="GB-" + "2" * 32)
    return [improvement_store.ContentApproval.for_document(item, item.approval_ref, "GP-" + "1" * 24) for item in (second, first)]


def test_b8_the_publisher_publishes_only_owner_approved_passages_once_in_version_order():
    from governedgraph.search import reindex_on_approval

    store, approvals = Container(), two_approvals()
    for approval in approvals:
        improvement_store.write_approval(store, approval)
    published = []
    publisher = SimpleNamespace(publish=lambda seed, candidates: published.append([doc.id for doc in candidates]))
    journal = journal_of(*approvals)
    assert reindex_on_approval.publish_approvals(store, journal, CORPUS, publisher, "job-1") == 2
    assert published == [["C-001-v1.4", "C-001-v1.5"]]
    assert reindex_on_approval.publish_approvals(store, journal, CORPUS, publisher, "job-2") == 0 and len(published) == 1


def test_b8_an_approval_without_a_matching_journalled_owner_decision_is_never_published():
    from governedgraph.search import reindex_on_approval

    approvals = two_approvals()
    published = []
    publisher = SimpleNamespace(publish=lambda seed, candidates: published.append(candidates))
    for journal in (journal_of(approvals[0]), journal_of(),
                    Container({f"{approvals[0].decision_ref}/requested.json": json.dumps({"action": "other"}).encode(),
                               **journal_of(approvals[1]).blobs})):
        store = Container()
        for approval in approvals:
            improvement_store.write_approval(store, approval)
        with pytest.raises(PermissionError):
            reindex_on_approval.publish_approvals(store, journal, CORPUS, publisher, "job-1")
        assert improvement_store.published_names(store) == set()
    assert published == []


def test_b8_the_gap_review_has_read_access_to_approvals_and_nothing_in_the_job_writes_them():
    from pathlib import Path

    source = Path(gap_review_job.__file__).read_text()
    assert "write_approval" not in source and "mark_published" not in source
    assert source.count("create_once(proposals") == 2


@pytest.mark.parametrize("field", ["title", "answers"])
def test_title_and_questions_answered_follow_the_same_content_rules_as_the_body(field):
    content = {"title": "Payment", "journey_states": ["*"], "answers": "Will my payment change?", "body": "Described in your letter."}
    for bad in ("{{value:capital}}", "You should choose the new contract."):
        with pytest.raises(ContractError):
            gap_review.content_document(draft={"action": "add", "language": "en"}, corpus=CORPUS,
                                        approval_ref="GB-" + "a" * 32, content={**content, field: bad})


@pytest.mark.parametrize("module", [gap_review, improvement_store])
def test_b4_the_review_logic_imports_no_azure_sdk_model_agent_or_prompt_code(module):
    import ast
    from pathlib import Path

    tree = ast.parse(Path(module.__file__).read_text())
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    toplevel = {node.module for node in tree.body if isinstance(node, ast.ImportFrom) and node.module}
    assert not {name for name in toplevel if name.split(".")[0] == "azure"}
    assert not {name for name in imported if name.startswith(("governedgraph.agents", "governedgraph.orchestrator",
                                                                "governedgraph.voice", "governedgraph.explainability"))}


def test_a_closed_decline_reason_separates_missing_styles_from_protective_escalations():
    styled = {"kind": "escalation", "content": ["C-001-v1.3"], "state": "first_contact"}
    assert gap_review.signal_for(record(1, **styled, decline="no_eligible_style")).outcome == "no_style"
    for reason in ("content_safety", "advice_question", "member_request", "component_failure", ""):
        assert gap_review.signal_for(record(2, **styled, decline=reason)).outcome == "escalated"
    assert len(Catalogue.load().rankable_for(gap_review.RequestKey("first_contact", "x", "chat", "standard"))) > 1
    proposals, _ = run([record(i, **styled, decline="no_eligible_style") for i in range(6)])
    (proposal,) = [item for item in proposals if item.kind == "strategy_gap"]
    assert proposal.evidence["no_style"] == 6 and proposal.evidence["decline_reasons"] == {"no_eligible_style": 6}
    assert "no eligible style was left" in proposal.rationale
    protective = [record(i, **styled, decline="content_safety") for i in range(8)]
    assert run(protective)[0] == []


def test_b4_the_decline_reason_is_closed_and_absent_records_stay_byte_identical():
    from governedgraph.explainability.store import encoded

    plain = record(1)
    assert "decline_reason" not in json.loads(encoded(plain))
    declined = record(2, decline="answer_review")
    assert json.loads(encoded(declined))["decline_reason"] == "answer_review"
    assert ExplainabilityRecord.from_dict(json.loads(encoded(declined))) == declined
    assert ExplainabilityRecord.from_dict(json.loads(encoded(plain))) == plain
    with pytest.raises(ContractError, match="decline"):
        record(3, decline="the member asked about my pension")
    with pytest.raises(ContractError, match="decline"):
        record(4, kind="explanation", strategy="S1", content=["C-001-v1.3"], decline="output_gate")
