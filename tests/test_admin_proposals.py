"""B3/B4/B8: owners decide on gap-review proposals; every decision is journalled first and activates nothing by itself."""
import json
from uuid import uuid4

import pytest

from admin_doubles import LocalSources, MemoryContainer, SignedIn, open_console, read_lines
from governedgraph.admin.sources import AdminSourceError
from governedgraph.learning import improvement_store
from strategy_doubles import local_strategy_service
from test_gap_review import CORPUS, record, run, unanswered

THIN = dict(state="objection_raised", channel="voice", mode="slower_pace", content=["C-001-v1.3"])


class OwnerSources(LocalSources):
    """Adds the Strategy API hand-off the real source performs for owner catalogue decisions."""

    def __init__(self, *args):
        super().__init__(*args)
        self.owner_calls = []

    def apply_owner(self, decision, authorization):
        self.owner_calls.append((decision, authorization))
        return {"decision_ref": decision.decision_ref, "status": "pending_signature"}


@pytest.fixture
def desk(tmp_path, monkeypatch):
    signed = SignedIn(monkeypatch)
    owners = str(uuid4())
    proposals = MemoryContainer()
    service = local_strategy_service(tmp_path / "strategy_service")
    source = OwnerSources(service, tmp_path, proposals)
    client = open_console(monkeypatch, source, signed.policy(owner_group=owners))
    client.headers.update(signed.bearer([signed.readers]))
    reader = dict(client.headers)

    def as_owner():
        client.headers.update(signed.bearer([owners]))
        return client

    return client, as_owner, reader, source, proposals, tmp_path


def publish(proposals, records):
    found, summary = run(records)
    for item in found:
        improvement_store.create_once(proposals, item.blob_name, item.encode())
    improvement_store.create_once(proposals, f"batches/{summary['batch_id']}.json", json.dumps(summary).encode())
    return {item.kind: item for item in found}


def decision(proposal, kind, **extra):
    return {"proposal_id": proposal.proposal_id, "evidence_digest": proposal.evidence_digest, "decision": kind,
            "reason": "Synthetic owner decision", "confirmed": True, **extra}


def journal(directory):
    return read_lines(directory / "admin" / "actions.jsonl")


def test_readers_see_proposals_with_status_but_cannot_decide(desk):
    client, _, _, _, proposals, _ = desk
    found = publish(proposals, unanswered(6))
    listing = client.get("/api/proposals").json()
    assert [row["kind"] for row in listing["proposals"]] == ["content_gap"]
    assert listing["proposals"][0]["status"]["code"] == "open" and listing["can_own"] is False
    assert listing["batches"][0]["declined_signals"] == 6
    assert client.post("/api/proposals/decision", json=decision(found["content_gap"], "reject")).status_code == 403


def test_b8_a_rejection_is_journalled_before_and_after_and_closes_the_proposal(desk):
    _, as_owner, _, _, proposals, directory = desk
    found = publish(proposals, unanswered(6))
    owner = as_owner()
    stale = decision(found["content_gap"], "reject") | {"evidence_digest": "0" * 64}
    assert owner.post("/api/proposals/decision", json=stale).status_code == 409
    assert journal(directory) == []
    response = owner.post("/api/proposals/decision", json=decision(found["content_gap"], "reject"))
    assert response.status_code == 200 and response.json()["status"] == "applied"
    assert [row["status"] for row in journal(directory)] == ["requested", "applied"]
    assert owner.get("/api/proposals").json()["proposals"][0]["status"]["code"] == "rejected"
    assert owner.post("/api/proposals/decision", json=decision(found["content_gap"], "defer")).status_code == 409


def test_a_deferred_proposal_stays_open_and_a_decision_needs_confirmation_and_a_reason(desk):
    _, as_owner, _, _, proposals, _ = desk
    found = publish(proposals, unanswered(6))
    owner = as_owner()
    for bad in ({"confirmed": False}, {"reason": "short"}, {"decision": "launch"}, {"unknown": 1}, {"age_band": "60"}):
        assert owner.post("/api/proposals/decision", json=decision(found["content_gap"], "defer") | bad).status_code == 400
    assert owner.post("/api/proposals/decision", json=decision(found["content_gap"], "defer")).status_code == 200
    assert owner.get("/api/proposals").json()["proposals"][0]["status"]["code"] == "deferred"
    assert owner.post("/api/proposals/decision", json=decision(found["content_gap"], "reject")).status_code == 200


def test_b8_approved_content_is_handed_to_the_publisher_not_published_by_the_console(desk):
    _, as_owner, _, source, proposals, directory = desk
    found = publish(proposals, unanswered(6))
    owner = as_owner()
    proposal = found["content_gap"]
    for body in ("{{value:capital}}", "You should choose the new contract.", ""):
        rejected = owner.post("/api/proposals/decision", json=decision(proposal, "approve", content={
            "title": "Payment", "journey_states": ["*"], "answers": "", "body": body}))
        assert rejected.status_code == 400
    assert journal(directory) == [] and improvement_store.read_approvals(proposals) == []
    content = {"title": "Will my payment change?", "journey_states": ["first_contact"],
               "answers": "Does my monthly payment change?", "body": "Your payment is explained in your official letter."}
    response = owner.post("/api/proposals/decision", json=decision(proposal, "approve", content=content))
    assert response.status_code == 200 and response.json()["status"] == "pending_publication"
    reference = response.json()["decision_ref"]
    assert reference.startswith("GB-")
    (approval,) = improvement_store.read_approvals(proposals)
    assert approval.decision_ref == reference and approval.content.id == "C-G001-v1.0"
    assert approval.content.approval_ref == reference and approval.content.status == "approved"
    rows = journal(directory)
    assert [row["status"] for row in rows] == ["requested", "pending_publication"]
    assert rows[0]["proposal"] == {"proposal_id": proposal.proposal_id, "evidence_digest": proposal.evidence_digest}
    assert owner.get("/api/proposals").json()["proposals"][0]["status"]["code"] == "queued_publication"
    improvement_store.mark_published(proposals, approval, "job-1")
    source._cache = {}
    assert owner.get("/api/proposals").json()["proposals"][0]["status"]["code"] == "published"
    assert owner.post("/api/proposals/decision", json=decision(proposal, "approve", content=content)).status_code == 409


def test_a_failed_hand_off_is_journalled_as_uncertain_and_blocks_a_second_approval(desk, monkeypatch):
    _, as_owner, _, source, proposals, directory = desk
    found = publish(proposals, unanswered(6))
    owner = as_owner()
    content = {"title": "t", "journey_states": ["*"], "answers": "", "body": "Your payment is in your letter."}

    def outage(approval):
        raise AdminSourceError("ILLUSTRATIVE - synthetic storage outage")

    monkeypatch.setattr(source, "approve_content", outage)
    response = owner.post("/api/proposals/decision", json=decision(found["content_gap"], "approve", content=content))
    assert response.status_code == 503 and "uncertain" in response.json()["detail"]
    assert [row["status"] for row in journal(directory)] == ["requested", "uncertain"]
    assert owner.get("/api/proposals").json()["proposals"][0]["status"]["code"] == "uncertain"
    again = owner.post("/api/proposals/decision", json=decision(found["content_gap"], "approve", content=content))
    assert again.status_code == 409


def test_b3_b8_approving_a_style_uses_the_owner_catalogue_path_with_the_displayed_revision(desk):
    _, as_owner, _, source, proposals, directory = desk
    found = publish(proposals, unanswered(6, **THIN))
    proposal = found["strategy_gap"]
    owner = as_owner()
    revision = owner.get("/api/overview").json()["revision"]
    assert owner.post("/api/proposals/decision", json=decision(proposal, "approve")).status_code == 400
    forged = decision(proposal, "approve", expected_revision=revision, style={"strategy_id": "S1"})
    assert owner.post("/api/proposals/decision", json=forged).status_code == 400
    edited = {"name": "Reviewed objection explanation"}
    response = owner.post("/api/proposals/decision", json=decision(
        proposal, "approve", expected_revision=revision, style=edited))
    assert response.status_code == 200 and response.json()["status"] == "pending_signature"
    ((queued, authorization),) = source.owner_calls
    assert queued.change.name == "Reviewed objection explanation" and queued.change.pattern_id == proposal.draft["pattern_id"]
    assert authorization.startswith("Bearer ")
    rows = journal(directory)
    assert rows[0]["action"] == "owner_catalogue_add" and rows[0]["proposal"]["proposal_id"] == proposal.proposal_id
    assert not any("authorization" in json.dumps(row).lower() for row in rows)
    row = next(item for item in owner.get("/api/proposals").json()["proposals"] if item["kind"] == "strategy_gap")
    assert row["status"]["code"] in {"queued", "superseded"}


def test_an_answer_review_idea_can_be_acknowledged_but_never_approved(desk):
    _, as_owner, _, _, proposals, directory = desk
    flagged = [record(i, kind="explanation", strategy="S1", content=["C-001-v1.3"], issues=["relevance"]) for i in range(6)]
    found = publish(proposals, flagged)
    owner = as_owner()
    proposal = found["false_negative_review"]
    assert owner.post("/api/proposals/decision", json=decision(proposal, "approve")).status_code == 400
    assert owner.post("/api/proposals/decision", json=decision(proposal, "acknowledge")).status_code == 200
    assert journal(directory)[-1]["action"] == "proposal_acknowledge"
    assert owner.get("/api/proposals").json()["proposals"][0]["status"]["code"] == "acknowledged"


def test_detail_offers_the_current_passage_to_edit_for_a_revision(desk):
    client, _, _, _, proposals, _ = desk
    grounded = [record(i, kind="explanation", strategy="S1", content=["C-001-v1.3"], issues=["grounding"]) for i in range(6)]
    found = publish(proposals, grounded)
    detail = client.get(f"/api/proposals/{found['content_revision'].proposal_id}").json()
    assert detail["base"]["id"] == "C-001-v1.3" and detail["base"]["body"] == next(
        doc.body for doc in CORPUS if doc.id == "C-001-v1.3")
    assert "*" in detail["stages"]
    assert client.get("/api/proposals/not-a-proposal").status_code == 400
    assert client.get("/api/proposals/GP-" + "0" * 24).status_code == 404


def test_an_unreadable_proposal_fails_the_view_instead_of_hiding_it(desk):
    client, _, _, _, proposals, _ = desk
    publish(proposals, unanswered(6))
    proposals.blobs["proposals/GP-" + "a" * 24 + "/" + "b" * 64 + ".json"] = b'{"kind": "content_gap"}'
    assert client.get("/api/proposals").status_code == 503


def test_the_azure_source_reads_proposals_and_explains_unreadable_or_missing_storage():
    from types import SimpleNamespace

    from governedgraph.admin.sources import AzureSources, Containers

    container, empty = MemoryContainer(), SimpleNamespace()
    publish(container, unanswered(6))
    source = AzureSources(http=empty, containers=Containers(empty, empty, empty, empty, container),
                          strategy_client_factory=lambda: None)
    assert [item.kind for item in source.improvement()["proposals"]] == ["content_gap"]
    assert any(doc.id == "C-001-v1.3" for doc in source.content_corpus())
    container.blobs["proposals/GP-" + "a" * 24 + "/" + "b" * 64 + ".json"] = b"{}"
    source._cache.clear()
    with pytest.raises(AdminSourceError, match="unreadable"):
        source.improvement()
    unconfigured = AzureSources(http=empty, containers=Containers(empty, empty, empty, empty),
                                strategy_client_factory=lambda: None)
    with pytest.raises(AdminSourceError, match="not configured"):
        unconfigured.improvement()
