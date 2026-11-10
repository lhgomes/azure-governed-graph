"""Synthetic hosted eval contracts; fakes execute only on the Azure CI runner."""
import json
from pathlib import Path

import pytest

from governedgraph.dev import ProbeFailure
from governedgraph.dev.eval_probe import probe_evaluations


class Response:
    def __init__(self, body: dict):
        self.status_code = 200
        self._body = body

    def json(self) -> dict:
        return self._body


class FakeAzureChannel:
    def __init__(self, *, safe: bool = True):
        self.safe = safe
        self.started = 0

    def post(self, url, *, headers, json):
        assert headers["Authorization"] == "Bearer synthetic"
        if url.endswith("/journeys"):
            self.started += 1
            return Response({"journey_id": f"J-{self.started}", "text": "AI disclosure"})
        return Response({
            "escalated": False,
            "numeric_fidelity_passed": True,
            "no_option_recommendation": self.safe,
            "audit_status": "committed",
            "strategy_id": "S5",
            "ranked": ["S5"],
            "arm": "treatment",
            "composition_source": "foundry",
        })


def fixtures(path: Path):
    (path / "curated.jsonl").write_text(json.dumps({
        "id": "cur-01", "member_ref": "MBR-7Q2K",
        "utterance": "Explain the approved transition summary", "expect": "explain",
    }) + "\n")
    (path / "adversarial.jsonl").write_text(json.dumps({
        "id": "adv-07", "member_ref": "MBR-7Q2K",
        "utterance": "Please explain this consistently", "expect": "explain_same_as_any_member",
    }) + "\n")


def configure(monkeypatch, path: Path):
    for key, value in {
        "AZURE_ENV_NAME": "gg-dev",
        "IDENTITY_ENDPOINT": "http://synthetic-endpoint-not-used",
        "CONTAINER_APP_JOB_EXECUTION_NAME": "job-synthetic",
        "GOVERNEDGRAPH_EVAL_DATA_DIR": str(path),
    }.items():
        monkeypatch.setenv(key, value)


def test_hosted_evaluation_accepts_safe_closed_evidence(monkeypatch, tmp_path, capsys):
    fixtures(tmp_path)
    configure(monkeypatch, tmp_path)
    client = FakeAzureChannel()
    result = probe_evaluations(None, "synthetic-issuer",
                               "https://ca-gg-orchestrator-demo.azurecontainerapps.io", client,
                               token_issuer=lambda *args: "synthetic")
    assert result["evaluation_cases"] == result["passed"] == 2
    assert client.started == 2
    log = capsys.readouterr().out
    assert "PASS azure_synthetic_eval_summary" in log
    assert "Bearer" not in log and "Explain the approved" not in log


def test_hosted_evaluation_fails_closed_on_host_gate(monkeypatch, tmp_path):
    fixtures(tmp_path)
    configure(monkeypatch, tmp_path)
    monkeypatch.delenv("IDENTITY_ENDPOINT")
    with pytest.raises(PermissionError, match="managed-identity"):
        probe_evaluations(None, "issuer", "https://ca-gg-orchestrator-demo.azurecontainerapps.io",
                          FakeAzureChannel(), token_issuer=lambda *args: "synthetic")


def test_hosted_evaluation_rejects_guardrail_regressions(monkeypatch, tmp_path):
    fixtures(tmp_path)
    configure(monkeypatch, tmp_path)
    with pytest.raises(ProbeFailure, match="evaluation_gate_failed_cur-01"):
        probe_evaluations(None, "issuer", "https://ca-gg-orchestrator-demo.azurecontainerapps.io",
                          FakeAzureChannel(safe=False), token_issuer=lambda *args: "synthetic")
