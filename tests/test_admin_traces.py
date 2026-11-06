"""B4/B7: read synthetic diagnostic turns only, with bounded scope and no logging of content."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from governedgraph.admin.data import WATERMARK
from governedgraph.admin.traces import SyntheticTraceReader, TraceUnavailable
from governedgraph.agents.foundry_tracing import _message

WORKSPACE = "00000000-0000-0000-0000-000000000001"
PROJECT = ("/subscriptions/00000000-0000-0000-0000-000000000002/resourceGroups/rg-gg-dev"
           "/providers/Microsoft.CognitiveServices/accounts/foundry/projects/gg-dev")


def row(**overrides):
    return {"journey_id": "J-1", "response_id": "a" * 32, "timestamp": "2026-11-05T00:00:00Z",
            "capture_status": "captured", "watermark": WATERMARK,
            "input": _message("user", "Synthetic question")[0],
            "output": _message("assistant", "Checked explanation")[0], **overrides}


def reader():
    return SyntheticTraceReader(WORKSPACE, PROJECT, "gg-dev")


def test_synthetic_lookup_is_scoped_read_only_bounded_and_content_free_in_logs(monkeypatch, caplog):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps([row()])))
    monkeypatch.setattr(subprocess, "run", run)
    result = reader().read("J-1")
    assert result["turns"][0]["messages"][0]["text"] == "Synthetic question"
    args = run.call_args.args[0]
    query = args[args.index("--analytics-query") + 1]
    assert "AppDependencies" in query and "ago(7d)" in query and "take 101" in query
    assert PROJECT in query and WATERMARK in query and '== "J-1"' in query
    assert "governedgraph.synthetic.conversation" in query
    # Azure Monitor stores the boolean attribute as "True"; an exact lowercase match silently returns nothing.
    assert 'tolower(tostring(Properties["governedgraph.synthetic.conversation"])) == "true"' in query
    assert run.call_args.kwargs["timeout"] == 30
    assert "--timespan" in args
    assert "Synthetic question" not in caplog.text
    assert result["truncated"] is False


@pytest.mark.parametrize("change", [
    {"watermark": "not synthetic"}, {"journey_id": "J-other"}, {"response_id": "invalid"},
    {"input": "not json"}, {"output": "x" * 7501},
    {"input": json.dumps([{"role": "system", "parts": [{"type": "text", "content": "not allowed"}]}])},
])
def test_trace_reader_rejects_malformed_and_out_of_scope_content(monkeypatch, change):
    monkeypatch.setattr(subprocess, "run", Mock(return_value=SimpleNamespace(
        returncode=0, stdout=json.dumps([row(**change)]))))
    with pytest.raises(TraceUnavailable, match="invalid or out-of-scope"):
        reader().read("J-1")


def test_no_configuration_or_non_dev_environment_cannot_query(monkeypatch):
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    for instance in (SyntheticTraceReader(), SyntheticTraceReader(WORKSPACE, PROJECT, "production")):
        with pytest.raises(TraceUnavailable, match="not configured"):
            instance.read("J-1")
    with pytest.raises(ValueError):
        reader().read('J-1" | take 999999')
    run.assert_not_called()


@pytest.mark.parametrize("failure", [OSError("private detail"), subprocess.TimeoutExpired("az", 30)])
def test_cli_failures_are_explicit_and_never_echo_output(monkeypatch, failure):
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(TraceUnavailable, match="could not complete"):
        reader().read("J-1")


def test_empty_truncated_withheld_and_permission_denied_results(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="[]"))
    monkeypatch.setattr(subprocess, "run", run)
    assert reader().read("J-1")["turns"] == []
    run.return_value.stdout = json.dumps([row()] * 101)
    result = reader().read("J-1")
    assert result["truncated"] and len(result["turns"]) == 100
    run.return_value.stdout = json.dumps([row(input=json.dumps([
        {"role": "user", "parts": [{"type": "text", "content": "Bearer PRIVATE-CREDENTIAL"}]},
    ]))])
    assert "PRIVATE-CREDENTIAL" not in json.dumps(reader().read("J-1"))
    run.return_value = SimpleNamespace(returncode=1, stdout="private transcript", stderr="private error")
    with pytest.raises(TraceUnavailable, match="Azure rejected"):
        reader().read("J-1")
