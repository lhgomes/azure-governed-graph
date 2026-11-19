"""B6/B7: metadata watchdog works before user code and cannot execute fairness."""
import json
from io import BytesIO
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import pytest

from governedgraph.learning.p2_components import STEPS, check_component_states
from governedgraph.learning import container_jobs
from governedgraph.learning.container_jobs import component_states, read_state, watch

IMAGE = "registry/governedgraph/p2@sha256:tested"
CYCLE = "cycle-test"
JOB_ID = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/jobs/job-gg-p2-pipeline"
EXECUTIONS_URL = f"https://management.azure.com{JOB_ID}/executions?api-version=2024-03-01"


@pytest.fixture
def execution_api(monkeypatch):
    pages, calls = {}, []

    def fetch(request, *, timeout):
        assert timeout == 30
        assert request.get_header("Authorization") == "Bearer synthetic-arm-token"
        calls.append(request.full_url)
        return BytesIO(json.dumps(pages[request.full_url]).encode())

    def token(scope):
        assert scope == "https://management.azure.com/.default"
        return SimpleNamespace(token="synthetic-arm-token")

    monkeypatch.setattr(container_jobs, "urlopen", fetch)
    return pages, calls, SimpleNamespace(get_token=token)


def execution(stage, *, status="Succeeded", image=IMAGE, cycle=CYCLE):
    return {"properties": {"status": status, "template": {"containers": [{
        "image": image,
        "env": [{"name": "GOVERNEDGRAPH_P2_STAGE", "value": stage}, {"name": "GOVERNEDGRAPH_P2_CYCLE_ID", "value": cycle}],
    }]}}}


def test_b6_container_watchdog_certifies_only_all_unique_completed_stages():
    executions = {"pipeline": [execution(stage) for stage in STEPS if stage != "audit"],
                  "fairness": [execution("audit")]}
    assert check_component_states(component_states(executions, CYCLE, IMAGE))
    executions["fairness"] = []
    assert not check_component_states(component_states(executions, CYCLE, IMAGE))


def test_b6_container_watchdog_detects_audit_failure_before_component_python():
    states = component_states({"fairness": [execution("audit", status="Failed")]}, CYCLE, IMAGE)
    with pytest.raises(RuntimeError, match="freeze"):
        check_component_states(states)


def test_b6_degraded_execution_requires_a_protective_freeze():
    states = component_states({"fairness": [execution("audit", status="Degraded")]}, CYCLE, IMAGE)
    with pytest.raises(RuntimeError, match="freeze"):
        check_component_states(states)


def test_b6_unknown_metadata_cannot_hide_a_failed_component():
    states = component_states({"pipeline": [execution("rank", status="Unknown")],
                               "fairness": [execution("audit", status="Failed")]}, CYCLE, IMAGE)
    with pytest.raises(RuntimeError, match="freeze"):
        check_component_states(states)


@pytest.mark.parametrize("recover", [True, False])
def test_b6_watchdog_bounds_unknown_observations_without_certifying_them(monkeypatch, recover):
    calls, sleeps = [], []
    monkeypatch.setenv("P2_PIPELINE_JOB_ID", "pipeline")
    monkeypatch.setenv("P2_FAIRNESS_JOB_ID", "fairness")
    monkeypatch.setattr(container_jobs.time, "sleep", sleeps.append)
    monkeypatch.setattr(container_jobs.time, "monotonic", lambda: 0)

    def rows(credential, job_id):
        calls.append(job_id)
        if job_id == "fairness":
            return [execution("audit")]
        status = "Succeeded" if recover and len(calls) > 2 else "Unknown"
        return [execution(stage, status=status if stage == "rank" else "Succeeded")
                for stage in STEPS if stage != "audit"]

    monkeypatch.setattr(container_jobs, "execution_rows", rows)
    if recover:
        watch(object(), CYCLE, IMAGE)
        assert len(calls) == 4 and sleeps == [10]
    else:
        with pytest.raises(container_jobs.P2MetadataUnavailable, match="not certified"):
            watch(object(), CYCLE, IMAGE)
        assert len(calls) == 10 and sleeps == [10] * 4


@pytest.mark.parametrize("executions", [
    {"pipeline": [execution("audit")]},
    {"fairness": [execution("sign")]},
    {"pipeline": [execution("rank"), execution("rank")]},
    {"pipeline": [execution("rank", image="unverified")]},
    {"pipeline": [execution("rank", status="Unknown")]},
])
def test_b7_container_watchdog_rejects_role_image_and_metadata_mismatch(executions):
    with pytest.raises(ValueError):
        component_states(executions, CYCLE, IMAGE)


def test_b6_container_watchdog_never_uses_unrelated_cycle_as_evidence():
    assert component_states({"fairness": [execution("audit", cycle="other")]}, CYCLE, IMAGE) == {}


def test_b6_execution_history_pagination_does_not_freeze_a_successful_cycle(execution_api):
    pages, calls, credential = execution_api
    next_page = EXECUTIONS_URL + "&$skiptoken=second-page"
    old = execution("rank", status="Failed", cycle="previous-cycle")
    current = [execution(stage) for stage in STEPS if stage != "audit"]
    pages[EXECUTIONS_URL] = {"value": [old, *current[:3]], "nextLink": next_page}
    pages[next_page] = {"value": current[3:]}
    rows = container_jobs.execution_rows(credential, JOB_ID)
    assert rows == [old, *current] and calls == [EXECUTIONS_URL, next_page]
    assert check_component_states(component_states(
        {"pipeline": rows, "fairness": [execution("audit")]}, CYCLE, IMAGE,
    ))


def test_b6_pagination_cannot_hide_a_failed_component_on_a_later_page(execution_api):
    pages, calls, credential = execution_api
    next_page = EXECUTIONS_URL + "&$skiptoken=second-page"
    pages[EXECUTIONS_URL] = {"value": [execution("rank")], "nextLink": next_page}
    pages[next_page] = {"value": [execution("veto", status="Failed")], "nextLink": None}
    rows = container_jobs.execution_rows(credential, JOB_ID)
    with pytest.raises(RuntimeError, match="freeze"):
        check_component_states(component_states({"pipeline": rows}, CYCLE, IMAGE))
    assert calls == [EXECUTIONS_URL, next_page]


@pytest.mark.parametrize("next_link", [
    "https://other.invalid/executions",
    EXECUTIONS_URL.replace("https:", "http:"),
    EXECUTIONS_URL.replace("management.azure.com", "user@management.azure.com"),
    EXECUTIONS_URL.replace("job-gg-p2-pipeline", "job-other"),
    EXECUTIONS_URL + "#fragment",
    EXECUTIONS_URL,
    [],
    True,
])
def test_b6_execution_pagination_rejects_foreign_malformed_or_repeated_links(execution_api, next_link):
    pages, calls, credential = execution_api
    pages[EXECUTIONS_URL] = {"value": [], "nextLink": next_link}
    with pytest.raises(ValueError, match="P2 execution pagination"):
        container_jobs.execution_rows(credential, JOB_ID)
    assert calls == [EXECUTIONS_URL]


@pytest.mark.parametrize("page", [{}, {"value": None}, {"value": {}}, {"value": [None]}, []])
def test_b6_execution_pagination_rejects_incomplete_page_shapes(execution_api, page):
    pages, calls, credential = execution_api
    pages[EXECUTIONS_URL] = page
    with pytest.raises(ValueError, match="invalid P2 execution page"):
        container_jobs.execution_rows(credential, JOB_ID)
    assert calls == [EXECUTIONS_URL]


def test_b7_container_handoff_rejects_extra_fields_and_wrong_image():
    raw = {"cycle_id": CYCLE, "stage": "key-isolation", "image": IMAGE, "state": {"rbac_denial": True},
           "metrics": {"component_success": 1}, "watermark": "ILLUSTRATIVE - synthetic data"}
    container = SimpleNamespace(get_blob_client=lambda _: SimpleNamespace(
        download_blob=lambda: SimpleNamespace(readall=lambda: json.dumps(raw).encode())))
    assert read_state(container, CYCLE, "aggregate", IMAGE) == {"rbac_denial": True}
    raw["cohort"] = "not-permitted"
    with pytest.raises(ValueError):
        read_state(container, CYCLE, "aggregate", IMAGE)
    del raw["cohort"]
    with pytest.raises(ValueError):
        read_state(container, CYCLE, "aggregate", "different-image")


def test_b7_container_monitor_cannot_start_override_or_read_fairness_logs():
    root = Path(__file__).resolve().parents[1]
    source = (root / "infra/modules/p2-container-monitor-rbac.bicep").read_text()
    assert "actions: ['Microsoft.App/jobs/executions/read']" in source
    assert "scope: pipeline" in source and "scope: fairness" in source
    assert "dataActions: []" in source
    for forbidden in ("/start", "/write", "/action", "/log", "/*"):
        assert forbidden not in source


def test_b6_watchdog_has_time_to_freeze_before_the_platform_stops_its_execution():
    root = Path(__file__).resolve().parents[1]
    source = (root / "infra/modules/p2-jobs.bicep").read_text()
    assert "replicaTimeout: 4200" in source
    assert signature(watch).parameters["timeout"].default == 3600
