"""ILLUSTRATIVE - synthetic data: durable, immutable, member-scoped audit trail."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
from types import ModuleType, SimpleNamespace
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
import jwt
import pytest

from governedgraph.agents.counsellor import Counsellor
from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError, ExplainabilityRecord, JourneyEvent
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.explainability.reconcile import LEGACY_LABEL, reconciliation_report, run_reconciliation
from governedgraph.explainability.store import (
    AuditConflict, AuditStorageUnavailable, AuditUnavailable, BlobExplainabilityStore,
    LocalExplainabilityStore, QueuedExplainabilityStore, enqueue_in_transaction, system_record,
)
from governedgraph.learning.run_cycle import run_cycle
from governedgraph.orchestrator import api
from governedgraph.orchestrator.messaging import FileJourneyPublisher
from governedgraph.orchestrator.service import OrchestratorService
from governedgraph.orchestrator.state_machine import Event, Journey, State
from governedgraph.orchestrator.store import BlobJourneyStore, FileJourneyStore, JourneyBusy, JourneyRecord, Transaction
from governedgraph.strategy_service.service import StrategyService

# Synthetic boundary for the optional gg-dev-only legacy exception; a fresh deployment configures none.
DEV_CUTOVER_UTC = "2026-10-23T14:29:04Z"


def dev_report(events, records):
    return reconciliation_report(events, records, environment_name="gg-dev", cutover_utc=DEV_CUTOVER_UTC)

MEMBER = MemberIdentity("MBR-7Q2K", True)
OTHER = MemberIdentity("MBR-3H8D", True)


class Primary:
    def __init__(self, path):
        self.store = LocalExplainabilityStore(path)
        self.unavailable = False
        self.lose_ack = False

    def append(self, record):
        if self.unavailable:
            raise AuditStorageUnavailable("PRIVATE values must not be logged")
        self.store.append(record)
        if self.lose_ack:
            self.lose_ack = False
            raise AuditStorageUnavailable("commit succeeded but acknowledgment lost")

    def read_all(self):
        return self.store.read_all()

    def read_member(self, member_ref):
        return self.store.read_member(member_ref)


@pytest.fixture
def runtime(tmp_path, monkeypatch, isolated_conversations):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda self: tmp_path))
    strategies = StrategyService(state_dir=tmp_path / "strategy", control_share=0)
    run_cycle(strategies)
    primary = Primary(tmp_path / "records.jsonl")

    def factory(journey, auth_ref, **kwargs):
        return Counsellor(journey, auth_ref, strategy_service=strategies, **kwargs)

    service = OrchestratorService(FileJourneyStore(tmp_path / "journeys"),
                                  FileJourneyPublisher(tmp_path / "queues"),
                                  factory, audit_store=primary, continuity=isolated_conversations)
    return service, primary


def test_b4_reconciliation_exactly_one_record_for_every_delivered_response(runtime):
    service, primary = runtime
    jid = service.start(MEMBER, "voice", "standard")["journey_id"]
    service.turn(jid, "explain", MEMBER, "", "Explain my transition letter")
    service.turn(jid, "ack", MEMBER, "", "It stays the same and can go up or down each year.")
    service.turn(jid, "human", MEMBER, "", "I want a human advisor")
    service.turn(jid, "waiting", MEMBER, "", "Any update?")
    assert service.turn(jid, "waiting", MEMBER, "", "Any update?")["duplicate"]
    report = run_reconciliation(service.store, primary, [jid])
    assert report["expected_responses"] == report["matched_responses"] == 5
    assert report["coverage_percent"] == 100 and report["complete"]
    assert report["pending_records"] == 0
    assert {r["response_kind"] for r in primary.read_all()} == {
        "disclosure", "explanation", "acknowledgment", "escalation",
    }


def test_ad07_store_failure_queues_durably_and_restart_retries(runtime, caplog):
    service, primary = runtime
    jid = service.start(MEMBER, "voice", "standard")["journey_id"]
    primary.unavailable = True
    result = service.turn(jid, "explain", MEMBER, "PRIVATE-token", "Explain my transition letter")
    assert not result["escalated"] and result["audit_status"] == "queued"
    with service.store.transaction(jid) as transaction:
        record = transaction.record
        assert len(record.pending_audits) == 1
        text = record.encode()
        assert all(s not in text for s in ("PRIVATE-token", "Explain my", "1,412.50"))
    report = run_reconciliation(service.store, primary, [jid])
    assert not report["complete"] and report["coverage_percent"] == 50
    assert report["pending_records"] == 1
    primary.unavailable = False
    restarted = FileJourneyStore(service.store.directory)
    report = run_reconciliation(restarted, primary, [jid])
    assert report["complete"] and report["coverage_percent"] == 100 and report["pending_records"] == 0
    assert "explainability_record_write_failed" in caplog.text and "PRIVATE values" not in caplog.text


def test_ad07_ambiguous_write_and_retry_cannot_duplicate_a_record(runtime):
    service, primary = runtime
    jid = service.start(MEMBER, "voice", "standard")["journey_id"]
    primary.lose_ack = True
    result = service.turn(jid, "explain", MEMBER, "", "Explain my transition letter")
    assert not result["escalated"]
    service.recover()
    report = run_reconciliation(service.store, primary, [jid])
    assert report["complete"] and len(primary.read_all()) == 2


def test_ad07_reconciliation_cannot_complete_with_an_unacknowledged_outbox(runtime):
    service, primary = runtime
    jid = service.start(MEMBER, "chat", "standard")["journey_id"]
    with service.store.transaction(jid) as transaction:
        record = transaction.record
        record.pending_audits.append(ExplainabilityRecord.from_dict(primary.read_all()[0]))
        transaction.save(record)
    primary.unavailable = True
    report = run_reconciliation(service.store, primary, [jid, jid])
    assert report["coverage_percent"] == 100 and report["pending_records"] == 1
    assert report["scope"]["journey_ids"] == [jid] and not report["complete"]
    primary.unavailable = False
    assert run_reconciliation(service.store, primary, [jid])["complete"]


def test_ad07_queue_failure_withholds_generation_and_escalates(runtime, caplog):
    service, primary = runtime
    jid = service.start(MEMBER, "voice", "standard")["journey_id"]
    with service.store.transaction(jid) as transaction:
        journey = transaction.record.journey
    primary.unavailable = True

    def unavailable(record):
        raise OSError("PRIVATE queue error")

    counsellor = service.counsellor_factory(journey, MEMBER.member_ref,
                                           audit_store=primary, audit_enqueue=unavailable)
    result = counsellor.handle("Explain my transition letter")
    assert result.escalated and result.audit_status == "unavailable"
    assert result.state == State.ESCALATION_REQUESTED
    assert "1,412" not in result.text and "PRIVATE" not in result.text
    assert counsellor.tools.trace.escalations[-1].reason_code == "component_failure"
    assert not any(e.response_id for e in journey.events[1:])
    assert journey.events[-1].response_withheld
    assert "generative_responses_paused" in caplog.text and "PRIVATE queue" not in caplog.text


def test_ad07_cannot_deliver_disclosure_if_primary_and_journal_both_fail(runtime):
    service, primary = runtime
    primary.unavailable = True
    original = service.store

    @contextmanager
    def unavailable(journey_id):
        def fail(payload):
            raise OSError("journal unavailable")
        yield Transaction(None, fail)

    service.store = SimpleNamespace(transaction=unavailable)
    with pytest.raises(AuditUnavailable):
        service.start(MEMBER, "chat", "standard")
    assert original.ids() is not None


def test_ad07_hosted_turn_withholds_answer_and_commits_handover_after_queue_failure(runtime):
    service, primary = runtime
    jid = service.start(MEMBER, "chat", "standard")["journey_id"]
    primary.unavailable = True
    original = service.store

    @contextmanager
    def fail_first_save(journey_id):
        with original.transaction(journey_id) as transaction:
            write = transaction.write
            failed = False

            def save(text):
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("synthetic transient journal outage")
                write(text)

            yield Transaction(transaction.record, save)

    service.store = SimpleNamespace(transaction=fail_first_save)
    with pytest.raises(AuditUnavailable):
        service.turn(jid, "explain", MEMBER, "", "Explain my transition letter")
    with original.transaction(jid) as transaction:
        assert transaction.record.journey.state == State.ESCALATION_REQUESTED
        assert transaction.record.journey.events[-1].response_withheld
    handovers = list((service.publisher.directory / "escalations").glob("*.json"))
    assert len(handovers) == 1
    assert json.loads(handovers[0].read_text())["payload"]["reason_code"] == "component_failure"


def test_b4_local_audit_is_idempotent_and_cannot_be_overwritten(tmp_path):
    store = LocalExplainabilityStore(tmp_path / "records.jsonl")
    record = system_record(Journey("J-synthetic", MEMBER.member_ref), uuid4().hex, "disclosure")
    store.append(record)
    store.append(record)
    with pytest.raises(AuditConflict):
        store.append(replace(record, agent_version="tampered"))
    assert store.read_all() == [asdict(record)]


@pytest.fixture
def blob_store(monkeypatch):
    class AzureError(Exception):
        pass

    class ResourceExistsError(AzureError):
        pass

    errors = ModuleType("azure.core.exceptions")
    errors.AzureError, errors.ResourceExistsError = AzureError, ResourceExistsError
    for name in ("azure", "azure.core"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", errors)
    blobs = {}
    uploads = []

    class Blob:
        def __init__(self, name):
            self.name = name

        def upload_blob(self, data, *, overwrite):
            uploads.append(overwrite)
            assert overwrite is False
            if self.name in blobs:
                raise ResourceExistsError()
            blobs[self.name] = data

        def download_blob(self):
            return SimpleNamespace(readall=lambda: blobs[self.name])

    container = SimpleNamespace(get_blob_client=Blob,
                                list_blobs=lambda name_starts_with="": [
                                    SimpleNamespace(name=k) for k in blobs if k.startswith(name_starts_with)])
    store = BlobExplainabilityStore("https://synthetic.invalid", "records",
                                    client=SimpleNamespace(get_container_client=lambda _: container))
    return store, blobs, uploads


def test_b4_blob_records_are_create_only_and_keep_historical_jsonl_readable(blob_store):
    store, blobs, uploads = blob_store
    record = system_record(Journey("J-synthetic", MEMBER.member_ref), uuid4().hex, "disclosure")
    store.append(record)
    store.append(record)
    with pytest.raises(AuditConflict):
        store.append(replace(record, agent_version="tampered"))
    legacy = {**asdict(record), "journey_id": "J-historical"}
    legacy.pop("response_id")
    legacy.pop("response_kind")
    blobs["2020-01-01/J-historical.jsonl"] = (json.dumps(legacy) + "\n").encode()
    assert store.read_journey("J-synthetic") == [asdict(record)]
    assert store.read_journey("J-historical")[0]["response_kind"] == "legacy"
    assert len(store.read_member(MEMBER.member_ref)) == 2
    assert store.read_member(OTHER.member_ref) == []
    assert uploads == [False, False, False]


@pytest.mark.parametrize("code", ["LeaseAlreadyPresent", "LeaseIdMissing"])
def test_b4_concurrent_blob_reconciliation_reports_busy_without_losing_records(blob_store, monkeypatch, code):
    class HttpResponseError(Exception):
        error_code = code

    monkeypatch.setattr(sys.modules["azure.core.exceptions"], "HttpResponseError", HttpResponseError, raising=False)

    def upload(*args, **kwargs):
        raise HttpResponseError()

    store = object.__new__(BlobJourneyStore)
    store.container = SimpleNamespace(get_blob_client=lambda _: SimpleNamespace(
        exists=lambda: True, acquire_lease=upload))
    with pytest.raises(JourneyBusy), store.transaction("J-synthetic"):
        pytest.fail("a leased journey must not be read or overwritten")


@pytest.mark.parametrize("case", ["missing", "duplicate_record", "duplicate_response", "legacy", "conflicting_event"])
@pytest.mark.parametrize("environment", ["", "gg-dev"])
def test_b4_reconciliation_rejects_any_non_exact_coverage(case, environment):
    record = system_record(Journey("J-synthetic", MEMBER.member_ref), uuid4().hex, "disclosure")
    event = JourneyEvent(record.journey_id, "initiated", "identified", "disclosed_and_authenticated",
                         response_id=record.response_id)
    events, records = [event], [asdict(record)]
    if case == "missing":
        records = []
    elif case == "duplicate_record":
        records *= 2
    elif case == "duplicate_response":
        events.append(replace(event, event_id=uuid4().hex))
    elif case == "legacy":
        events = [replace(event, response_id=None)]
    else:
        events.append(replace(event, to_state="closed"))
    assert not reconciliation_report(events, records, environment_name=environment)["complete"]
    assert reconciliation_report([event, event], [asdict(record)], environment_name=environment)["complete"]


def test_b4_dev_cutover_separates_legacy_without_fabricating_records():
    record = system_record(Journey("J-synthetic", MEMBER.member_ref), uuid4().hex, "disclosure")
    current = JourneyEvent(record.journey_id, "initiated", "identified", "disclosed_and_authenticated",
                           created_at=DEV_CUTOVER_UTC, response_id=record.response_id)
    legacy = replace(current, event_id=uuid4().hex, created_at="2026-10-23T14:29:03.999999Z",
                     response_id=None)
    rows = [asdict(record)]
    before = json.dumps(rows, sort_keys=True)
    report = dev_report([legacy, legacy, current], rows)
    assert report["complete"] and report["coverage_percent"] == 100
    assert report["expected_responses"] == report["matched_responses"] == 1
    assert report["legacy_untracked"] == {"label": LEGACY_LABEL, "count": 1}
    assert report["cutover_utc"] == DEV_CUTOVER_UTC and report["legacy_unlinked_responses"] == 0
    assert json.dumps(rows, sort_keys=True) == before
    missing = replace(current, event_id=uuid4().hex, response_id=None)
    assert not dev_report([legacy, missing], rows)["complete"]


@pytest.mark.parametrize("timestamp", [DEV_CUTOVER_UTC, "2026-10-23T16:29:04+02:00",
                                     "2026-10-23T14:29:04.000001Z", "2099-01-01T00:00:00Z"])
def test_b4_every_delivery_at_or_after_dev_cutover_is_required(timestamp):
    event = JourneyEvent("J-synthetic", "a", "b", "response_delivered", created_at=timestamp)
    report = dev_report([event], [])
    assert not report["complete"] and report["expected_responses"] == 1
    assert report["legacy_untracked"]["count"] == 0


def test_b4_a_fresh_dev_deployment_has_no_cutover_exception_unless_configured():
    event = JourneyEvent("J-synthetic", "a", "b", "response_delivered", created_at="2020-01-01T00:00:00Z")
    report = reconciliation_report([event], [], environment_name="gg-dev")
    assert not report["complete"] and report["expected_responses"] == 1
    assert report["cutover_utc"] is None and report["legacy_untracked"] is None


@pytest.mark.parametrize("cutover", ["not-a-time", "2026-10-23T14:29:04"])
def test_b4_a_configured_cutover_must_be_a_timezone_aware_timestamp(cutover):
    with pytest.raises(ValueError):
        reconciliation_report([], [], environment_name="gg-dev", cutover_utc=cutover)


@pytest.mark.parametrize("environment", ["", "local", "production", "gg-prod", "gg-dev-copy"])
def test_b4_production_and_unknown_environments_have_no_cutover_exception(environment):
    event = JourneyEvent("J-synthetic", "a", "b", "response_delivered", created_at="2020-01-01T00:00:00Z")
    report = reconciliation_report([event], [], environment_name=environment, cutover_utc=DEV_CUTOVER_UTC)
    assert not report["complete"] and report["expected_responses"] == 1
    assert report["cutover_utc"] is None and report["legacy_untracked"] is None


@pytest.mark.parametrize("timestamp", ["invalid", "", None, "2026-10-23T14:29:04"])
def test_b4_dev_scope_rejects_invalid_or_ambiguous_timestamps(timestamp):
    event = JourneyEvent("J-synthetic", "a", "b", "response_delivered", created_at=timestamp)
    with pytest.raises(ValueError):
        dev_report([event], [])


def test_b4_empty_reconciliation_is_not_success_shaped():
    report = reconciliation_report([], [])
    assert not report["complete"] and report["coverage_percent"] is None


def test_ad07_withheld_output_is_reported_not_counted_as_a_delivered_answer():
    event = JourneyEvent("J", "explaining", "escalation_requested", "escalate", response_withheld=True)
    report = reconciliation_report([event], [])
    assert report["expected_responses"] == 0 and report["withheld_responses"] == 1
    assert not report["complete"]
    with pytest.raises(ContractError):
        replace(event, response_id=uuid4().hex)


def test_b7_audit_reference_is_opaque_and_schemas_reject_demographics():
    record = system_record(Journey("J-synthetic", MEMBER.member_ref), uuid4().hex, "disclosure")
    for change in ({"response_id": "age_band=75+"}, {"response_kind": "age_band"}, {"age_band": "75+"}):
        with pytest.raises(ContractError):
            ExplainabilityRecord.from_dict({**asdict(record), **change})
    with pytest.raises(ContractError):
        JourneyEvent("J", "a", "b", "output_delivered", response_id="age_75")


def test_gdpr_api_requires_own_stepup_member_and_never_returns_another_report(runtime, monkeypatch):
    service, primary = runtime
    jid = service.start(MEMBER, "chat", "standard")["journey_id"]
    other = service.start(OTHER, "chat", "standard")["journey_id"]
    service.turn(jid, "advice", MEMBER, "", "Which pension option should I choose?")
    monkeypatch.setattr(api.app.state, "runtime", service, raising=False)
    identities = {"own": MEMBER, "other": OTHER, "weak": MemberIdentity(MEMBER.member_ref, False)}

    def verify(token):
        if token not in identities:
            raise jwt.InvalidTokenError()
        return identities[token]

    monkeypatch.setattr(api, "member_authenticator", SimpleNamespace(verify=verify))
    client = TestClient(api.app)
    path = f"/members/{MEMBER.member_ref}/explainability-report"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer expired"}).status_code == 401
    for token in ("other", "weak"):
        assert client.get(path, headers={"Authorization": "Bearer " + token}).status_code == 403
    response = client.get(path, headers={"Authorization": "Bearer own"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    report = response.json()
    assert report["member_ref"] == MEMBER.member_ref and report["interactions"] == 2
    assert {r["journey_id"] for r in report["records"]} == {jid} and other not in response.text
    assert client.post(path, headers={"Authorization": "Bearer own"}).status_code == 405
    primary.unavailable = True
    service.turn(jid, "waiting", MEMBER, "", "Any update?")
    report = service.explainability_report(MEMBER.member_ref, MEMBER)
    assert report["interactions"] == 3 and len(report["pending_response_ids"]) == 1


def test_b4_worm_retention_and_apim_report_route_are_preserved():
    from governedgraph.common.config import REPO_ROOT

    data = (REPO_ROOT / "infra/modules/data.bicep").read_text()
    assert "immutabilityPeriodSinceCreationInDays: 30" in data
    assert "allowProtectedAppendWrites: true" in data
    gateway = (REPO_ROOT / "infra/modules/gateway.bicep").read_text()
    assert "'/members/{memberRef}/explainability-report'" in gateway
    main = (REPO_ROOT / "infra/workloads.bicep").read_text()
    assert "AZURE_ENV_NAME: environmentName" in main


def test_b4_member_report_rejects_conflicting_committed_and_queued_records(runtime):
    service, primary = runtime
    jid = service.start(MEMBER, "chat", "standard")["journey_id"]
    primary.lose_ack = True
    service.turn(jid, "explain", MEMBER, "", "Explain my transition letter")
    with service.store.transaction(jid) as transaction:
        record = transaction.record
        committed = ExplainabilityRecord.from_dict(primary.read_all()[-1])
        record.pending_audits.append(replace(committed, agent_version="conflict"))
        transaction.save(record)
    with pytest.raises(AuditConflict):
        service.explainability_report(MEMBER.member_ref, MEMBER)
