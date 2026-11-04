"""ILLUSTRATIVE - synthetic data; human writer approval without Azure requests."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts import writer_approval as approval
from scripts.azd_env import EnvError
from scripts.create_agents import definition_sha256


def fixture(version="1"):
    definition = {"model": "synthetic", "instructions": "ILLUSTRATIVE - synthetic data"}
    values = {"AZURE_ENV_NAME": "gg-dev", "AZURE_AI_PROJECT_ENDPOINT": "https://ais-synthetic.services.ai.azure.com/api/projects/synthetic"}
    record = {
        "tier": "tier2", "approved": True, "decision_ref": "GB-SYNTHETIC",
        "evidence_ref": "ILLUSTRATIVE - synthetic acceptance reference",
        "project_endpoint": values["AZURE_AI_PROJECT_ENDPOINT"],
        "writers": {},
    }
    for name, variable in approval.WRITERS.items():
        values[variable] = version
        record["writers"][name] = {"version": version, "definition_sha256": definition_sha256(definition)}
    return record, values, definition


class Project:
    def __init__(self, version, definition):
        self.calls = []
        self.version = version
        self.definition = definition
        self.agents = self

    def get_version(self, name, version):
        self.calls.append((name, version))
        return SimpleNamespace(version=self.version, definition=SimpleNamespace(as_dict=lambda: self.definition))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.mark.parametrize("version", ["1", "14", "37"])
@pytest.mark.parametrize("decision_ref", ["GB-SYNTHETIC", "HR-SYNTHETIC"])
def test_b8_any_immutable_number_requires_matching_human_approval_and_actual_definition(version, decision_ref):
    record, values, definition = fixture(version)
    record["decision_ref"] = decision_ref
    project = Project(version, definition)
    encoded = approval.verify(project, record, values)
    assert json.loads(encoded) == record
    assert project.calls == [(name, version) for name in approval.WRITERS]
    assert len(encoded.encode()) * 4 // 3 <= 4096


@pytest.mark.parametrize("change", [
    lambda r, v: r.clear(),
    lambda r, v: r.update(tier="tier1"),
    lambda r, v: r.update(approved=False),
    lambda r, v: r.update(approved="true"),
    lambda r, v: r.update(approved=1),
    lambda r, v: r.update(decision_ref=""),
    lambda r, v: r.update(decision_ref="automatic"),
    lambda r, v: r.update(decision_ref="HR-"),
    lambda r, v: r.update(decision_ref="deployment-request"),
    lambda r, v: r.update(evidence_ref=""),
    lambda r, v: r.update(evidence_ref=" " * 2),
    lambda r, v: r.update(evidence_ref="x" * 257),
    lambda r, v: r.update(project_endpoint="https://other.services.ai.azure.com/api/projects/synthetic"),
    lambda r, v: r.update(age_band="synthetic"),
    lambda r, v: r.update(utterance="synthetic"),
    lambda r, v: r["writers"].pop("gg-counsellor"),
    lambda r, v: r["writers"].update({"gg-intent-agent": r["writers"]["gg-counsellor"]}),
    lambda r, v: r["writers"]["gg-counsellor"].update(version="latest"),
    lambda r, v: r["writers"]["gg-counsellor"].update(version="0"),
    lambda r, v: r["writers"]["gg-counsellor"].update(version=1),
    lambda r, v: r["writers"]["gg-counsellor"].update(version="14"),
    lambda r, v: r["writers"]["gg-counsellor"].update(definition_sha256=""),
    lambda r, v: r["writers"]["gg-counsellor"].update(age_band="synthetic"),
    lambda r, v: v.update(AZURE_ENV_NAME="production"),
    lambda r, v: v.update(AZURE_AI_AGENT_VERSION="2"),
    lambda r, v: v.update(AZURE_AI_VOICE_COMPACT_VERSION="2"),
])
def test_b7_b8_missing_rejected_unknown_or_stale_approvals_never_reach_foundry(change):
    record, values, definition = fixture()
    change(record, values)
    project = Project("1", definition)
    with pytest.raises(EnvError):
        approval.verify(project, record, values)
    assert project.calls == []


@pytest.mark.parametrize("record", [None, [], True, "approved"])
def test_b8_malformed_root_fails_closed(record):
    _, values, _ = fixture()
    with pytest.raises(EnvError):
        approval.validate_record(record, values)


@pytest.mark.parametrize("endpoint", [
    "http://ais-synthetic.services.ai.azure.com/api/projects/synthetic",
    "https://synthetic.example/api/projects/synthetic",
    "https://user@ais-synthetic.services.ai.azure.com/api/projects/synthetic",
    "https://ais-synthetic.services.ai.azure.com/api/projects/synthetic?override=1",
    "https://[invalid",
])
def test_b8_invalid_project_origin_cannot_receive_credentials(endpoint):
    record, values, _ = fixture()
    record["project_endpoint"] = values["AZURE_AI_PROJECT_ENDPOINT"] = endpoint
    with pytest.raises(EnvError):
        approval.validate_record(record, values)


def test_b8_changed_actual_definition_cannot_reuse_approval():
    record, values, definition = fixture()
    project = Project("1", {**definition, "instructions": "changed synthetic definition"})
    with pytest.raises(EnvError, match="live immutable definition"):
        approval.verify(project, record, values)


def test_b8_another_returned_version_is_rejected():
    record, values, definition = fixture()
    with pytest.raises(EnvError, match="another version"):
        approval.verify(Project("2", definition), record, values)


def test_b8_failed_foundry_read_propagates_without_creating_or_approving_a_version():
    record, values, _ = fixture()
    def denied(*args):
        raise PermissionError("synthetic read denied")
    with pytest.raises(PermissionError, match="read denied"):
        approval.verify(SimpleNamespace(agents=SimpleNamespace(get_version=denied)), record, values)


@pytest.mark.parametrize("apply", [False, True])
def test_b8_operator_default_is_read_only_and_apply_only_stores_verified_record(tmp_path, monkeypatch, apply, capsys):
    record, values, definition = fixture()
    file = tmp_path / "synthetic-approval.json"
    file.write_text(json.dumps(record))
    project = Project("1", definition)
    modules = {
        "azure.ai.projects": {"AIProjectClient": lambda **kw: project},
        "azure.identity": {"DefaultAzureCredential": lambda: project},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(approval, "load", lambda **kw: values)
    calls = []
    monkeypatch.setattr(approval.subprocess, "run", lambda command, **kw: calls.append((command, kw)))
    args = ["--file", str(file)] + (["--apply"] if apply else [])
    assert approval.main(args) == 0
    assert len(calls) == int(apply)
    if apply:
        command, options = calls[0]
        assert command[:4] == ["azd", "env", "set", approval.APPROVAL_ENV]
        assert json.loads(command[4]) == record
        assert command[5:] == ["--environment", "gg-dev"]
        assert options == {"check": True}
    output = capsys.readouterr().out
    assert "definition_sha256" not in output and "project_endpoint" not in output
    assert project.calls == [(name, "1") for name in approval.WRITERS]


def test_b8_duplicate_fields_and_malformed_files_never_write_approval(tmp_path, monkeypatch, capsys):
    _, values, _ = fixture()
    file = tmp_path / "invalid.json"
    monkeypatch.setattr(approval, "load", lambda **kw: values)
    monkeypatch.setattr(approval.subprocess, "run", lambda *a, **kw: pytest.fail("must not write"))
    for text in ('{"approved":false,"approved":true}', '{"writers":{"version":"1","version":"14"}}', "{"):
        file.write_text(text)
        assert approval.main(["--file", str(file), "--apply"]) == 2
        assert "error:" in capsys.readouterr().err


def test_b8_operator_cannot_write_another_environment(monkeypatch, capsys):
    monkeypatch.setattr(approval, "load", lambda **kw: pytest.fail("must not access another environment"))
    assert approval.main(["--file", "unused.json", "--environment", "production", "--apply"]) == 2
    assert "only to gg-dev" in capsys.readouterr().err
