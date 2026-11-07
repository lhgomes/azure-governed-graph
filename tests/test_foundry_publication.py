"""ILLUSTRATIVE - synthetic data. Fresh deployment evaluation publication, with no network."""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from scripts.azd_env import EnvError
from scripts.run_foundry_evals import PIN_KEYS, run, validate


def values():
    return {
        "AZURE_ENV_NAME": "gg-dev",
        "AZURE_AI_PROJECT_ENDPOINT": "https://synthetic.services.ai.azure.com/api/projects/synthetic",
        **dict.fromkeys(PIN_KEYS.values(), "1"),
    }


@pytest.mark.parametrize("change", [
    {"AZURE_ENV_NAME": "production"}, {"AZURE_AI_PROJECT_ENDPOINT": ""},
    {"AZURE_AI_AGENT_VERSION": ""}, {"AZURE_AI_REVIEW_AGENT_VERSION": "latest"},
])
def test_publication_requires_gg_dev_project_and_every_explicit_pin(change):
    with pytest.raises(EnvError):
        validate({**values(), **change})


def project():
    from governedgraph.agents.counsellor import INSTRUCTIONS
    from governedgraph.agents.decision_agents import agent_definition as decision_definition
    from governedgraph.agents.foundry_runtime import agent_definition

    definitions = {
        "gg-counsellor": agent_definition(INSTRUCTIONS, model="gpt-counsellor"),
        "gg-counsellor-voice-compact": agent_definition(INSTRUCTIONS, model="gpt-compact"),
        **{f"gg-{role}-agent": decision_definition(role) for role in ("intent", "understanding", "answer-review")},
    }
    remote = Mock()
    remote.agents.get_version.side_effect = lambda name, version: SimpleNamespace(
        name=name, version=version, definition=definitions[name],
    )
    client = MagicMock()
    client.__enter__.return_value = client
    remote.get_openai_client.return_value = client
    return remote


@pytest.mark.parametrize("failure", [None, "numeric_fidelity", "expectation", "judge", "coverage", "invariance"])
def test_fresh_publication_wires_pins_and_preserves_every_acceptance_gate(tmp_path, monkeypatch, failure):
    from evals.run_evals import load, persona_cases
    from governedgraph.agents.persona_simulator import PersonaScenario, ScriptedPersona
    from governedgraph.evaluation import FLOORS

    cases = load("curated.jsonl") + load("adversarial.jsonl") + persona_cases()
    collected = []

    def collect(model, composer, actor_factory, directory, *, on_row, decision_agents, supporting_recorders):
        assert composer.agent_version == "1"
        assert set(decision_agents) == {"intent", "understanding", "answer-review"}
        assert all(agent.agent_version == "1" for agent in decision_agents.values())
        assert set(supporting_recorders) == {"gpt-counsellor", "gpt-compact"}
        actor = actor_factory(PersonaScenario("synthetic", "ILLUSTRATIVE - synthetic data", "Explain", "Explain"))
        assert isinstance(actor, ScriptedPersona)
        collected.append(model)
        for case in cases[:-1] if failure == "coverage" else cases:
            row = {
                "candidate": model, "case_id": case["id"], "actor_status": "scripted fixture",
                "expectation_met": failure != "expectation", **dict.fromkeys(FLOORS, True),
                "judge_scores": dict.fromkeys(("groundedness", "strategy_style", "advice_refusal"),
                                             3 if failure == "judge" else 4),
            }
            if failure in FLOORS:
                row[failure] = False
            on_row(row)

    def score(remote, report, output, judge, cards):
        assert judge == "gpt-counsellor" and not cards
        assert json.loads(output.read_text())["rows"] == report["rows"]
        report["foundry"] = {"eval_id": "fresh-eval", "run_id": "fresh-run", "status": "completed",
                             "report_url": "https://ai.azure.com/synthetic-report"}
        report["summary"] = {
            model: {"hard_floors": {"demographic_invariance": failure != "invariance"}} for model in report["rows"]
        }

    monkeypatch.setattr("evals.run_model_comparison.collect_candidate", collect)
    monkeypatch.setattr("evals.run_model_comparison.score_report", score)
    output = tmp_path / "report.json"
    remote = project()
    assert run(remote, values(), output) == (0 if failure is None else 1)
    saved = json.loads(output.read_text())
    assert saved["project_endpoint"] == values()["AZURE_AI_PROJECT_ENDPOINT"]
    assert saved["versions"] == dict.fromkeys(PIN_KEYS, "1")
    assert saved["foundry"]["run_id"] == "fresh-run"
    assert saved["automatic_promotion"] is False
    assert saved["evaluation_passed"] == (failure is None)
    assert collected == ["gpt-counsellor", "gpt-compact"]
    assert remote.agents.get_version.call_count == len(PIN_KEYS)
    original = deepcopy(saved)
    with pytest.raises(FileExistsError):
        run(remote, values(), output)
    assert json.loads(output.read_text()) == original


def test_changed_remote_definition_blocks_before_paid_calls(tmp_path):
    remote = project()
    remote.agents.get_version.side_effect = lambda name, version: SimpleNamespace(
        name=name, version=version, definition=SimpleNamespace(as_dict=lambda: {"changed": True}),
    )
    with pytest.raises(ValueError, match="pinned definition"):
        run(remote, values(), tmp_path / "report.json")
    remote.get_openai_client.assert_not_called()
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize("kind", ["timeout", "grader_error", "connection"])
def test_unavailable_grader_retains_remote_ids_and_never_claims_a_quality_pass(tmp_path, monkeypatch, kind):
    from scripts import run_foundry_evals

    from governedgraph.foundry_evaluation import EvaluationUnavailable

    class ProviderError(RuntimeError):
        pass

    for module, content in (
        ("azure.ai.projects", SimpleNamespace(AIProjectClient=MagicMock())),
        ("azure.identity", SimpleNamespace(DefaultAzureCredential=MagicMock())),
        ("openai", SimpleNamespace(OpenAIError=ProviderError)),
    ):
        monkeypatch.setitem(sys.modules, module, content)
    monkeypatch.setattr(run_foundry_evals, "load", lambda **_: values())
    failure = {"timeout": TimeoutError, "grader_error": EvaluationUnavailable, "connection": ProviderError}[kind]

    def interrupted(project, selected, output):
        output.write_text(json.dumps({"foundry": {"eval_id": "retained-eval", "run_id": "retained-run"},
                                      "rows": {"synthetic": []}}))
        raise failure("synthetic evaluator unavailable")

    monkeypatch.setattr(run_foundry_evals, "run", interrupted)
    output = tmp_path / "interrupted.json"
    assert run_foundry_evals.main(["--output", str(output)]) == 2
    report = json.loads(output.read_text())
    assert report["evaluation_passed"] is False and report["evaluation_status"] == "unavailable"
    assert report["foundry"] == {"eval_id": "retained-eval", "run_id": "retained-run"}
    assert report["rows"] == {"synthetic": []} and "synthetic evaluator unavailable" in report["error"]
    before = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        run_foundry_evals.main(["--output", str(output)])
    assert error.value.code == 2 and output.read_bytes() == before
