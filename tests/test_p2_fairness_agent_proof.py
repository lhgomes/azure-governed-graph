"""ILLUSTRATIVE - synthetic data. Operator-side fairness agent proof without Azure requests."""
import json

import pytest

from scripts import run_p2_container_jobs as p2

IMAGE = "crmlsynthetic.azurecr.io/governedgraph/p2@sha256:" + "a" * 64
VALUES = {"AZURE_AI_PROJECT_ENDPOINT": "https://synthetic.services.ai.azure.com/api/projects/p",
          "AZURE_AI_FAIRNESS_AGENT_VERSION": "3", "FAIRNESS_CLIENT_ID": "00000000-0000-4000-8000-0000000000d1"}


def job():
    return {"id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/jobs/job-gg-p2-fairness",
            "properties": {"template": {"containers": [{"name": "fairness", "image": "old", "env": [
                {"name": "GOVERNEDGRAPH_P2_ROLE", "value": "fairness"},
                {"name": "AZURE_CLIENT_ID", "value": VALUES["FAIRNESS_CLIENT_ID"]},
                {"name": "SECRET_REFERENCE", "secretRef": "never-copied"}]}]}}}


def test_b6_b9_proof_runs_the_protective_agent_on_the_existing_fairness_job(monkeypatch, capsys):
    calls = []

    def az_json(*args):
        calls.append(args)
        return {"name": "execution-1"}

    monkeypatch.setattr(p2, "az_json", az_json)
    monkeypatch.setattr(p2, "wait", lambda *args, **kwargs: "Succeeded")
    assert p2.fairness_agent_proof(job(), IMAGE, VALUES) == "execution-1"
    assert calls[0][:4] == ("rest", "--method", "post", "--url") and calls[0][4].endswith("/jobs/job-gg-p2-fairness/start?api-version=2024-03-01")
    body = json.loads(calls[0][calls[0].index("--body") + 1])["containers"][0]
    assert body["image"] == IMAGE
    assert body["command"] == ["python", "-m", "governedgraph.agents.fairness_runtime", "--action", "audit"]
    env = {item["name"]: item["value"] for item in body["env"]}
    assert env["AZURE_AI_FAIRNESS_AGENT_VERSION"] == "3" and env["GOVERNEDGRAPH_P2_ROLE"] == "fairness"
    assert env["FAIRNESS_MANAGED_IDENTITY_CLIENT_ID"] == env["AZURE_CLIENT_ID"] == VALUES["FAIRNESS_CLIENT_ID"]
    assert "SECRET_REFERENCE" not in env and "reinstate" not in json.dumps(body)
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"fairness_agent": "Succeeded", "execution": "execution-1"}


@pytest.mark.parametrize("change", [
    {"AZURE_AI_FAIRNESS_AGENT_VERSION": ""},
    {"AZURE_AI_FAIRNESS_AGENT_VERSION": "latest"},
    {"AZURE_AI_PROJECT_ENDPOINT": ""},
    {"FAIRNESS_CLIENT_ID": ""},
])
def test_b3_b6_proof_needs_a_pinned_agent_and_the_isolated_identity(monkeypatch, change):
    monkeypatch.setattr(p2, "az_json", lambda *args: pytest.fail("must not start a job"))
    with pytest.raises(ValueError):
        p2.fairness_agent_proof(job(), IMAGE, {**VALUES, **change})


def test_b6_b9_a_failed_agent_proof_does_not_report_success(monkeypatch, capsys):
    monkeypatch.setattr(p2, "az_json", lambda *args: {"name": "execution-1"})
    monkeypatch.setattr(p2, "wait", lambda *args, **kwargs: "Failed")
    with pytest.raises(RuntimeError, match="protective state"):
        p2.fairness_agent_proof(job(), IMAGE, VALUES)
    assert "Succeeded" not in capsys.readouterr().out
