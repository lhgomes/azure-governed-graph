"""B3/B4/B6/B7/B8/B9: the Azure-hosted console reads governed stores and acts only through the Strategy API."""
import hashlib
import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from fastapi.testclient import TestClient

from governedgraph.admin import api, sources
from admin_doubles import SignedIn
from governedgraph.admin.access import AccessPolicy, Identity
from governedgraph.admin.data import Context, revision
from governedgraph.admin.sources import AdminSourceError, AzureSources, Containers
from governedgraph.admin.traces import SyntheticTraceReader
from governedgraph.common.config import settings
from governedgraph.common.contracts import ExplainabilityRecord, SuspensionEvent
from governedgraph.explainability.store import encoded
from governedgraph.learning.feedback import FeedbackManifest
from governedgraph.learning.feedback_store import snapshot_name
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.snapshot import RankingSnapshot

KEY = "first_contact|transition_impact_question|voice|standard"
EMPTY_STATE = {"current": None, "last_good": None, "frozen": False, "suspensions": []}
SUSPENSION = asdict(SuspensionEvent("S2", "1.0", "first_contact", "audit-1", 26.0, "1.0", "GC-1", "2026-11-05T00:00:00+00:00"))
WATERMARK = "ILLUSTRATIVE - synthetic data"
ACTIVE_S1 = Catalogue.load().get("S1").version


class FakeBlob:
    def __init__(self, container, name):
        self.container, self.name = container, name

    def download_blob(self):
        if self.name not in self.container.blobs:
            raise ResourceNotFoundError("missing")
        return SimpleNamespace(readall=lambda: self.container.blobs[self.name])

    def upload_blob(self, data, overwrite=False):
        if self.name in self.container.blobs and not overwrite:
            raise ResourceExistsError("exists")
        self.container.blobs[self.name] = data
        self.container.uploads.append((self.name, overwrite))


class FakeContainer:
    def __init__(self, blobs=None):
        self.blobs, self.uploads, self.listed = dict(blobs or {}), [], 0

    def list_blobs(self, name_starts_with=""):
        self.listed += 1
        return [SimpleNamespace(name=name) for name in sorted(self.blobs) if name.startswith(name_starts_with)]

    def get_blob_client(self, name):
        return FakeBlob(self, name)


class FakeStrategyClient:
    def __init__(self, failure=None):
        self.calls, self.failure = [], failure

    def _do(self, *call):
        if self.failure:
            raise self.failure
        self.calls.append(call)

    def freeze(self):
        self._do("freeze")

    def revert_to_static(self):
        self._do("revert_to_static")

    def suspend(self, event):
        self._do("suspend", event.strategy_id)

    def owner_decision(self, decision, authorization) -> dict:
        raise AssertionError("this protective-action double does not authorize owner decisions")


class Strategy:
    """A mocked Strategy API: serves GET snapshots and records nothing else."""

    def __init__(self, state=None, status=200):
        self.state, self.status, self.requests = state or dict(EMPTY_STATE), status, []

    def client(self):
        def handler(request):
            self.requests.append((request.method, request.url.path))
            return httpx.Response(self.status, json=self.state)
        return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://apim.test/strategy-admin/")


def record(journey="J-1", response="a" * 32):
    return ExplainabilityRecord(
        journey_id=journey, member_ref="SYNTHETIC-member", strategy_id="S1", strategy_version="1.0",
        snapshot_id="snap-1", arm="control", content_versions=["approved:1"], value_ids=["V1"],
        value_sources=["official"], model_deployment="pinned", agent_name="counsellor", agent_version="1",
        template_version="1", disclosure_given=True, escalation_offered=True,
        request_key={"journey_state": "first_contact", "intent": "transition_impact_question",
                     "channel": "voice", "accessibility_mode": "standard"},
        numeric_fidelity_passed=True, response_id=response, response_kind="explanation",
    )


def outcome_csv():
    return ("request_key,strategy_id,strategy_version,interactions,resolved_first_contact,repeat_contact_14d\n"
            f"{KEY},S1,{ACTIVE_S1},400,200,40\n").encode()


def aggregates(payload=None):
    payload = payload or outcome_csv()
    digest = hashlib.sha256(payload).hexdigest()
    manifest = FeedbackManifest(digest, "a" * 64, "b" * 64, 10, 10, 0, 5)
    return {"current.json": json.dumps(asdict(manifest)).encode(), snapshot_name(digest): payload}


def build(state=None, *, strategy=None, audit=None, learning=None, evidence=None, actions=None, client=None, **options):
    strategy = strategy or Strategy(state)
    containers = Containers(FakeContainer(audit), FakeContainer(learning), FakeContainer(evidence), FakeContainer(actions))
    fake_client = client or FakeStrategyClient()
    return AzureSources(http=strategy.client(), containers=containers, strategy_client_factory=lambda: fake_client,
                        context=Context.load(), **options), containers, fake_client, strategy


def test_overview_projects_the_deployed_stores_without_cohort_or_conversation_data():
    evidence = {
        "tier1/snap-1.json": json.dumps({"watermark": WATERMARK, "snapshot_id": "snap-1", "tier": "tier1", "approved": True,
                                         "verdicts": [{"agent": "challenger", "passed": True, "note": "ok"}],
                                         "humans_notified": False, "notification_status": "pending_operator_review",
                                         "rollback_target_retained": True, "created_at": "2026-11-05T01:00:00+00:00"}).encode(),
        "uplift/w1.json": json.dumps({"watermark": WATERMARK, "window_id": "w1", "aggregate_digest": "d", "fcr_uplift_pts": 1.2,
                                      "repeat_reduction_pts": 0.4, "z_fcr": 2.1, "significant": True, "window_closed": True,
                                      "decision": "continue"}).encode(),
    }
    actions = {"ADMIN-1/requested.json": json.dumps({"action_id": "ADMIN-1", "status": "requested", "created_at": "2026-11-05T02:00:00+00:00"}).encode(),
               "ADMIN-1/applied.json": json.dumps({"action_id": "ADMIN-1", "status": "applied", "created_at": "2026-11-05T02:00:00+00:00"}).encode()}
    source, _, _, _ = build({**EMPTY_STATE, "suspensions": [SUSPENSION]}, learning=aggregates(), evidence=evidence, actions=actions)
    result = source.overview()
    assert result["mode"] == "azure" and result["state"]["suspensions"][0]["strategy_id"] == "S2"
    case = result["cases"][0]
    assert case["case_id"] == "GC-1" and case["finding"]["gap_pts"] == 26.0 and case["tier"] == "Tier 2 to reinstate"
    assert {row["kind"] for row in result["decisions"]} == {"tier1_review", "uplift_window"}
    assert next(row for row in result["decisions"] if row["kind"] == "uplift_window")["outcome"] == "continue"
    assert result["learning"]["aggregates"][0]["interactions"] == 400
    assert [row["status"] for row in result["actions"]] == ["applied", "requested"]
    suspended_rows = [row for row in result["rankings"] if row["suspended"]]
    assert suspended_rows and all("S2" not in row["effective_treatment"] for row in suspended_rows)
    blob = json.dumps(result)
    assert not {"age_band", "gender", "salary_band", "cohort_fcr_pct", "dimension"} & set(re.findall(r'"([a-z_]+)"', blob))


def test_only_the_expected_containers_are_ever_read_or_written():
    text = Path(sources.__file__).read_text(encoding="utf-8")
    assert set(re.findall(r'get_container_client\(\s*"([a-z-]+)"', text)) == {"learning-aggregates", "learning-evidence"}
    assert set(Containers.__dataclass_fields__) == {"explainability", "aggregates", "evidence", "actions", "proposals", "approvals"}
    assert "cohort-zone" not in text and "journey-state" not in text and "approved-content" not in text


@pytest.mark.parametrize("state,status", [({"unexpected": True}, 200), (dict(EMPTY_STATE, frozen="yes"), 200),
                                          (dict(EMPTY_STATE, suspensions=[{"age": 70}]), 200), (EMPTY_STATE, 500)])
def test_strategy_state_that_is_malformed_or_unavailable_is_an_explicit_error(state, status):
    source, _, _, _ = build(strategy=Strategy(state, status))
    with pytest.raises(AdminSourceError):
        source.state()


@pytest.mark.parametrize("static_override", [False, True])
def test_admin_reads_strategy_static_baseline_without_losing_protective_state(static_override, monkeypatch):
    baseline = RankingSnapshot(
        "expert-priors-1.1", "1.0", "1.0", "expert-priors", "expert_priors",
        {KEY: [["S1", "1.0"]]}, signature="synthetic-signature", signer="synthetic-signer",
    ).to_dict()
    state = {**EMPTY_STATE, "static": baseline, "suspensions": [SUSPENSION]}
    if static_override:
        state.update(frozen=True, static_override=True)
    source, _, _, strategy = build(state)
    result = source.overview()
    assert result["state"] == state
    assert result["revision"] == revision(state)
    assert all("S2" not in row["effective_control"] + row["effective_treatment"]
               for row in result["rankings"] if row["suspended"])
    assert strategy.requests == [("GET", "/strategy-admin/snapshots")]
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_ENABLED", "true")
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_PUBLIC_ORIGIN", "https://admin.example")
    monkeypatch.setattr(AccessPolicy, "require",
                        lambda self, authorization, *, act: Identity("synthetic-reader", "Synthetic", True, False))
    with TestClient(api.create_app(sources=source)) as web:
        page = web.get("/")
        session = re.search(r'admin-session" content="([^"]+)"', page.text)
        assert session is not None
        response = web.get("/api/overview", headers={"x-admin-session": session[1]})
        assert response.status_code == 200
        assert response.json()["state"] == state


@pytest.mark.parametrize("changes", [
    {"static": None},
    {"static": {"unexpected": True}},
    {"static_override": False},
    {"static_override": True},
    {"static_override": True, "frozen": True, "current": {}},
    {"suspensions": {}},
    {"age_band": "synthetic"},
])
def test_admin_rejects_malformed_extended_strategy_state(changes, caplog):
    source, _, _, _ = build({**EMPTY_STATE, **changes})
    with pytest.raises(AdminSourceError, match="unexpected format"):
        source.state()
    assert "admin_source_failed" in caplog.text


def test_tampered_learning_aggregate_fails_verification_and_missing_manifest_is_not_history():
    files = aggregates()
    digest_name = next(name for name in files if name.startswith("live/"))
    files[digest_name] += b"tampered"
    source, _, _, _ = build(learning=files)
    with pytest.raises(AdminSourceError, match="failed verification"):
        source.overview()
    empty, _, _, _ = build()
    assert empty.overview()["learning"] == {"manifest": None, "aggregates": []}


def test_unreadable_governance_evidence_never_leaks_its_content():
    source, _, _, _ = build(evidence={"tier1/bad.json": b"PRIVATE not json"})
    with pytest.raises(AdminSourceError) as error:
        source.overview()
    assert "PRIVATE" not in str(error.value)


def test_audit_records_are_bounded_cached_and_flagged_when_truncated():
    blobs = {f"members/x/{i}.json": encoded(record(f"J-{i}", f"{i:032x}")).encode() for i in range(5)}
    source, containers, _, _ = build(audit=blobs, max_audit_blobs=3)
    rows, truncated = source.audit_records()
    assert len(rows) == 3 and truncated is True
    source.audit_records()
    assert containers.explainability.listed == 1
    assert "conversation" not in json.dumps(rows) and rows[0]["member_ref"] == "SYNTHETIC-member"
    corrupt, _, _, _ = build(audit={"members/x/bad.json": b"PRIVATE"})
    with pytest.raises(AdminSourceError) as error:
        corrupt.audit_records()
    assert "PRIVATE" not in str(error.value)


def test_journal_is_create_only_and_never_overwrites_an_action_step():
    source, containers, _, _ = build()
    entry = {"action_id": "ADMIN-9", "status": "requested", "created_at": "t"}
    source.journal(entry)
    assert containers.actions.uploads == [("ADMIN-9/requested.json", False)]
    with pytest.raises(AdminSourceError, match="admin journal write failed"):
        source.journal({**entry, "reason": "changed"})
    assert json.loads(containers.actions.blobs["ADMIN-9/requested.json"]) == entry


def request_error(status):
    request = httpx.Request("POST", "https://apim.test/strategy-admin/freeze")
    return httpx.HTTPStatusError("failed", request=request, response=httpx.Response(status, request=request))


@pytest.mark.parametrize("failure,message", [
    (request_error(403), "Strategy.Freeze"), (request_error(401), "refused"),
    (request_error(500), "HTTP 500"), (httpx.ConnectError("private"), "unreachable"),
])
def test_strategy_api_failures_become_actionable_messages(failure, message):
    source, _, _, _ = build(client=FakeStrategyClient(failure))
    with pytest.raises(AdminSourceError, match=message) as error:
        source.apply("freeze", None)
    assert "private" not in str(error.value)


def test_apply_uses_only_the_protective_client_and_drops_cached_audit_reads():
    source, containers, client, _ = build(audit={"members/x/1.json": encoded(record()).encode()})
    source.audit_records()
    source.apply("freeze", None)
    source.apply("revert_to_static", None)
    source.apply("suspend", SuspensionEvent(**SUSPENSION))
    assert client.calls == [("freeze",), ("revert_to_static",), ("suspend", "S2")]
    assert not hasattr(client, "load_snapshot") and not hasattr(client, "reinstate")
    source.audit_records()
    assert containers.explainability.listed == 2


def test_missing_configuration_names_the_missing_settings(monkeypatch):
    monkeypatch.setattr(sources, "settings", replace(settings, strategy_admin_url="", strategy_api_client_id="",
                                                      explainability_account_url="", ranking_storage_url=""))
    with pytest.raises(AdminSourceError) as error:
        AzureSources.from_settings()
    assert all(name in str(error.value) for name in ("STRATEGY_ADMIN_URL", "STRATEGY_API_CLIENT_ID",
                                                    "EXPLAINABILITY_STORAGE_URL", "RANKING_STORAGE_URL"))


# ---------------- the hosted API ----------------
ORIGIN = "https://apim-test.azure-api.net"


@pytest.fixture
def hosted(monkeypatch):
    signed = SignedIn(monkeypatch)

    def make(source, operate=False, **env):
        monkeypatch.setattr(api, "settings", replace(settings, environment_name="gg-dev"))
        monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_ENABLED", env.get("enabled", "true"))
        monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_PUBLIC_ORIGIN", env.get("origin", ORIGIN))
        monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_BASE_PATH", "/admin/")
        app = api.create_app(access=signed.policy(), sources=source, trace_reader=SyntheticTraceReader())
        web = TestClient(app, base_url="http://admin.internal")
        web.headers.update(signed.bearer([signed.operators if operate else signed.readers]))
        page = re.search(r'admin-session" content="([^"]+)"', web.get("/").text)
        if page:
            web.headers["x-admin-session"] = page[1]
        return web
    return make


def freeze_request(web):
    state = web.get("/api/overview").json()
    return web.post("/api/actions", json={"action": "freeze", "reason": "Synthetic operator protection",
                                          "expected_revision": state["revision"]})


def test_hosted_page_uses_the_gateway_base_path_and_needs_no_loopback(hosted):
    source, _, _, _ = build()
    web = hosted(source)
    page = web.get("/")
    assert page.status_code == 200 and '<base href="/admin/">' in page.text and 'href="assets/admin.css"' in page.text
    assert web.get("/healthz").json() == {"ok": True}
    assert web.get("/assets/admin.js").status_code == 200 and web.get("/assets/other.js").status_code == 404
    assert web.get("/assets/guide.js").status_code == 200 and web.get("/assets/guide.json").status_code == 404
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"] and page.headers["cache-control"] == "no-store"


def test_hosted_console_requires_explicit_enablement_and_an_https_origin(hosted):
    source, _, _, _ = build()
    assert hosted(source, origin="http://insecure.test").get("/").status_code == 404
    assert hosted(source, origin="").get("/").status_code == 404
    assert hosted(source, enabled="false").get("/api/overview").status_code == 404


def test_hosted_cross_origin_requests_are_rejected_and_the_session_capability_is_required(hosted):
    source, _, client, _ = build()
    web = hosted(source, operate=True)
    body = {"action": "freeze", "reason": "Synthetic operator protection", "expected_revision": "0" * 64}
    assert web.post("/api/actions", json=body, headers={"origin": "https://attacker.example"}).status_code == 403
    assert web.get("/api/overview", headers={"x-admin-session": "wrong"}).status_code == 403
    assert client.calls == []


def test_reader_azure_console_is_read_only_until_the_operator_group_acts(hosted):
    source, containers, client, _ = build()
    web = hosted(source)
    overview = web.get("/api/overview").json()
    assert overview["access"] == {"mode": "enabled", "user": "synthetic.operator@example.test", "can_act": False,
                                  "can_own": False, "open_actions": False}
    assert web.get("/api/access").json()["mode"] == "enabled"
    response = freeze_request(web)
    assert response.status_code == 403 and "operator group" in response.json()["detail"]
    assert client.calls == [] and containers.actions.uploads == []


def test_operator_action_is_journaled_before_and_after_the_strategy_call(hosted):
    source, containers, client, strategy = build()
    web = hosted(source, operate=True)
    response = freeze_request(web)
    assert response.status_code == 200 and client.calls == [("freeze",)]
    assert [name.split("/")[1] for name, _ in containers.actions.uploads] == ["requested.json", "applied.json"]
    assert all(overwrite is False for _, overwrite in containers.actions.uploads)
    first = json.loads(containers.actions.blobs[containers.actions.uploads[0][0]])
    assert first["actor"].startswith("entra:") and first["status"] == "requested"
    assert all(method == "GET" for method, _ in strategy.requests)


def test_a_failed_strategy_call_is_journaled_as_failed_and_explains_the_fix(hosted):
    source, containers, _, _ = build(client=FakeStrategyClient(request_error(403)))
    web = hosted(source, operate=True)
    response = freeze_request(web)
    assert response.status_code == 503 and "Strategy.Freeze" in response.json()["detail"]
    assert [json.loads(containers.actions.blobs[name])["status"] for name, _ in containers.actions.uploads] == ["requested", "failed"]


def test_unavailable_journal_prevents_any_protective_call(hosted):
    source, containers, client, _ = build()
    web = hosted(source, operate=True)
    containers.actions.upload = None
    original = containers.actions.get_blob_client
    from azure.core.exceptions import ServiceRequestError

    class Down(FakeBlob):
        def upload_blob(self, data, overwrite=False):
            raise ServiceRequestError("down")

    containers.actions.get_blob_client = lambda name: Down(containers.actions, name)
    response = freeze_request(web)
    containers.actions.get_blob_client = original
    assert response.status_code == 503 and "admin journal write failed" in response.json()["detail"]
    assert client.calls == []


def test_stale_state_revision_is_a_conflict_in_azure_too(hosted):
    source, _, client, strategy = build()
    web = hosted(source, operate=True)
    revision = web.get("/api/overview").json()["revision"]
    strategy.state = dict(EMPTY_STATE, frozen=True)
    response = web.post("/api/actions", json={"action": "freeze", "reason": "Synthetic operator protection",
                                              "expected_revision": revision})
    assert response.status_code == 409 and client.calls == []


def test_source_errors_show_safe_messages_and_hide_everything_else(hosted):
    source, _, _, strategy = build()
    web = hosted(source)
    strategy.status = 500
    response = web.get("/api/overview")
    assert response.status_code == 503 and "Strategy service state is unavailable" in response.json()["detail"]
    source.overview = lambda: (_ for _ in ()).throw(ValueError("PRIVATE internal detail"))
    response = web.get("/api/overview")
    assert response.status_code == 503 and "PRIVATE" not in response.text


def test_journeys_are_read_from_the_audit_store_with_truncation_reported(hosted):
    blobs = {f"members/x/{i}.json": encoded(record(f"J-{i}", f"{i:032x}")).encode() for i in range(4)}
    source, _, _, _ = build(audit=blobs, max_audit_blobs=2)
    web = hosted(source)
    listing = web.get("/api/journeys").json()
    assert len(listing["journeys"]) == 2 and listing["truncated"] is True
    detail = web.get("/api/journeys/" + listing["journeys"][0]["journey_id"]).json()
    assert "conversation text" in detail["transcript_notice"] and detail["records"][0]["strategy_id"] == "S1"
    assert web.get("/api/journeys/J-absent").status_code == 404
