"""B4/B7/B8/B9/B10: hosted admin authorization, audited protection, no widening actions."""
import json
import re
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import admin_doubles
from admin_doubles import LocalSources, ORIGIN, SignedIn, open_console, read_lines
from governedgraph.admin import api
from governedgraph.admin.access import AccessPolicy
from governedgraph.common.config import settings
from governedgraph.common.contracts import ExplainabilityRecord
from governedgraph.explainability.store import LocalExplainabilityStore
from governedgraph.strategy_service.snapshot import RankingSnapshot, sign_snapshot
from governedgraph.strategy_service.state import StateUnavailable
from strategy_doubles import local_strategy_service


@pytest.fixture
def console(tmp_path, monkeypatch):
    signed = SignedIn(monkeypatch)
    service = local_strategy_service(tmp_path / "strategy_service")
    client = open_console(monkeypatch, LocalSources(service, tmp_path), signed.policy())
    client.headers.update(signed.bearer([signed.operators]))
    return client, service, tmp_path


def state(client):
    response = client.get("/api/overview")
    assert response.status_code == 200
    return response.json()


def payload(client, action="freeze", **extra):
    return {"action": action, "reason": "Synthetic operator protection", "expected_revision": state(client)["revision"], **extra}


def test_admin_is_opt_in_session_and_same_origin_only(console, monkeypatch):
    client, _, _ = console
    assert client.get("/api/overview", headers={"x-admin-session": "wrong"}).status_code == 403
    assert client.post("/api/actions", json=payload(client), headers={"origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/overview", headers={"origin": ORIGIN}).status_code == 200
    assert client.get("/").headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]
    assert client.get("/assets/admin.js").status_code == 200
    assert client.get("/assets/guide.js").status_code == 200
    assert client.get("/assets/unknown.js").status_code == 404
    monkeypatch.delenv("GOVERNEDGRAPH_ADMIN_ENABLED")
    assert client.get("/").status_code == 404
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_ENABLED", "true")
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_PUBLIC_ORIGIN", "http://insecure.example")
    insecure = TestClient(api.create_app(access=AccessPolicy()), base_url="http://admin.internal")
    assert insecure.get("/").status_code == 404 and insecure.get("/api/overview").status_code == 404


def test_b8_freeze_is_durable_and_audited_before_and_after_mutation(console):
    client, service, directory = console
    request = payload(client)
    response = client.post("/api/actions", json=request)
    assert response.status_code == 200
    assert state(client)["state"]["frozen"]
    restarted = local_strategy_service(directory / "strategy_service")
    assert restarted.frozen
    rows = read_lines(directory / "admin" / "actions.jsonl")
    assert [row["status"] for row in rows] == ["requested", "applied"]
    assert all(row["action_id"] == response.json()["action_id"] for row in rows)
    assert client.post("/api/actions", json=request).status_code == 409
    assert len(read_lines(directory / "admin" / "actions.jsonl")) == 2
    assert service.current is None


def test_b6_b9_suspend_excludes_every_member_but_not_human_help(console):
    client, service, _ = console
    evidence = {"strategy_id": "S2", "strategy_version": service.catalogue.get("S2").version,
                "journey_state": "*", "audit_id": "audit-1", "gap_metric": 25.0,
                "threshold_version": service.thresholds["threshold_version"], "governance_case_id": "GC-1"}
    assert client.post("/api/actions", json=payload(client, "suspend", suspension=evidence)).status_code == 200
    for arm_share in (0, 1):
        service.control_share = arm_share
        served = service.get_ranked({"journey_id": "J-test", "journey_state": "first_contact",
                                     "intent": "transition_impact_question", "channel": "voice"})
        assert "S2" not in [row.strategy_id for row in served.ranked]
        assert served.always_available == ["S6"]
    evidence["strategy_id"] = "S6"
    response = client.post("/api/actions", json=payload(client, "suspend", suspension=evidence))
    assert response.status_code == 400
    assert "escalation" in response.json()["detail"]


@pytest.mark.parametrize("change", [
    {"age_band": "x"}, {"strategy_version": "inactive"}, {"journey_state": "unknown"},
    {"threshold_version": "old"}, {"gap_metric": -1}, {"gap_metric": True}, {"audit_id": " "},
])
def test_suspension_requires_closed_valid_evidence(console, change):
    client, service, directory = console
    evidence = {"strategy_id": "S2", "strategy_version": service.catalogue.get("S2").version,
                "journey_state": "*", "audit_id": "audit-1", "gap_metric": 25,
                "threshold_version": service.thresholds["threshold_version"], "governance_case_id": "GC-1"}
    evidence.update(change)
    assert client.post("/api/actions", json=payload(client, "suspend", suspension=evidence)).status_code == 400
    assert state(client)["state"]["suspensions"] == []
    assert not (directory / "admin" / "actions.jsonl").exists()


def test_b3_admin_read_fails_closed_instead_of_showing_tampered_snapshot(console):
    client, service, directory = console
    snapshot = sign_snapshot(RankingSnapshot(
        "snapshot-admin", service.catalogue.version, "1.0", "audit-1", "tier0",
        {"first_contact|transition_impact_question|voice|standard": [["S1", service.catalogue.get("S1").version]]},
    ), service.signer)
    service.load_snapshot(snapshot)
    path = directory / "strategy_service" / "state.json"
    saved = json.loads(path.read_text())
    saved["current"]["snapshot_id"] = "tampered"
    path.write_text(json.dumps(saved))
    assert client.get("/api/overview").status_code == 503


@pytest.mark.parametrize("extra", [
    {"age": 60}, {"action": "reinstate"}, {"action": "publish"}, {"action": "run_learning"},
    {"action": "recover"}, {"reason": ""}, {"action": ["freeze"]},
    {"suspension": {"gender": "x"}},
])
def test_b7_b8_rejects_demographics_and_widening_or_invalid_actions(console, extra):
    client, _, directory = console
    request = payload(client)
    request.update(extra)
    assert client.post("/api/actions", json=request).status_code == 400
    assert not (directory / "admin" / "actions.jsonl").exists()
    assert not state(client)["state"]["frozen"]


def test_b10_revert_is_protective_and_reinstatement_stays_disabled(console):
    client, _, _ = console
    assert client.post("/api/actions", json=payload(client, "revert_to_static")).status_code == 200
    result = state(client)
    assert result["state"]["frozen"] and result["state"]["current"] is None
    assert result["reinstatement_enabled"] is False
    assert all(row["effective_control"] == row["effective_treatment"] for row in result["rankings"])


def test_b8_no_mutation_when_write_ahead_journal_is_unavailable(console, monkeypatch):
    client, service, _ = console
    monkeypatch.setattr(admin_doubles, "append_action", Mock(side_effect=OSError("private path")))
    response = client.post("/api/actions", json=payload(client))
    assert response.status_code == 503
    assert not service.frozen
    assert "private path" not in response.text


def test_b8_completion_failure_is_not_reported_as_success(console, monkeypatch):
    client, service, _ = console
    original = admin_doubles.append_action

    def fail_completion(path, record):
        if record["status"] == "applied":
            raise OSError("private path")
        original(path, record)

    monkeypatch.setattr(admin_doubles, "append_action", fail_completion)
    response = client.post("/api/actions", json=payload(client))
    assert response.status_code == 503
    assert service.frozen
    assert "may have applied" in response.json()["detail"]
    assert len(state(client)["actions"]) == 1


def test_failed_protection_is_recorded_and_source_errors_are_explicit(console, monkeypatch):
    client, service, directory = console
    monkeypatch.setattr(service, "freeze", Mock(side_effect=StateUnavailable("private source detail")))
    assert client.post("/api/actions", json=payload(client)).status_code == 503
    assert [row["status"] for row in state(client)["actions"]] == ["requested", "failed"]
    (directory / "governance").mkdir()
    (directory / "governance" / "cases.jsonl").write_text('{"bad": "record"}\n')
    response = client.get("/api/overview")
    assert response.status_code == 503
    assert "private" not in response.text


def test_b4_journey_drilldown_is_reference_only_and_never_a_learning_input(console):
    client, _, directory = console
    record = ExplainabilityRecord(
        journey_id="J-admin", member_ref="SYNTHETIC-member", strategy_id="S1", strategy_version="1.0",
        snapshot_id="snapshot-test", arm="control", content_versions=["approved:1"], value_ids=["V1"],
        value_sources=["official"], model_deployment="pinned-model", agent_name="counsellor", agent_version="1",
        template_version="1", disclosure_given=True, escalation_offered=True,
        request_key={"journey_state": "first_contact", "intent": "transition_impact_question",
                     "channel": "voice", "accessibility_mode": "standard"},
        numeric_fidelity_passed=True, response_id="a" * 32, response_kind="explanation",
    )
    store = LocalExplainabilityStore(directory / "explainability" / "records.jsonl")
    store.append(record)
    listing = client.get("/api/journeys").json()["journeys"]
    assert listing[0]["journey_id"] == "J-admin" and listing[0]["responses"] == 1
    detail = client.get("/api/journeys/J-admin").json()
    assert detail["records"][0]["response_id"] == "a" * 32
    assert "utterance" not in json.dumps(detail) and "conversation text" in detail["transcript_notice"]
    assert client.get("/api/journeys/unknown").status_code == 404
    assert client.get("/api/traces/invalid'query").status_code == 400
    response = client.get("/api/traces/J-admin")
    assert response.status_code == 503 and "not configured" in response.json()["detail"]
    assert store.read_all() == detail["records"]
    assert not (directory / "learning-aggregates").exists()


def test_trace_lookup_reuses_the_deployments_values_only_when_explicitly_opted_in(tmp_path, monkeypatch):
    project = ("/subscriptions/00000000-0000-0000-0000-000000000002/resourceGroups/rg-gg-dev"
               "/providers/Microsoft.CognitiveServices/accounts/foundry/projects/gg-dev")
    workspace = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(api, "settings", replace(settings, project_resource_id=project, environment_name="gg-dev"))
    monkeypatch.setenv("LOG_ANALYTICS_WORKSPACE_ID", workspace)
    for name in ("GOVERNEDGRAPH_ADMIN_TRACE_WORKSPACE", "GOVERNEDGRAPH_ADMIN_TRACE_PROJECT",
                 "GOVERNEDGRAPH_ADMIN_TRACE_ENVIRONMENT", "GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP"):
        monkeypatch.delenv(name, raising=False)

    readers = []
    original = api.SyntheticTraceReader
    monkeypatch.setattr(api, "SyntheticTraceReader", lambda *a, **k: readers.append(a) or original(*a, **k))
    api.create_app(access=AccessPolicy())
    assert readers[-1] == ("", "", "gg-dev")
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP", "true")
    api.create_app(access=AccessPolicy())
    assert readers[-1] == (workspace, project, "gg-dev") and original(*readers[-1]).enabled
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_TRACE_WORKSPACE", "00000000-0000-0000-0000-0000000000ff")
    api.create_app(access=AccessPolicy())
    assert readers[-1][0] == "00000000-0000-0000-0000-0000000000ff"


def test_every_element_the_script_uses_exists_in_the_page():
    static = Path(api.__file__).with_name("static")
    script, page = (static / "admin.js").read_text(), (static / "index.html").read_text()
    used = set(re.findall(r'\$\("([a-z-]+)"\)', script))
    present = set(re.findall(r'id="([a-z-]+)"', page))
    assert used and not used - present, f"script references missing elements: {sorted(used - present)}"
    assert 'href="assets/admin.css"' in page and '<base href="{{ADMIN_BASE}}">' in page
