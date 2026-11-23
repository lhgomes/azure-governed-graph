"""ILLUSTRATIVE - synthetic data. The opt-in model-call record is gg-dev only and never carries credentials."""
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from governedgraph.agents import decision_model, llm_log
from governedgraph.agents.tools import Trace
from governedgraph.common.operational_logging import OperationalFormatter


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(llm_log, "settings", SimpleNamespace(environment_name="gg-dev"))
    monkeypatch.setenv("GOVERNEDGRAPH_LOG_LLM_CALLS", "true")


def lines(caplog):
    return [json.loads(OperationalFormatter().format(record)) for record in caplog.records
            if record.getMessage() == "llm_call"]


@pytest.mark.parametrize("environment,flag", [("gg-dev", "false"), ("gg-prod", "true"), ("", "true")])
def test_b4_model_calls_are_not_recorded_unless_gg_dev_and_explicitly_enabled(
        monkeypatch, caplog, environment, flag):
    caplog.set_level(logging.INFO, logger="governedgraph.llm_calls")
    monkeypatch.setattr(llm_log, "settings", SimpleNamespace(environment_name=environment))
    monkeypatch.setenv("GOVERNEDGRAPH_LOG_LLM_CALLS", flag)
    llm_log.record("J-1", "decision", "decision-v1", {"state": "synthetic"}, "synthetic")
    assert not lines(caplog)


def test_large_requests_are_split_and_reassemble_exactly(on, caplog):
    caplog.set_level(logging.INFO, logger="governedgraph.llm_calls")
    request = {"state": "x" * (2 * llm_log.CHUNK_CHARS + 50)}
    llm_log.record("J-1", "writer", "gpt-counsellor", request, SimpleNamespace(model_dump=lambda **_: {"ok": 1}))
    records = lines(caplog)
    sent = [item for item in records if item["part"] == "request"]
    assert [item["chunk"] for item in sent] == [1, 2, 3] and all(item["chunks"] == 3 for item in sent)
    assert json.loads("".join(item["text"] for item in sent)) == request
    got = [item for item in records if item["part"] == "response"]
    assert json.loads("".join(item["text"] for item in got)) == {"ok": 1}
    assert {item["call_id"] for item in records} == {records[0]["call_id"]}
    assert {item["journey_id"] for item in records} == {"J-1"} and records[0]["kind"] == "writer"


def test_text_that_looks_like_a_credential_is_withheld_and_logging_never_raises(on, caplog):
    caplog.set_level(logging.INFO, logger="governedgraph.llm_calls")

    class Unserializable:
        def model_dump(self, **_):
            raise RuntimeError("synthetic")

    llm_log.record("J-1", "decision", "decision-v1", {"note": "Authorization: Bearer abc.def.ghi"}, Unserializable())
    records = lines(caplog)
    assert "Bearer" not in json.dumps(records)
    assert any(item["text"] == "[withheld: possible credential]" for item in records)


def test_the_decision_call_records_the_exact_request_and_response_without_any_header(on, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="governedgraph.llm_calls")
    body = {"model": "microsoft-decision-1.1", "usage": {"input_tokens": 12, "output_tokens": 1},
            "answers": {"reason": {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.1}}}}
    response = httpx.Response(200, json=body, request=httpx.Request("POST", "https://synthetic.example/"))
    monkeypatch.setattr(decision_model, "settings", SimpleNamespace(
        project_endpoint="https://synthetic.services.ai.azure.com/api/projects/synthetic",
        decision_deployment="decision-v1"))
    monkeypatch.setattr(decision_model.connections, "credential", lambda: SimpleNamespace(
        get_token=lambda *_: SimpleNamespace(token="synthetic-private-token")))
    monkeypatch.setattr(decision_model.connections, "http_client",
                        lambda: SimpleNamespace(post=lambda *_a, **_k: response))
    monkeypatch.setattr(decision_model, "emit", lambda *_a, **_k: None)
    turn = SimpleNamespace(journey=SimpleNamespace(journey_id="J-synthetic"), trace_parent="", trace=Trace())
    questions = {"reason": {"type": "choice", "instructions": "Which?", "criteria": {"a": "A", "b": "B"}}}
    assert decision_model.choose(turn, "Customer message: Synthetic question.", questions) == {"reason": "a"}
    records = lines(caplog)
    request = json.loads("".join(item["text"] for item in records if item["part"] == "request"))
    assert request == {"model": "decision-v1", "state": "Customer message: Synthetic question.",
                       "questions": questions}
    assert json.loads("".join(item["text"] for item in records if item["part"] == "response")) == body
    assert "synthetic-private-token" not in json.dumps(records)
    assert {item["outcome"] for item in records} == {"ok"} and records[0]["kind"] == "decision_reason"


def test_a_rejected_decision_is_recorded_with_its_failure_domain(on, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="governedgraph.llm_calls")
    body = {"model": "microsoft-decision-1.1", "usage": {"input_tokens": 12, "output_tokens": 1},
            "answers": {"reason": {"type": "choice", "choice": "a", "probabilities": {"a": 0.28, "b": 0.26}}}}
    response = httpx.Response(200, json=body, request=httpx.Request("POST", "https://synthetic.example/"))
    monkeypatch.setattr(decision_model, "settings", SimpleNamespace(
        project_endpoint="https://synthetic.services.ai.azure.com/api/projects/synthetic",
        decision_deployment="decision-v1"))
    monkeypatch.setattr(decision_model.connections, "credential", lambda: SimpleNamespace(
        get_token=lambda *_: SimpleNamespace(token="synthetic-private-token")))
    monkeypatch.setattr(decision_model.connections, "http_client",
                        lambda: SimpleNamespace(post=lambda *_a, **_k: response))
    monkeypatch.setattr(decision_model, "emit", lambda *_a, **_k: None)
    turn = SimpleNamespace(journey=SimpleNamespace(journey_id="J-synthetic"), trace_parent="", trace=Trace())
    with pytest.raises(decision_model.DecisionModelAbstained):
        decision_model.choose(turn, "state", {"reason": {"type": "choice", "criteria": {"a": "A", "b": "B"}}})
    records = lines(caplog)
    assert {item["outcome"] for item in records} == {"low_confidence"}
    assert json.loads("".join(item["text"] for item in records if item["part"] == "response")) == body
