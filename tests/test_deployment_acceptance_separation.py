"""ILLUSTRATIVE - synthetic data. Deployment success is not member-use acceptance."""
import json
import shlex
from dataclasses import asdict
from pathlib import Path

import pytest
from scripts import acceptance, deploy
from test_deploy import ARGS, DEFAULT_ARGS, FakeAzure, approved_release, gateway  # noqa: F401


@pytest.mark.parametrize("problem", [None, "frozen", "running", "audit", "scope", "image", "receipt"])
def test_b8_recovered_cycle_is_archived_only_after_human_receipt_and_live_checks(tmp_path, monkeypatch, problem):
    from scripts import run_p2_container_jobs as p2
    from test_p2_operator_polling import CYCLE, IMAGE, execution

    answers = deploy.Answers(subscription_id="s", resource_group="rg")
    attempt = {"subscription_id": "s", "resource_group": "rg", "sample": "pension_wtp",
               "cycle_id": CYCLE, "image": IMAGE}
    state = deploy.State(answers=asdict(answers), learning_attempt=dict(attempt))
    driver = deploy.Deployer(FakeAzure(), deploy.Prompter(interactive=False), state,
                             state_path=tmp_path / "state.json", sleep=lambda _: None)
    receipt = {"audit_execution": "job-gg-p2-fairness-healthy", "decision_ref": "GB-DEV-2026-005",
               "frozen": False, "recovery_id": "recovery-" + "1" * 32}
    watchdog = execution("watchdog", status="Failed")
    audit = execution("audit", name=receipt["audit_execution"])
    audit["properties"]["startTime"] = "2026-11-07T07:34:00Z"
    container = audit["properties"]["template"]["containers"][0]
    container["command"] = ["python", "-m", "governedgraph.learning.container_jobs", "--stage", "audit"]
    container["env"] += [{"name": "GOVERNEDGRAPH_P2_ROLE", "value": "fairness"},
                         {"name": "GOVERNEDGRAPH_P2_IMAGE", "value": IMAGE}]
    if problem == "running":
        watchdog["properties"]["status"] = "Running"
    if problem == "audit":
        audit["name"] = "job-gg-p2-fairness-other"
    if problem == "scope":
        state.learning_attempt["resource_group"] = "other"
    if problem == "image":
        container["image"] = "other"
    if problem == "receipt":
        receipt["frozen"] = True
    monkeypatch.setattr(p2, "execution_history",
                        lambda job, **kwargs: [audit] if job["id"].endswith("fairness") else [watchdog])
    monkeypatch.setattr(driver, "values", lambda: {"STRATEGY_ADMIN_URL": "https://synthetic/strategy-admin"})
    monkeypatch.setattr(driver, "expect_status", lambda *args: json.dumps({"frozen": problem == "frozen"}))
    if problem:
        with pytest.raises(deploy.LearningCycleStopped):
            driver.acknowledge_learning_recovery(receipt)
        assert state.learning_attempt and not state.learning_recoveries
    else:
        driver.acknowledge_learning_recovery(receipt)
        assert not state.learning_attempt and state.learning_recoveries == [{"attempt": attempt, "receipt": receipt}]
        assert not state.learning_receipts and state.deployment_status == "pending"
        assert deploy.State.load(tmp_path / "state.json").learning_recoveries == state.learning_recoveries


def deployment(azure, tmp_path, argv=ARGS):
    azure.environment_path = tmp_path / "azd" / "gg-dev"
    return deploy.main(argv, runner=azure, state_path=tmp_path / "state.json", sleep=lambda _: None,
                       environment_path=azure.environment_path)


def accept(azure, tmp_path, argv=()):
    return acceptance.main(list(argv), runner=azure, state_path=tmp_path / "state.json", sleep=lambda _: None)


def test_deployment_completes_without_running_probabilistic_acceptance(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["scripts.run_foundry_evals"] = (1, "judge unavailable")
    azure.voice_proof_status = "Failed"
    assert deployment(azure, tmp_path) == 0
    state = deploy.State.load(tmp_path / "state.json")
    assert state.deployment_status == "succeeded"
    assert state.deployment_id and state.runtime_fingerprint
    assert not state.acceptance
    assert not any("scripts.run_foundry_evals" in c or "scripts.run_dev_proof" in c for c in azure.calls)
    assert state.learning_receipts["schedule"] == "enabled"
    assert "Deployment complete." in capsys.readouterr().out


def test_p2_observation_retry_reuses_persisted_cycle_without_rebuilding_or_republishing_fixtures(
        tmp_path, monkeypatch):
    azure = FakeAzure()
    original = azure.run
    pending = {}

    def interrupted(args, **kwargs):
        if "scripts/run_p2_container_jobs.py" in args:
            state = deploy.State.load(tmp_path / "state.json")
            pending.update(state.learning_attempt)
            assert args[args.index("--cycle-id") + 1] == pending["cycle_id"]
            raise deploy.CommandError(args, 3, "observation unavailable")
        return original(args, **kwargs)

    monkeypatch.setattr(azure, "run", interrupted)
    assert deployment(azure, tmp_path) == 1
    assert pending["image"] and pending["runtime_fingerprint"]
    monkeypatch.setattr(azure, "run", original)
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 0
    command = next(c for c in azure.calls if "scripts/run_p2_container_jobs.py" in c)
    assert command[command.index("--resume-cycle") + 1] == pending["cycle_id"]
    assert "--cycle-id" not in command
    assert not any(c[:2] == ["azd", "deploy"] or c[:3] == ["az", "acr", "build"]
                   or deploy.FIXTURES_JOB in c and "start" in c for c in azure.calls)
    state = deploy.State.load(tmp_path / "state.json")
    assert state.deployment_status == "succeeded" and not state.learning_attempt
    assert state.learning_receipts["p2-cycle"] == pending["cycle_id"]


def test_explicit_recovery_of_legacy_cycle_runs_finalization_not_another_cycle(tmp_path):
    azure = FakeAzure()
    azure.p2_output = []
    assert deployment(azure, tmp_path) == 1
    path = tmp_path / "state.json"
    state = deploy.State.load(path)
    cycle = state.learning_attempt["cycle_id"]
    state.learning_attempt = {}
    state.save(path)
    azure.p2_output = ['{"cycle_id":"cycle-synthetic","pipeline":"Completed"}']
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive",
                                      "--resume-learning-cycle", cycle]) == 0
    command = next(c for c in azure.calls if "scripts/run_p2_container_jobs.py" in c)
    assert command[command.index("--resume-cycle") + 1] == cycle
    assert not any(deploy.FIXTURES_JOB in c and "start" in c for c in azure.calls)
    assert deploy.State.load(path).learning_receipts["schedule"] == "enabled"


def test_changed_p2_image_cannot_certify_recovered_work_even_on_retry(tmp_path, monkeypatch):
    azure = FakeAzure()
    azure.p2_output = []
    assert deployment(azure, tmp_path) == 1
    monkeypatch.setattr(deploy.SampleDefinition, "runtime_fingerprint", lambda _: "e" * 64)
    azure.voice_image_digest = "sha256:" + "f" * 64
    for _ in range(2):
        azure.calls.clear()
        assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 1
        assert not any("scripts/run_p2_container_jobs.py" in c for c in azure.calls)
        assert azure.env["P2_SCHEDULE_ENABLED"] == "false"
        assert deploy.State.load(tmp_path / "state.json").deployment_status == "pending"


def test_identical_payload_retains_original_image_and_cycle_despite_rebuild_metadata(tmp_path, monkeypatch):
    azure = FakeAzure()
    azure.p2_output = []
    assert deployment(azure, tmp_path) == 1
    before = deploy.State.load(tmp_path / "state.json").learning_attempt
    monkeypatch.setattr(deploy.SampleDefinition, "runtime_fingerprint", lambda _: "e" * 64)
    azure.voice_image_digest = "sha256:" + "f" * 64
    checked = []

    def payload(self, registry, image):
        checked.append(image)
        return "a" * 64

    monkeypatch.setattr(deploy.Deployer, "p2_source_digest", payload)
    azure.p2_output = ['{"cycle_id":"cycle-synthetic","pipeline":"Completed"}']
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 0
    assert checked == [before["image"], before["image"].split("@")[0] + "@sha256:" + "f" * 64]
    state = deploy.State.load(tmp_path / "state.json")
    assert state.learning_receipts["p2-image"] == before["image"]
    assert state.learning_receipts["p2-cycle"] == before["cycle_id"]
    assert azure.env["P2_CONTAINER_IMAGE"] == before["image"]


def test_changed_payload_refuses_cycle_reuse(tmp_path, monkeypatch):
    azure = FakeAzure()
    azure.p2_output = []
    assert deployment(azure, tmp_path) == 1
    monkeypatch.setattr(deploy.SampleDefinition, "runtime_fingerprint", lambda _: "e" * 64)
    azure.voice_image_digest = "sha256:" + "f" * 64
    digests = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(deploy.Deployer, "p2_source_digest", lambda *a: next(digests))
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 1
    assert not any("scripts/run_p2_container_jobs.py" in c for c in azure.calls)
    assert azure.env["P2_SCHEDULE_ENABLED"] == "false"


@pytest.mark.parametrize("output,valid", [
    ("GG_P2_SOURCE_SHA256=" + "a" * 64, True),
    ("missing digest", False),
    (("GG_P2_SOURCE_SHA256=" + "a" * 64 + "\n") * 2, False),
])
def test_image_payload_probe_uses_yaml_safe_literal_and_requires_one_digest(tmp_path, monkeypatch, output, valid):
    azure = FakeAzure()
    image = "crmlsynthetic.azurecr.io/governedgraph/p2@sha256:" + "a" * 64

    def request(args, **kwargs):
        assert args[:3] == ["az", "acr", "run"]
        command = shlex.split(args[args.index("--cmd") + 1])
        assert command[:3] == [image, "python", "-c"]
        code = command[3]
        assert "\n" not in code and ": " not in code
        compile(code, "<payload-probe>", "exec")
        return output

    monkeypatch.setattr(azure, "run", request)
    d = deploy.Deployer(azure, deploy.Prompter(interactive=False), deploy.State(), state_path=tmp_path / "state.json")
    if valid:
        assert d.p2_source_digest("crmlsynthetic", image) == "a" * 64
    else:
        with pytest.raises(deploy.LearningCycleStopped, match="one conclusive"):
            d.p2_source_digest("crmlsynthetic", image)


def test_legacy_cycle_handoff_survives_failure_before_learning(tmp_path, monkeypatch):
    azure = FakeAzure()
    azure.p2_output = []
    assert deployment(azure, tmp_path) == 1
    path = tmp_path / "state.json"
    state = deploy.State.load(path)
    cycle = state.learning_attempt["cycle_id"]
    state.learning_attempt = {}
    state.save(path)
    monkeypatch.setattr(deploy.SampleDefinition, "runtime_fingerprint", lambda _: "e" * 64)
    azure.fail["azd deploy"] = (1, "synthetic deployment interruption")
    assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive",
                                      "--resume-learning-cycle", cycle]) == 1
    assert deploy.State.load(path).learning_attempt["cycle_id"] == cycle
    azure.p2_output = ['{"cycle_id":"cycle-synthetic","pipeline":"Completed"}']
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 0
    command = next(c for c in azure.calls if "scripts/run_p2_container_jobs.py" in c)
    assert command[command.index("--resume-cycle") + 1] == cycle


@pytest.mark.parametrize("field,value", [("subscription_id", "other"), ("resource_group", "other"),
                                        ("sample", "other"), ("cycle_id", "cycle-" + "0" * 16)])
def test_recovery_refuses_mismatched_pending_scope(tmp_path, field, value):
    azure = FakeAzure()
    azure.p2_output = []
    assert deployment(azure, tmp_path) == 1
    path = tmp_path / "state.json"
    state = deploy.State.load(path)
    cycle = state.learning_attempt["cycle_id"]
    state.learning_attempt[field] = value
    state.save(path)
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive",
                                      "--resume-learning-cycle", cycle]) == 1
    assert not any("scripts/run_p2_container_jobs.py" in c for c in azure.calls)
    assert azure.env["P2_SCHEDULE_ENABLED"] == "false"


def test_completion_from_another_cycle_never_enables_schedule(tmp_path):
    azure = FakeAzure()
    azure.p2_output = ['{"cycle_id":"cycle-wrong","pipeline":"Completed"}']
    assert deployment(azure, tmp_path) == 1
    assert azure.env["P2_SCHEDULE_ENABLED"] == "false"
    assert deploy.State.load(tmp_path / "state.json").learning_attempt


def test_schedule_provision_failure_retains_the_original_cycle_for_resume(tmp_path, monkeypatch):
    azure = FakeAzure()
    original = azure.run

    def fail_schedule(args, **kwargs):
        if args[:2] == ["azd", "provision"] and azure.env.get("P2_SCHEDULE_ENABLED") == "true":
            raise deploy.CommandError(args, 1, "synthetic schedule reconciliation failure")
        return original(args, **kwargs)

    monkeypatch.setattr(azure, "run", fail_schedule)
    assert deployment(azure, tmp_path) == 1
    pending = deploy.State.load(tmp_path / "state.json").learning_attempt["cycle_id"]
    monkeypatch.setattr(azure, "run", original)
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 0
    command = next(c for c in azure.calls if "scripts/run_p2_container_jobs.py" in c)
    assert command[command.index("--resume-cycle") + 1] == pending


@pytest.mark.parametrize("flags", [[], ["--mode", "fresh"], ["--mode", "redeploy"],
                                  ["--mode", "resume", "--only", "learning"], ["--mode", "resume", "--force"]])
def test_explicit_recovery_rejects_unsafe_deployment_modes_before_mutation(tmp_path, flags):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--yes", "--non-interactive", "--resume-learning-cycle",
                                      "cycle-" + "a" * 16, *flags]) == 1
    assert azure.calls == []


def test_failed_acceptance_preserves_deployment_and_retry_never_redeploys(tmp_path, capsys):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    before = deploy.State.load(tmp_path / "state.json")
    capsys.readouterr()
    azure.fail["scripts.run_foundry_evals"] = (1, "synthetic failed score")
    azure.calls.clear()
    assert accept(azure, tmp_path) == 1
    failed = deploy.State.load(tmp_path / "state.json")
    assert failed.deployment_status == before.deployment_status == "succeeded"
    assert failed.deployment_id == before.deployment_id
    assert failed.acceptance["status"] == "failed"
    evidence = json.loads(Path(failed.acceptance["evidence"]).read_text())
    assert evidence["status"] == "failed"
    assert "Deployment complete." not in capsys.readouterr().out
    assert accept(azure, tmp_path) == 0
    passed = deploy.State.load(tmp_path / "state.json")
    assert passed.acceptance["status"] == "passed"
    assert passed.acceptance["evidence"] != failed.acceptance["evidence"]
    assert json.loads(Path(failed.acceptance["evidence"]).read_text()) == evidence
    assert not any(c[:2] in (["azd", "deploy"], ["azd", "provision"])
                   or "create_agents.py" in " ".join(c) and "--apply" in c or "run_p2_container_jobs.py" in " ".join(c)
                   for c in azure.calls)


def test_evaluation_contract_changes_do_not_invalidate_services_or_learning(tmp_path, monkeypatch):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    previous = deploy.State.load(tmp_path / "state.json")
    azure.calls.clear()
    monkeypatch.setattr(deploy.SampleDefinition, "fingerprint", lambda _: "c" * 64)
    assert deployment(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    current = deploy.State.load(tmp_path / "state.json")
    assert current.deployment_id == previous.deployment_id
    assert current.learning_receipts == previous.learning_receipts
    assert any("pension_wtp.evaluation.run_evals" in c for c in azure.calls)
    assert not any(c[:2] in (["azd", "deploy"], ["azd", "provision"])
                   or "create_agents.py" in " ".join(c) and "--apply" in c or "run_p2_container_jobs.py" in " ".join(c)
                   for c in azure.calls)


def test_acceptance_status_is_bound_to_contract_and_agent_pins(tmp_path, monkeypatch, capsys):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    assert accept(azure, tmp_path) == 0
    capsys.readouterr()
    azure.calls.clear()
    assert accept(azure, tmp_path, ["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["acceptance_status"] == "passed"
    assert all(c[:3] == ["azd", "env", "get-values"] for c in azure.calls)
    azure.env["AZURE_AI_AGENT_VERSION"] = "999"
    assert accept(azure, tmp_path, ["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["acceptance_status"] == "stale"
    azure.env["AZURE_AI_AGENT_VERSION"] = "1"
    monkeypatch.setattr(deploy.SampleDefinition, "fingerprint", lambda _: "c" * 64)
    assert accept(azure, tmp_path, ["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["acceptance_status"] == "stale"
    azure.calls.clear()
    assert accept(azure, tmp_path) == 0
    assert any("pension_wtp.evaluation.run_evals" in c for c in azure.calls)
    assert not any(c[:2] in (["azd", "deploy"], ["azd", "provision"]) for c in azure.calls)


@pytest.mark.parametrize("key", ["AZURE_ENV_NAME", "AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP",
                                     "AZURE_LOCATION", "GOVERNEDGRAPH_NAMING_SEED", "GOVERNEDGRAPH_SAMPLE"])
def test_acceptance_refuses_redirected_scope_without_mutating_it(tmp_path, key):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    path = tmp_path / "state.json"
    before = path.read_bytes()
    azure.env[key] = "different"
    azure.calls.clear()
    assert accept(azure, tmp_path) == 1
    assert path.read_bytes() == before
    assert all(c[:3] == ["azd", "env", "get-values"] for c in azure.calls)


def test_acceptance_refuses_undeployed_runtime_changes(tmp_path, monkeypatch):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    before = (tmp_path / "state.json").read_bytes()
    monkeypatch.setattr(deploy.SampleDefinition, "runtime_fingerprint", lambda _: "e" * 64)
    azure.calls.clear()
    assert accept(azure, tmp_path) == 1
    assert (tmp_path / "state.json").read_bytes() == before
    assert not any("scripts.run_foundry_evals" in c or "scripts.run_dev_proof" in c for c in azure.calls)


def test_legacy_step_receipts_do_not_fabricate_current_acceptance(tmp_path, capsys):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    path = tmp_path / "state.json"
    state = json.loads(path.read_text())
    state["completed"] += ["verify", "evaluations"]
    state["channel_proof_execution"] = "synthetic-old-proof"
    for key in ("runtime_fingerprint", "deployment_id", "deployment_status"):
        state.pop(key)
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    capsys.readouterr()
    assert accept(azure, tmp_path, ["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["acceptance_status"] == "not_run"
    assert accept(azure, tmp_path) == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("only", ["verify", "evaluations"])
def test_partial_checks_never_certify_full_acceptance(tmp_path, only):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    assert accept(azure, tmp_path) == 0
    assert accept(azure, tmp_path, ["--only", only]) == 0
    current = deploy.State.load(tmp_path / "state.json")
    assert current.deployment_status == "succeeded"
    assert current.acceptance["status"] == "incomplete"
    assert set(current.acceptance["checks"]) == {only}


def test_evaluator_unavailable_is_distinct_from_failed_scores_and_keeps_evidence(tmp_path, monkeypatch):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    original = azure.run

    def unavailable(args, **kwargs):
        if "scripts.run_foundry_evals" in args:
            output = Path(args[args.index("--output") + 1])
            output.write_text(json.dumps({"evaluation_status": "unavailable", "evaluation_passed": False,
                                          "foundry": {"eval_id": "retained-eval", "run_id": "retained-run"}}))
            raise deploy.CommandError(args, 2, "FAILED_EXECUTION")
        return original(args, **kwargs)

    monkeypatch.setattr(azure, "run", unavailable)
    assert accept(azure, tmp_path) == 2
    state = deploy.State.load(tmp_path / "state.json")
    assert state.deployment_status == "succeeded"
    assert state.acceptance["status"] == "unavailable"
    assert json.loads(Path(state.acceptance["foundry_report"]).read_text())["foundry"]["run_id"] == "retained-run"


@pytest.mark.parametrize("change", ["missing", "malformed", "wrong_project", "wrong_pins", "no_run_id",
                                       "failed", "unavailable", "invalid_shape"])
def test_zero_exit_without_valid_passing_evidence_is_not_acceptance(tmp_path, monkeypatch, change):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    original = azure.run

    def invalid_report(args, **kwargs):
        result = original(args, **kwargs)
        if "scripts.run_foundry_evals" in args:
            output = Path(args[args.index("--output") + 1])
            if change == "missing":
                output.unlink()
                return result
            if change == "malformed":
                output.write_text("invalid json")
                return result
            report = json.loads(output.read_text())
            if change == "wrong_project":
                report["project_endpoint"] = "https://different.invalid"
            elif change == "wrong_pins":
                report["versions"]["gg-counsellor"] = "999"
            elif change == "no_run_id":
                report["foundry"].pop("run_id")
            elif change == "failed":
                report["evaluation_passed"] = False
            elif change == "unavailable":
                report["evaluation_status"] = "unavailable"
            elif change == "invalid_shape":
                report = []
            output.write_text(json.dumps(report))
        return result

    monkeypatch.setattr(azure, "run", invalid_report)
    assert accept(azure, tmp_path) == 1
    state = deploy.State.load(tmp_path / "state.json")
    assert state.acceptance["status"] == "failed" and state.deployment_status == "succeeded"


def test_deployment_change_during_acceptance_cannot_be_overwritten_or_certified(tmp_path, monkeypatch):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    path = tmp_path / "state.json"
    original = azure.run

    def replacement(args, **kwargs):
        result = original(args, **kwargs)
        if "scripts.run_foundry_evals" in args:
            state = deploy.State.load(path)
            state.deployment_id = "replacement"
            state.deployment_status = "pending"
            state.save(path)
        return result

    monkeypatch.setattr(azure, "run", replacement)
    assert accept(azure, tmp_path) == 1
    state = deploy.State.load(path)
    assert state.deployment_id == "replacement" and state.deployment_status == "pending"
    attempts = [json.loads(p.read_text()) for p in (tmp_path / "acceptance").glob("*.json")
                if not p.name.endswith("-foundry.json")]
    assert len(attempts) == 1 and attempts[0]["status"] == "failed"


def test_legacy_partial_plan_never_runs_acceptance(tmp_path, capsys):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    before = (tmp_path / "state.json").read_bytes()
    azure.calls.clear()
    assert deployment(azure, tmp_path, ["--plan", "--only", "verify", "--mode", "resume"]) == 0
    assert azure.calls == [] and (tmp_path / "state.json").read_bytes() == before


def test_failed_voice_proof_retains_both_execution_ids_without_certifying_channel_only(tmp_path):
    azure = FakeAzure()
    assert deployment(azure, tmp_path) == 0
    azure.voice_proof_status = "Failed"
    assert accept(azure, tmp_path) == 1
    state = deploy.State.load(tmp_path / "state.json")
    assert not state.sample_proof_executions and not state.channel_proof_execution
    assert state.acceptance["checks"]["verify"]["executions"] == {
        "channel": azure.proof_output.strip(), "voice": azure.voice_proof_output.strip(),
    }
    assert state.acceptance["status"] == "failed"


def test_capture_acceptance_cannot_use_conversations_from_before_this_attempt(tmp_path):
    azure = FakeAzure()
    assert deployment(azure, tmp_path, DEFAULT_ARGS) == 0
    assert azure.capture_queries == []
    assert accept(azure, tmp_path) == 0
    state = deploy.State.load(tmp_path / "state.json")
    command = azure.capture_queries[-1]
    query = command[command.index("--analytics-query") + 1]
    assert f"datetime({state.acceptance['started_at']})" in query
    assert "ago(3h)" not in query
