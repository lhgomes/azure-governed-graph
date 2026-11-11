"""Delivery contracts, not a state-machine test runner. Runtime boundary suites remain controlling."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap, delivery, deploy
from scripts.azd_env import EnvError

COMPOSITION = delivery.Composition.load(delivery.ROOT / "examples/pension_wtp/deployment.json")


@pytest.fixture
def command_processes(monkeypatch):
    state = SimpleNamespace(clock=0, outcomes=[], calls=[], killed=[], delays=[], processes=[])

    class Process:
        def __init__(self, args, **kwargs):
            self.pid = 700 + len(state.calls)
            self.returncode = None
            self.waits = []
            self.drained = False
            self.outcome = state.outcomes.pop(0)
            state.calls.append((args, kwargs))
            state.processes.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            assert self.returncode is not None

        def communicate(self, timeout=None):
            self.waits.append(timeout)
            if timeout is None:
                self.drained = True
                self.returncode = -delivery.signal.SIGKILL
                return "private partial output", "private partial diagnostics"
            if self.outcome == "hang":
                state.clock += timeout
                raise delivery.subprocess.TimeoutExpired("az", timeout)
            if isinstance(self.outcome, KeyboardInterrupt):
                raise self.outcome
            self.returncode, stdout, stderr = self.outcome
            return stdout, stderr

    monkeypatch.setattr(delivery.subprocess, "Popen", Process)
    monkeypatch.setattr(delivery.os, "killpg", lambda pid, sig: state.killed.append((pid, sig)))
    monkeypatch.setattr(delivery.time, "monotonic", lambda: state.clock)
    monkeypatch.setattr(delivery.time, "sleep", state.delays.append)
    return state


def test_command_runner_preserves_output_and_hides_private_arguments(command_processes, capsys):
    command_processes.outcomes.append((0, '  {"result":"private output"}\n', "private diagnostics"))
    args = ["azd", "env", "set", "PRIVATE_SETTING", "private argument"]
    env = {"PRIVATE_SETTING": "private environment"}
    assert delivery.Runner().run(args, env=env) == '{"result":"private output"}'
    assert command_processes.calls == [(args, {
        "cwd": delivery.ROOT, "env": env, "stdout": delivery.subprocess.PIPE,
        "stderr": delivery.subprocess.PIPE, "text": True, "start_new_session": True,
    })]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Running azd env set" in captured.err and "Finished azd env set (exit 0)" in captured.err
    assert "private" not in captured.err and "PRIVATE_SETTING" not in captured.err


@pytest.mark.parametrize("args,label", [
    (["az", "acr", "repository", "list", "-n", "synthetic-registry", "-o", "json"], "az acr repository list"),
    (["az", "account", "show", "--subscription", "selected-subscription"], "az account show"),
])
def test_stalled_read_is_killed_and_retried_with_bounded_backoff(command_processes, capsys, args, label):
    command_processes.outcomes.extend(["hang", "hang", (0, "[]\n", "")])
    assert delivery.Runner().run(args) == "[]"
    assert [call[0] for call in command_processes.calls] == [args] * 3
    assert command_processes.delays == [2, 4]
    assert command_processes.killed == [(700, delivery.signal.SIGKILL), (701, delivery.signal.SIGKILL)]
    for process in command_processes.processes[:2]:
        assert process.waits == [30, 30, 30, 30, None]
        assert process.drained
    captured = capsys.readouterr()
    assert f"Still waiting for {label} (30s elapsed, limit 120s)" in captured.err
    assert "attempt 3/3" in captured.err and "retrying read-only command" in captured.err
    assert "private partial" not in captured.err


@pytest.mark.parametrize("check", [True, False])
def test_exhausted_read_timeouts_never_return_partial_success(command_processes, check):
    command_processes.outcomes.extend(["hang"] * 3)
    with pytest.raises(delivery.CommandTimeout, match="timed out after 120s"):
        delivery.Runner().run(["az", "acr", "repository", "list"], check=check)
    assert len(command_processes.calls) == len(command_processes.killed) == 3
    assert all(process.drained for process in command_processes.processes)
    assert command_processes.delays == [2, 4]


@pytest.mark.parametrize("args,timeout", [
    (["az", "acr", "build"], 3600),
    (["az", "deployment", "sub", "create"], 7200),
    (["az", "deployment", "group", "create"], 7200),
    (["az", "containerapp", "job", "start"], 120),
    (["az", "network", "private-endpoint-connection", "approve"], 120),
    (["azd", "env", "set", "PRIVATE_SETTING", "private argument"], 120),
    (["az", "unknown", "operation"], 120),
])
def test_timed_out_writes_and_unknown_commands_are_never_retried(command_processes, args, timeout):
    command_processes.outcomes.append("hang")
    with pytest.raises(delivery.CommandTimeout, match=f"timed out after {timeout}s") as error:
        delivery.Runner().run(args)
    assert "may still be running" in str(error.value)
    assert len(command_processes.calls) == 1
    assert command_processes.clock == timeout
    assert command_processes.delays == []
    assert command_processes.processes[0].drained
    assert command_processes.killed == [(700, delivery.signal.SIGKILL)]


@pytest.mark.parametrize("check", [True, False])
def test_nonzero_command_exit_preserves_check_semantics_without_retries(command_processes, capsys, check):
    command_processes.outcomes.append((2, "diagnostic output", "AuthorizationFailed"))
    args = ["az", "acr", "repository", "list"]
    if check:
        with pytest.raises(delivery.CommandError, match="AuthorizationFailed") as error:
            delivery.Runner().run(args)
        assert error.value.args_ == args and error.value.code == 2
    else:
        assert delivery.Runner().run(args, check=False) == "diagnostic output"
        assert "Finished az acr repository list (exit 2)" in capsys.readouterr().err
    assert len(command_processes.calls) == 1
    assert command_processes.killed == command_processes.delays == []


def test_interrupt_cleans_up_local_command_without_retry(command_processes):
    command_processes.outcomes.append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        delivery.Runner().run(["az", "acr", "repository", "list"])
    assert command_processes.killed == [(700, delivery.signal.SIGKILL)]
    assert command_processes.processes[0].drained
    assert command_processes.delays == []


@pytest.mark.parametrize("failure,exit_code,message", [
    (KeyboardInterrupt(), 130, "Delivery interrupted"),
    (delivery.CommandTimeout("az acr build", 3600), 1, "Delivery failed: az acr build timed out after 3600s"),
])
def test_deploy_stops_cleanly_with_unknown_cloud_outcome(private_delivery, monkeypatch, capsys,
                                                        failure, exit_code, message):
    path, raw = private_delivery
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(delivery.Composition, "load", lambda _: COMPOSITION)

    def interrupted(*_, **__):
        raise failure

    monkeypatch.setattr(deploy, "select_target", interrupted)
    assert deploy.main(["--inputs", str(path)]) == exit_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err and "may still be running" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("group", [False, True])
def test_provision_places_the_scope_action_before_scope_arguments(tmp_path, monkeypatch, group):
    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    calls = []
    class Runner:
        def run(self, args):
            calls.append(args)
            return json.dumps({"properties": {"provisioningState": "Succeeded",
                                             "outputs": {"result": {"value": "ok"}}}})
    inputs = SimpleNamespace(subscription="synthetic", resource_group="rg-gg-dev", location="swedencentral")
    assert delivery.provision(Runner(), inputs, "infra/main.bicep", {}, group=group) == {"result": "ok"}
    assert calls[0][:4] == ["az", "deployment", "group" if group else "sub", "create"]
    assert calls[0][4:6] == (["--resource-group", "rg-gg-dev"] if group else ["--location", "swedencentral"])
    suffix = hashlib.sha256(b"rg-gg-dev").hexdigest()[:12]
    assert calls[0][calls[0].index("--name") + 1] == ("gg-main" if group else f"gg-main-{suffix}")


def test_command_failure_includes_bounded_operator_diagnostics():
    error = delivery.CommandError(["az", "deployment"], 2, "invalid command")
    assert "invalid command" in str(error)


@pytest.mark.parametrize("diagnostic,retries", [
    ("InvalidParameterValueInContainerTemplate: unable to pull image using Managed identity", 3),
    ("AuthorizationFailed", 0),
])
def test_provision_retries_only_bounded_identity_image_pull_propagation(tmp_path, monkeypatch, diagnostic, retries):
    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    delays, calls = [], []
    monkeypatch.setattr(delivery.time, "sleep", delays.append)
    class Runner:
        def run(self, args):
            calls.append(args)
            raise delivery.CommandError(args, 1, diagnostic)
    inputs = SimpleNamespace(subscription="synthetic", resource_group="rg-gg-dev", location="swedencentral")
    with pytest.raises(delivery.CommandError):
        delivery.provision(Runner(), inputs, "infra/bootstrap.bicep", {})
    assert len(calls) == retries + 1
    assert all(call == calls[0] for call in calls)
    assert delays == ([30, 60, 120] if retries else [])


def test_each_registry_build_uses_only_its_own_dependency_family(monkeypatch):
    calls = []
    def build(_runner, registry, target, dockerfile, paths, args, *, subscription):
        assert subscription == "selected-subscription"
        calls.append((registry, target, dockerfile, args))
        return {"image": registry + "/" + target}
    monkeypatch.setattr(deploy, "build", build)
    deploy.image_artifacts(None, {"compute": {"registryName": "runtime-registry"},
                                "learning": {"mlRegistryId": "/p2-registry"}}, COMPOSITION,
                           subscription="selected-subscription")
    assert len(calls) == 6
    for registry, target, dockerfile, args in calls:
        if registry == "p2-registry":
            assert dockerfile.endswith("Dockerfile.p2")
            assert "FRAMEWORK_RUNTIME" not in args
            assert "runtime-registry" not in " ".join(args.values())
        else:
            assert "FRAMEWORK_P2" not in args
            assert "p2-registry" not in " ".join(args.values())


def test_initializer_receives_existing_keyless_telemetry_without_widening_signer_authority(monkeypatch):
    calls = []
    foundation = {
        "security": {key: "synthetic-" + key for key in (
            "pipelineIdentityId", "pipelineClientId", "pipelinePrincipalId",
            "strategyIdentityId", "strategyClientId", "strategyPrincipalId", "keyVaultUri", "signingKeyName",
        )},
        "compute": {"registryName": "runtime-registry", "environmentId": "existing-environment"},
        "learning": {"mlRegistryId": "/ml-registry"},
        "data": {"contentBlobEndpoint": "https://synthetic.blob.core.windows.net"},
        "monitoring": {"appInsightsRouting": "synthetic-routing-metadata"},
        "ai": {"projectResourceId": "/synthetic/approved-project"},
    }
    inputs = SimpleNamespace(location="swedencentral")
    images = {role: {"image": "synthetic-" + role} for role in ("runtime", "p2")}

    def provision(runner, inputs, template, values, *, group):
        assert template == "infra/bootstrap-job.bicep" and group is True
        calls.append(values)

    monkeypatch.setattr(bootstrap, "provision", provision)
    monkeypatch.setattr(bootstrap, "run_job", lambda runner, inputs, name: name + "-execution")
    monkeypatch.setattr(bootstrap, "public_result", lambda runner, inputs, name, execution: (
        {"snapshot": {"snapshot_id": "signed-expert-priors"}} if name.endswith("-sign")
        else {"initialized": False, "snapshot_id": "preserved-learned-snapshot"}))
    result = bootstrap.initial_priors(None, inputs, COMPOSITION, foundation, images)
    sign, initialize = calls
    assert sign["identityId"] == foundation["security"]["pipelineIdentityId"]
    assert initialize["identityId"] == foundation["security"]["strategyIdentityId"]
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" not in sign["env"]
    assert "AZURE_AI_PROJECT_RESOURCE_ID" not in sign["env"]
    assert "GOVERNEDGRAPH_INITIAL_SNAPSHOT" not in sign["env"]
    assert initialize["env"]["APPLICATIONINSIGHTS_CONNECTION_STRING"] == foundation["monitoring"]["appInsightsRouting"]
    assert initialize["env"]["AZURE_AI_PROJECT_RESOURCE_ID"] == foundation["ai"]["projectResourceId"]
    assert initialize["env"]["GOVERNEDGRAPH_SERVICE_NAME"] == "gg-bootstrap-initialize"
    assert initialize["env"]["GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS"] == "false"
    assert json.loads(initialize["env"]["GOVERNEDGRAPH_INITIAL_SNAPSHOT"]) == {"snapshot_id": "signed-expert-priors"}
    assert result["initialize"]["initialized"] is False
    assert result["initialize"]["snapshot_id"] == "preserved-learned-snapshot"


def test_entra_bootstrap_imports_modules_and_binds_exact_recovery_scope(monkeypatch):
    import os
    from governedgraph.strategy_service import provision_auth
    from scripts import configure_admin_console, configure_contact_centre, configure_strategy_recovery, configure_voice_demo
    monkeypatch.setenv("STRATEGY_RECOVERY_OPERATOR_ID", "stale-operator")
    monkeypatch.setenv("STRATEGY_API_CLIENT_ID", "stale-api")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "selected-subscription")
    def strategy(*args, **kwargs):
        assert os.environ["STRATEGY_RECOVERY_OPERATOR_ID"] == "approved-operator"
        return "exact-api"
    def recovery(client, operator, *, apply):
        assert apply and client == os.environ["STRATEGY_API_CLIENT_ID"] == "exact-api"
        assert operator == os.environ["STRATEGY_RECOVERY_OPERATOR_ID"] == "approved-operator"
    monkeypatch.setattr(provision_auth, "configure", strategy)
    monkeypatch.setattr(configure_strategy_recovery, "configure", recovery)
    monkeypatch.setattr(configure_contact_centre, "configure",
                        lambda *args, **kwargs: {"client_id": "contact", "audience": "contact"})
    monkeypatch.setattr(configure_admin_console, "configure", lambda *args, **kwargs: {
        "client_id": "admin", "reader_group_id": "reader", "operator_group_id": "operator"})
    monkeypatch.setattr(configure_voice_demo, "configure", lambda *args, **kwargs: {"client_id": "voice"})
    inputs = SimpleNamespace(subscription="selected-subscription", operator_id="approved-operator", environment="gg-dev",
                             naming_seed="synthetic", voice_demo=True)
    security = {key: "synthetic" for key in ("pipelinePrincipalId", "fairnessPrincipalId",
                                           "runtimePrincipalId", "adminPrincipalId")}
    assert bootstrap.entra(inputs, {"security": security, "gateway": {"gatewayUrl": "https://synthetic.invalid"}})[
        "strategyApiClientId"] == "exact-api"


@pytest.mark.parametrize("subscription", ["", "other-subscription"])
def test_entra_bootstrap_refuses_missing_or_different_subscription_before_directory_calls(monkeypatch, subscription):
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", subscription)
    with pytest.raises(delivery.DeliveryError, match="selected delivery subscription"):
        bootstrap.entra(SimpleNamespace(subscription="selected-subscription"), {})


def writer_plans():
    from scripts.create_agents import provision

    return {role: {**provision(None, apply=False, role=role, agent_name=delivery.AGENT_NAMES[role]), "agent_version": "17"}
            for role in delivery.AGENT_ROLES}


def test_azure_member_values_requires_explicit_apim_endpoint(monkeypatch):
    from governedgraph.agents import clients
    from types import SimpleNamespace

    monkeypatch.setattr(clients, "settings", SimpleNamespace(member_values_url=""))
    with pytest.raises(ValueError, match="MEMBER_VALUES_API_URL"):
        clients.default_values_client()
    assert not hasattr(clients, "LocalValuesClient")


def test_each_member_facing_role_has_its_own_identity_and_values_have_only_registry_pull():
    security = (delivery.ROOT / "infra/modules/security.bicep").read_text()
    compute = (delivery.ROOT / "infra/modules/compute.bicep").read_text()
    assert "resource memberValuesId " in security
    assert "memberValuesIdentityId: security.outputs.memberValuesIdentityId" in (
        delivery.ROOT / "infra/main.bicep").read_text()
    assert "identityId: memberValuesIdentityId, clientId: memberValuesClientId" in compute
    assert "identityId: runtimeIdentityId, clientId: runtimeIdentityClientId" in compute
    assert "resource memberValuesAcrPull " in compute
    assert "scope: acr" in compute.split("resource memberValuesAcrPull ", 1)[1].split("resource ", 1)[0]
    for path in (delivery.ROOT / "infra").rglob("*.bicep"):
        if path.name not in {"security.bicep", "compute.bicep", "main.bicep", "workloads.bicep"}:
            assert "memberValuesPrincipalId" not in path.read_text()


@pytest.fixture
def private_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    path = tmp_path / "inputs.json"
    raw = {"subscription": "00000000-0000-0000-0000-000000000001",
        "naming_seed": "00000000-0000-0000-0000-000000000002",
        "operator_id": "00000000-0000-0000-0000-000000000003", "on_call_email": "synthetic@example.invalid",
        "composition": "examples/pension_wtp/deployment.json"}
    return path, raw


@pytest.mark.parametrize("resource_group", [None, "rg-gg-dev-test"])
def test_plan_runs_nothing(private_delivery, monkeypatch, capsys, resource_group):
    path, raw = private_delivery
    if resource_group is not None:
        raw["resource_group"] = resource_group
    content = json.dumps(raw)
    path.write_text(content)
    monkeypatch.setattr(delivery.Composition, "load", lambda _: COMPOSITION)
    monkeypatch.setattr(delivery.Runner, "run", lambda *_args, **_kwargs: pytest.fail("plan must not call cloud"))
    inputs = delivery.Inputs.load(path)
    suffix = hashlib.sha256(bytes.fromhex("00000000000000000000000000000002")).hexdigest()[:12]
    assert inputs.resource_group == (resource_group or f"rg-gg-dev-{suffix}")
    assert delivery.Inputs.load(path) == inputs
    assert deploy.main(["--inputs", str(path), "--plan"]) == 0
    assert json.loads(capsys.readouterr().out)["sequence"] == list(deploy.SEQUENCE)
    assert path.read_text() == content


def test_generated_resource_group_changes_only_with_the_naming_seed(private_delivery):
    path, raw = private_delivery
    path.write_text(json.dumps(raw))
    first = delivery.Inputs.load(path)
    raw["naming_seed"] = "00000000000000000000000000000002"
    raw["on_call_email"] = "other@example.invalid"
    path.write_text(json.dumps(raw))
    assert delivery.Inputs.load(path).resource_group == first.resource_group
    raw["naming_seed"] = "00000000-0000-0000-0000-000000000004"
    path.write_text(json.dumps(raw))
    second = delivery.Inputs.load(path)
    suffix = hashlib.sha256(bytes.fromhex("00000000000000000000000000000004")).hexdigest()[:12]
    assert second.resource_group == f"rg-gg-dev-{suffix}"
    assert second.resource_group != first.resource_group


@pytest.mark.parametrize("resource_group", ["", "invalid/group"])
def test_explicit_invalid_resource_group_is_not_replaced(private_delivery, resource_group):
    path, raw = private_delivery
    raw["resource_group"] = resource_group
    path.write_text(json.dumps(raw))
    with pytest.raises(delivery.DeliveryError, match="invalid resource group"):
        delivery.Inputs.load(path)


@pytest.fixture
def saved_delivery(private_delivery):
    path, raw = private_delivery
    path.write_text(json.dumps(raw))
    saved = {
        "AZURE_SUBSCRIPTION_ID": raw["subscription"],
        "GOVERNEDGRAPH_NAMING_SEED": raw["naming_seed"],
        "AZURE_RESOURCE_GROUP": "rg-gg-dev-00000000000000000000000000000002",
    }
    env_file = delivery.ROOT / ".azure" / "gg-dev" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("".join(f"{key}={json.dumps(value)}\n" for key, value in saved.items()))
    return path, raw, env_file, saved


def test_omitted_group_preserves_the_saved_scope_instead_of_renaming_it(saved_delivery):
    path, _, env_file, saved = saved_delivery
    before_inputs, before_env = path.read_text(), env_file.read_text()
    assert delivery.Inputs.load(path).resource_group == saved["AZURE_RESOURCE_GROUP"]
    assert delivery.Inputs.load(path).resource_group == saved["AZURE_RESOURCE_GROUP"]
    assert path.read_text() == before_inputs and env_file.read_text() == before_env


@pytest.mark.parametrize("key", ["AZURE_SUBSCRIPTION_ID", "GOVERNEDGRAPH_NAMING_SEED"])
@pytest.mark.parametrize("value", [None, "00000000-0000-0000-0000-000000000004"])
def test_saved_resource_group_cannot_be_reused_with_mismatched_or_missing_scope(saved_delivery, key, value):
    path, _, env_file, saved = saved_delivery
    if value is None:
        del saved[key]
    else:
        saved[key] = value
    env_file.write_text("".join(f"{name}={json.dumps(item)}\n" for name, item in saved.items()))
    with pytest.raises(delivery.DeliveryError, match=key):
        delivery.Inputs.load(path)


def test_explicit_group_change_is_still_refused_before_any_azd_write(saved_delivery, monkeypatch):
    from scripts import azd_env

    path, raw, _, saved = saved_delivery
    raw["resource_group"] = "rg-gg-dev-other"
    path.write_text(json.dumps(raw))
    inputs = delivery.Inputs.load(path)
    assert inputs.resource_group == "rg-gg-dev-other"
    monkeypatch.setattr(deploy, "ROOT", delivery.ROOT)
    monkeypatch.setattr(azd_env, "load", lambda **_: saved)
    monkeypatch.setattr(delivery.Runner, "run", lambda *_args, **_kwargs: pytest.fail("scope conflict must not write"))
    with pytest.raises(delivery.DeliveryError, match="scope differs"):
        deploy.environment(delivery.Runner(), inputs, COMPOSITION)


def test_no_obsolete_deployment_state_or_acceptance_hooks():
    source = Path(deploy.__file__).read_text()
    assert not any(value in source for value in ("deploy-state.json", "STEPS", "pytest", "run_evals", "run_demo",
                                                 "run_dev_proof", "--mode", "hello", "step_learning"))
    assert len([line for line in source.splitlines() if line.strip()]) <= 250
    assert not (delivery.ROOT / "scripts/acceptance.py").exists()


def test_b8_the_release_approval_binds_only_the_new_deployments_immutable_writer_versions():
    results = writer_plans()
    record = bootstrap.approved_writers(COMPOSITION, results, "https://approved.services.ai.azure.com/api/projects/test")
    assert record["tier"] == "tier2" and record["approved"] is True
    assert {value["version"] for value in record["writers"].values()} == {"17"}
    assert record["project_endpoint"] == "https://approved.services.ai.azure.com/api/projects/test"


@pytest.mark.parametrize("role", ["counsellor", "voice-compact"])
def test_b8_writer_approval_cannot_relabel_a_differently_named_agent(role):
    results = writer_plans()
    results[role]["agent_name"] = "legacy-synthetic-writer"
    with pytest.raises(delivery.DeliveryError, match="writer name"):
        bootstrap.approved_writers(COMPOSITION, results, "https://approved.services.ai.azure.com/api/projects/test")


def test_b8_the_sample_authorization_covers_only_its_exact_shipped_writer_definitions():
    results = writer_plans()
    approval = json.loads(COMPOSITION.release_approval.read_text())
    assert approval["writers"]["gg-counsellor"]["definition_sha256"] == results["counsellor"]["definition_sha256"]
    assert approval["writers"]["gg-counsellor-voice-compact"]["definition_sha256"] == results["voice-compact"]["definition_sha256"]


@pytest.mark.parametrize("change", [
    lambda value: value.update(approved=False),
    lambda value: value.update(tier="tier1"),
    lambda value: value.update(decision_ref="automatic"),
    lambda value: value.update(evidence_ref=""),
    lambda value: value.update(extra="unknown"),
    lambda value: value["writers"]["gg-counsellor"].update(definition_sha256="0" * 64),
    lambda value: value["writers"]["gg-counsellor"].update(version="latest"),
])
def test_b7_b8_release_approval_is_closed_and_has_no_numeric_version_exception(tmp_path, change):
    approval = json.loads(COMPOSITION.release_approval.read_text())
    change(approval)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval))
    sample = SimpleNamespace(release_approval=path)
    with pytest.raises((delivery.DeliveryError, EnvError, ValueError)):
        bootstrap.approved_writers(sample, writer_plans(), "https://approved.services.ai.azure.com/api/projects/test")


def test_b7_b8_duplicate_approval_fields_and_malformed_files_fail_closed(tmp_path):
    path = tmp_path / "approval.json"
    for content in ('{"tier":"tier2","tier":"tier1"}', "not-json"):
        path.write_text(content)
        with pytest.raises((EnvError, ValueError)):
            bootstrap.approved_writers(SimpleNamespace(release_approval=path), writer_plans(),
                                       "https://approved.services.ai.azure.com/api/projects/test")


@pytest.mark.parametrize("status", ["Unknown", "", "Failed", "Stopped", "Canceled"])
def test_bootstrap_job_unknown_or_unsuccessful_status_never_certifies_completion(status):
    class Runner:
        def run(self, args):
            return "[]" if "list" in args else "job-gg-bootstrap-test-execution" if "start" in args else status
    with pytest.raises(delivery.DeliveryError):
        delivery.run_job(Runner(), SimpleNamespace(subscription="synthetic", resource_group="rg-gg-dev"),
                         "job-gg-bootstrap-test", sleep=lambda _: None)


def test_the_member_key_is_taken_only_if_it_is_a_pure_public_key():
    values = {"kty": "RSA", "alg": "RS256", "use": "sig", "n": "AQAB", "e": "AQAB"}
    inputs = SimpleNamespace(subscription="synthetic", resource_group="rg-gg-dev")
    class Runner:
        def run(self, _args):
            return json.dumps(values)
    assert bootstrap.member_public_jwk(Runner(), inputs, "job", "execution") == values
    for field in ("d", "p", "q", "dp", "dq", "qi", "secret"):
        bad = copy.deepcopy(values)
        bad[field] = "private"
        original = values
        values = bad
        with pytest.raises(delivery.DeliveryError):
            bootstrap.member_public_jwk(Runner(), inputs, "job", "execution")
        values = original


def test_immutable_build_is_reused_without_another_build(capsys, monkeypatch):
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "ambient-subscription")
    calls = []
    class Runner:
        def run(self, args, **_kwargs):
            calls.append(args)
            if "list" in args:
                return '["governedgraph/framework-runtime"]'
            if "show-tags" in args:
                return json.dumps([delivery.source_digest(
                    [delivery.ROOT / "requirements-azure.txt", delivery.ROOT / "Dockerfile",
                     delivery.ROOT / ".dockerignore"], {"target": "runtime", "args": {}})])
            return "sha256:" + "a" * 64
    result = delivery.build(Runner(), "crsynthetic", "runtime", "Dockerfile",
                            [delivery.ROOT / "requirements-azure.txt"], {}, subscription="selected-subscription")
    assert result["image"].endswith("@sha256:" + "a" * 64)
    assert len(calls) == 3 and not any("build" in call for call in calls)
    assert all(call[call.index("--subscription") + 1] == "selected-subscription" for call in calls)
    assert capsys.readouterr().out == "Reusing governedgraph/framework-runtime...\n"


def test_registry_observation_failure_never_starts_a_build():
    class Runner:
        def run(self, args):
            assert "build" not in args
            raise delivery.CommandError(args, 1, "network unavailable")
    with pytest.raises(delivery.CommandError):
        delivery.build(Runner(), "crsynthetic", "runtime", "Dockerfile", [], {},
                       subscription="selected-subscription")


def test_absent_immutable_image_is_built_exactly_once(capsys, monkeypatch):
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "ambient-subscription")
    calls = []
    class Runner:
        def run(self, args):
            calls.append(args)
            return "[]" if "list" in args else "sha256:" + "b" * 64 if "show" in args else ""
    result = delivery.build(Runner(), "crsynthetic", "runtime", "Dockerfile", [], {},
                            subscription="selected-subscription")
    assert sum("build" in call for call in calls) == 1
    assert all(call[call.index("--subscription") + 1] == "selected-subscription" for call in calls)
    assert result["image"].endswith("@sha256:" + "b" * 64)
    assert len(result["source_sha256"]) == 64
    assert capsys.readouterr().out == "Building governedgraph/framework-runtime...\n"


@pytest.mark.parametrize("role", delivery.AGENT_ROLES)
def test_every_foundry_role_requires_an_actual_immutable_version(monkeypatch, role):
    from contextlib import nullcontext
    from scripts import create_agents
    results = writer_plans()
    results[role]["agent_version"] = "latest"
    def scoped_client(endpoint, *, subscription):
        assert subscription == "selected-subscription"
        return nullcontext(object())
    monkeypatch.setattr(create_agents, "project_client", scoped_client)
    def create(_project, *, apply, role, agent_name):
        assert agent_name == delivery.AGENT_NAMES[role]
        return results[role]
    monkeypatch.setattr(create_agents, "provision", create)
    with pytest.raises(delivery.DeliveryError, match="immutable numeric pins"):
        bootstrap.agents(COMPOSITION, "https://approved.services.ai.azure.com/api/projects/test",
                         subscription="selected-subscription")


@pytest.mark.parametrize("role", ["counsellor", "fairness"])
def test_deployment_agent_names_override_legacy_settings_without_changing_standalone_defaults(monkeypatch, role):
    from dataclasses import replace
    from scripts import create_agents
    from governedgraph.agents import fairness_runtime

    legacy = {"counsellor": "legacy-synthetic-counsellor", "fairness": "legacy-synthetic-fairness"}
    monkeypatch.setattr(create_agents, "settings", replace(
        create_agents.settings, agent_name=legacy["counsellor"], fairness_agent_name=legacy["fairness"]))
    definition = SimpleNamespace(as_dict=lambda: {"kind": "prompt", "instructions": "ILLUSTRATIVE - synthetic data"})
    monkeypatch.setattr(create_agents, "agent_definition", lambda *_: definition)
    monkeypatch.setattr(fairness_runtime, "agent_definition", lambda: definition)
    pinned, versions, reads = [], [], []
    def create(**kwargs):
        assert kwargs["agent_name"] == delivery.AGENT_NAMES[role]
        version = SimpleNamespace(name=kwargs["agent_name"], version="1", metadata=kwargs["metadata"],
                                  definition=kwargs["definition"])
        versions.append(version)
        return version
    def list_versions(name):
        reads.append(name)
        return versions
    monkeypatch.setattr(create_agents, "pin_fairness_endpoint",
                        lambda _project, version, *, agent_name: pinned.append((agent_name, version)))
    project = SimpleNamespace(agents=SimpleNamespace(list_versions=list_versions, create_version=create))
    assert create_agents.provision(None, apply=False, role=role)["agent_name"] == legacy[role]
    plan = create_agents.provision(None, apply=False, role=role, agent_name=delivery.AGENT_NAMES[role])
    assert plan["agent_name"] == delivery.AGENT_NAMES[role]
    for _ in range(2):
        result = create_agents.provision(project, apply=True, role=role, agent_name=delivery.AGENT_NAMES[role])
        assert result["agent_name"] == delivery.AGENT_NAMES[role] and result["agent_version"] == "1"
    assert len(versions) == 1 and reads == [delivery.AGENT_NAMES[role]] * 2
    assert pinned == ([(delivery.AGENT_NAMES[role], "1")] * 2 if role == "fairness" else [])


def test_b8_bootstrap_rejects_an_unexpected_agent_identity_before_writer_verification(monkeypatch):
    from contextlib import nullcontext
    from scripts import create_agents, writer_approval

    results = writer_plans()
    results["fairness"]["agent_name"] = "legacy-synthetic-fairness"
    monkeypatch.setattr(create_agents, "project_client", lambda *_, **kw: nullcontext(object()))
    monkeypatch.setattr(create_agents, "provision", lambda _project, **kw: results[kw["role"]])
    monkeypatch.setattr(writer_approval, "verify", lambda *_: pytest.fail("must not verify unexpected identities"))
    with pytest.raises(delivery.DeliveryError, match="different deployment agent name"):
        bootstrap.agents(COMPOSITION, "https://approved.services.ai.azure.com/api/projects/test",
                         subscription="selected-subscription")


@pytest.mark.parametrize("fail", [False, True])
def test_foundry_tokens_and_refreshes_stay_in_the_selected_tenant(monkeypatch, fail):
    from contextlib import contextmanager, nullcontext
    import azure.ai.projects
    import azure.identity
    from azure.core.credentials import AccessToken
    from scripts import create_agents

    tenant = "00000000-0000-0000-0000-000000000004"
    endpoint = "https://approved.services.ai.azure.com/api/projects/test"
    token = AccessToken("ILLUSTRATIVE - synthetic token", 123)
    calls, requests, closed = [], [], []
    credentials = []
    project = object()
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "ambient-subscription")
    monkeypatch.setenv("AZURE_TENANT_ID", "ambient-tenant")
    def account(_runner, args):
        calls.append(args)
        return tenant
    def get_token(*scopes, **kwargs):
        requests.append((scopes, kwargs))
        return token
    @contextmanager
    def default_credential(**kwargs):
        assert kwargs == {"process_timeout": 120}
        try:
            yield SimpleNamespace(get_token=get_token)
        finally:
            closed.append("credential")
    @contextmanager
    def project_factory(**kwargs):
        assert kwargs["endpoint"] == endpoint
        credentials.append(kwargs["credential"])
        try:
            yield project
        finally:
            closed.append("project")
    monkeypatch.setattr(delivery.Runner, "run", account)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", default_credential)
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", project_factory)
    with pytest.raises(PermissionError, match="synthetic denied") if fail else nullcontext():
        with create_agents.project_client(endpoint, subscription="selected-subscription") as selected:
            assert selected is project
            for options in ({}, {"claims": "synthetic-claims", "enable_cae": True, "tenant_id": tenant}):
                assert credentials[0].get_token("https://ai.azure.com/.default", **options) is token
            with pytest.raises(delivery.DeliveryError, match="cannot leave"):
                credentials[0].get_token("https://ai.azure.com/.default", tenant_id="other-tenant")
            if fail:
                raise PermissionError("synthetic denied")
    assert calls == [["az", "account", "show", "--subscription", "selected-subscription",
                      "--query", "tenantId", "-o", "tsv"]]
    assert requests == [(("https://ai.azure.com/.default",), {"tenant_id": tenant}),
                        (("https://ai.azure.com/.default",),
                         {"claims": "synthetic-claims", "enable_cae": True, "tenant_id": tenant})]
    assert closed == ["project", "credential"]


@pytest.mark.parametrize("subscription, response", [
    ("", "unused"), ("selected-subscription", ""), ("selected-subscription", "not-a-tenant"),
])
def test_missing_or_invalid_foundry_scope_fails_before_sdk_authentication(monkeypatch, subscription, response):
    import azure.identity
    from scripts import create_agents

    calls = []
    def account(_runner, args):
        calls.append(args)
        return response
    monkeypatch.setattr(delivery.Runner, "run", account)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential",
                        lambda **_: pytest.fail("must not authenticate without selected tenant"))
    with pytest.raises(delivery.DeliveryError, match="selected|invalid Foundry tenant"):
        with create_agents.project_client("https://approved.services.ai.azure.com/api/projects/test",
                                          subscription=subscription):
            pytest.fail("must not open an unscoped project")
    assert len(calls) == int(bool(subscription))


def test_failed_foundry_tenant_lookup_never_falls_back_to_cli_default(monkeypatch):
    import azure.identity
    from scripts import create_agents

    def unavailable(_runner, args):
        raise delivery.CommandError(args, 1, "synthetic account unavailable")
    monkeypatch.setattr(delivery.Runner, "run", unavailable)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential",
                        lambda **_: pytest.fail("must not authenticate after a failed scope lookup"))
    with pytest.raises(delivery.CommandError, match="account unavailable"):
        with create_agents.project_client("https://approved.services.ai.azure.com/api/projects/test",
                                          subscription="selected-subscription"):
            pytest.fail("must not open a project after failed scope lookup")


def test_standalone_agent_apply_uses_the_exported_subscription(monkeypatch):
    from contextlib import nullcontext
    import sys
    from scripts import create_agents

    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "selected-subscription")
    monkeypatch.setattr(sys, "argv", ["create_agents", "--apply", "--role", "intent"])
    project = object()
    def scoped_client(endpoint, *, subscription):
        assert endpoint == create_agents.settings.project_endpoint
        assert subscription == "selected-subscription"
        return nullcontext(project)
    def provision(selected, *, apply, role):
        assert selected is project and apply is True and role == "intent"
        return {"action": "ILLUSTRATIVE - synthetic data"}
    monkeypatch.setattr(create_agents, "project_client", scoped_client)
    monkeypatch.setattr(create_agents, "provision", provision)
    create_agents.main()


@pytest.mark.parametrize("history, expected", [
    ([{"name": "existing-execution", "properties": {"status": "Running"}}], "existing-execution"),
    ([{"name": "unknown-execution", "properties": {"status": "Unknown"}}], None),
    ([{"name": f"active-{number}", "properties": {"status": "Running"}} for number in range(2)], None),
])
def test_bootstrap_reuses_a_single_active_execution_and_rejects_ambiguous_metadata(history, expected):
    class Runner:
        def run(self, args):
            assert "start" not in args
            return json.dumps(history) if "list" in args else "Succeeded"
    inputs = SimpleNamespace(subscription="synthetic", resource_group="rg-gg-dev")
    if expected:
        assert delivery.run_job(Runner(), inputs, "bootstrap", sleep=lambda _: None) == expected
    else:
        with pytest.raises(delivery.DeliveryError):
            delivery.run_job(Runner(), inputs, "bootstrap", sleep=lambda _: None)


def test_complete_workloads_are_provisioned_once_without_learning_or_evaluation(monkeypatch, capsys):
    from dataclasses import replace
    from scripts import create_agents
    monkeypatch.setattr(create_agents, "settings", replace(
        create_agents.settings, agent_name="legacy-synthetic-counsellor", fairness_agent_name="legacy-synthetic-fairness"))
    calls = []
    foundation = {"compute": {"registryName": "synthetic"},
                  "learning": {"mlRegistryId": "/synthetic"},
                  "ai": {"projectEndpoint": "https://approved.services.ai.azure.com/api/projects/test"}}
    results = writer_plans()
    images = {target: {"image": f"crsynthetic.azurecr.io/{target}@sha256:" + "a" * 64}
              for target in ("runtime", "p2", "fixtures", "probe")}
    inputs = SimpleNamespace(environment="gg-dev", subscription="synthetic", resource_group="rg-gg-dev-test",
                             naming_seed="synthetic", location="swedencentral", search_location="swedencentral",
                             operator_id="synthetic", on_call_email="synthetic@example.invalid", voice_demo=True)
    def provision(_runner, _inputs, template, values):
        calls.append((template, values))
        return {"foundation": foundation} if template.endswith("main.bicep") else (
            {"issuerJob": "issuer", "contentJob": "content", "fixturesJob": "fixtures"}
            if template.endswith("bootstrap.bicep") else {"deployment": {"FAIRNESS_CLIENT_ID": "synthetic"}})
    monkeypatch.setattr(deploy, "environment", lambda *_: None)
    monkeypatch.setattr(deploy, "provision", provision)
    def scoped_images(*_, subscription):
        assert subscription == inputs.subscription
        return images
    monkeypatch.setattr(deploy, "image_artifacts", scoped_images)
    monkeypatch.setattr(bootstrap, "entra", lambda *_: {key: "synthetic" for key in (
        "voiceDemoClientId", "adminClientId", "adminReaderGroupId", "adminOperatorGroupId", "contactCentreApiClientId")})
    def scoped_agents(*_, subscription):
        assert subscription == inputs.subscription
        return results, bootstrap.approved_writers(COMPOSITION, results, foundation["ai"]["projectEndpoint"])
    monkeypatch.setattr(bootstrap, "agents", scoped_agents)
    monkeypatch.setattr(bootstrap, "approve_search_link", lambda *_: None)
    monkeypatch.setattr(bootstrap, "member_public_jwk", lambda *_: {"kty": "RSA", "n": "AQAB", "e": "AQAB"})
    monkeypatch.setattr(bootstrap, "initial_priors", lambda *_: {"initialize": {"initialized": False}})
    monkeypatch.setattr(bootstrap, "ml_containers", lambda *_: ["verified-ml-owned"])
    monkeypatch.setattr(deploy, "run_job", lambda *_: "execution")
    monkeypatch.setattr(deploy, "readiness", lambda *_: None)
    monkeypatch.setattr(deploy, "save_json", lambda *_: None)
    class Runner:
        def run(self, _args):
            return ""
    receipt = deploy.deploy(Runner(), inputs, COMPOSITION)
    assert [name for name, _ in calls] == ["infra/main.bicep", "infra/bootstrap.bicep", COMPOSITION.workload_template]
    workload = calls[-1][1]["configuration"]
    assert workload["orchestratorImage"] == workload["strategyServiceImage"] == workload["memberValuesImage"]
    assert workload["p2MlArtifactContainers"] == ["verified-ml-owned"]
    assert receipt["readiness"] == "passed" and receipt["evaluation"] == "not_run"
    assert capsys.readouterr().out.splitlines() == [
        "Deploying foundation...", "Preparing immutable images...", "Running bounded bootstrap...",
        "Deploying complete workloads...", "Checking deployment readiness...",
    ]
