"""Delivery contracts, not a state-machine test runner. Runtime boundary suites remain controlling."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap, delivery, deploy
from scripts.delivery_evidence import Evidence
from governedgraph.strategy_service.provision_auth import GraphError

COMPOSITION = delivery.Composition.load(delivery.ROOT / "examples/pension_wtp/deployment.json")


@pytest.fixture
def synthetic_upgrade_composition(tmp_path):
    from dataclasses import replace
    from governedgraph.strategy_service.source_upgrade import load_upgrade

    upgrade = load_upgrade(COMPOSITION.data_directory)
    assert upgrade is not None
    approval = json.loads(COMPOSITION.release_approval.read_text())
    approval.update(decision_ref=upgrade.decision_ref, evidence_ref=upgrade.evidence_ref)
    path = tmp_path / "synthetic-upgrade-approval.json"
    path.write_text(json.dumps(approval))
    return replace(COMPOSITION, release_approval=path)


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


def test_timed_out_write_retains_only_safe_command_metadata(command_processes, tmp_path, monkeypatch):
    monkeypatch.setattr(delivery.subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=1, stdout=""))
    evidence = Evidence(tmp_path)
    command_processes.outcomes.append("hang")
    with pytest.raises(delivery.CommandTimeout):
        delivery.Runner(evidence=evidence).run(
            ["azd", "env", "set", "PRIVATE_SETTING", "DO_NOT_RECORD"], env={"TOKEN": "DO_NOT_RECORD"})
    evidence.finish("failed")
    text = evidence.path.read_text()
    result = json.loads(text)
    assert result["status"] == "failed"
    assert result["commands"][0]["status"] == "timed_out"
    assert result["commands"][0]["operation"] == "azd env set"
    assert len(command_processes.calls) == 1
    assert "DO_NOT_RECORD" not in text and "private partial" not in text and "PRIVATE_SETTING" not in text


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


FOUNDRY_PARENT_CONFLICT = (
    '{"code":"RequestConflict","message":"Another operation is being performed on the parent resource '
    "'/subscriptions/synthetic/resourceGroups/rg-gg-dev/providers/Microsoft.CognitiveServices/accounts/ais-gg'. "
    'Please try again later."}'
)


@pytest.mark.parametrize("template,diagnostic,retries", [
    ("infra/bootstrap.bicep", "InvalidParameterValueInContainerTemplate: unable to pull image using Managed identity", 3),
    ("infra/main.bicep", FOUNDRY_PARENT_CONFLICT, 3),
    ("infra/bootstrap.bicep", FOUNDRY_PARENT_CONFLICT, 0),
    ("infra/main.bicep", FOUNDRY_PARENT_CONFLICT.replace("Microsoft.CognitiveServices/accounts",
                                                        "Microsoft.Network/privateEndpoints"), 0),
    ("infra/main.bicep", "InternalServerError", 0),
    ("infra/main.bicep", "AuthorizationFailed", 0),
])
def test_provision_retries_only_known_transient_errors(tmp_path, monkeypatch, template, diagnostic, retries):
    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    delays, calls = [], []
    monkeypatch.setattr(delivery.time, "sleep", delays.append)
    class Runner:
        def run(self, args):
            calls.append(args)
            raise delivery.CommandError(args, 1, diagnostic)
    inputs = SimpleNamespace(subscription="synthetic", resource_group="rg-gg-dev", location="swedencentral")
    with pytest.raises(delivery.CommandError):
        delivery.provision(Runner(), inputs, template, {})
    assert len(calls) == retries + 1
    assert all(call == calls[0] for call in calls)
    assert delays == ([30, 60, 120] if retries else [])


def test_single_registry_build_preserves_distinct_dependency_families(monkeypatch):
    calls = []
    def build(_runner, registry, target, dockerfile, paths, args, *, subscription):
        assert subscription == "selected-subscription"
        calls.append((registry, target, dockerfile, args))
        return {"image": registry + "/" + target}
    monkeypatch.setattr(deploy, "build", build)
    deploy.image_artifacts(None, {"compute": {"registryName": "shared-registry"}}, COMPOSITION,
                           subscription="selected-subscription")
    assert len(calls) == 6
    for registry, target, dockerfile, args in calls:
        assert registry == "shared-registry"
        if dockerfile.endswith("Dockerfile.p2"):
            assert "FRAMEWORK_RUNTIME" not in args
        else:
            assert "FRAMEWORK_P2" not in args


@pytest.mark.parametrize("catalogue_upgrade", [False, True])
def test_initializer_receives_existing_keyless_telemetry_without_widening_signer_authority(
        monkeypatch, catalogue_upgrade, synthetic_upgrade_composition):
    composition = synthetic_upgrade_composition if catalogue_upgrade else COMPOSITION
    calls = []
    foundation = {
        "security": {key: "synthetic-" + key for key in (
            "pipelineIdentityId", "pipelineClientId", "pipelinePrincipalId",
            "strategyIdentityId", "strategyClientId", "strategyPrincipalId", "keyVaultUri", "signingKeyName",
        )},
        "compute": {"registryName": "runtime-registry", "environmentId": "existing-environment"},
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
        {"snapshot": {"snapshot_id": "signed-expert-priors"},
         **({"upgrade": {"plan_sha256": "a" * 64, "signature": "synthetic", "signer": "synthetic"}}
            if catalogue_upgrade else {})} if name.endswith("-sign")
        else {"initialized": False, "snapshot_id": "preserved-learned-snapshot"}))
    if not catalogue_upgrade:
        from governedgraph.strategy_service import source_upgrade
        monkeypatch.setattr(source_upgrade, "load_upgrade", lambda *_: pytest.fail("ordinary bootstrap loaded an upgrade plan"))
    result = bootstrap.initial_priors(None, inputs, composition, foundation, images, catalogue_upgrade=catalogue_upgrade)
    sign, initialize = calls
    assert sign["identityId"] == foundation["security"]["pipelineIdentityId"]
    assert initialize["identityId"] == foundation["security"]["strategyIdentityId"]
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" not in sign["env"]
    assert "AZURE_AI_PROJECT_RESOURCE_ID" not in sign["env"]
    assert "GOVERNEDGRAPH_INITIAL_SNAPSHOT" not in sign["env"]
    if catalogue_upgrade:
        assert json.loads(sign["env"]["GOVERNEDGRAPH_SOURCE_UPGRADE_APPROVAL"]) == json.loads(
            composition.release_approval.read_text())
        assert "GOVERNEDGRAPH_SOURCE_UPGRADE_APPROVAL" not in initialize["env"]
        assert json.loads(initialize["env"]["GOVERNEDGRAPH_SOURCE_UPGRADE_SIGNATURE"]) == {
            "plan_sha256": "a" * 64, "signature": "synthetic", "signer": "synthetic",
        }
    else:
        assert not any(key.startswith("GOVERNEDGRAPH_SOURCE_UPGRADE") for job in calls for key in job["env"])
    assert sign["command"][-1] == ("sign-upgrade" if catalogue_upgrade else "sign")
    assert initialize["command"][-1] == ("upgrade" if catalogue_upgrade else "initialize")
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
        "client_id": "admin", "reader_group_id": "reader", "operator_group_id": "operator", "owner_group_id": "owner"})
    monkeypatch.setattr(configure_admin_console, "signed_in_user", lambda: "synthetic-owner")
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
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    path = tmp_path / "inputs.json"
    raw = {"subscription": "00000000-0000-0000-0000-000000000001",
        "naming_seed": "00000000-0000-0000-0000-000000000002",
        "operator_id": "00000000-0000-0000-0000-000000000003", "on_call_email": "synthetic@example.invalid",
        "composition": "examples/pension_wtp/deployment.json"}
    return path, raw


@pytest.fixture
def delivery_flow(private_delivery, monkeypatch):
    from scripts import create_agents
    from governedgraph.strategy_service import source_upgrade

    path, raw = private_delivery
    raw["voice_demo"] = False
    path.write_text(json.dumps(raw))
    calls = []
    foundation = {"compute": {"environmentId": "/synthetic/environment"},
                  "ai": {"projectEndpoint": "https://synthetic.services.ai.azure.com/api/projects/test"}}
    images = {role: {"image": "synthetic.azurecr.io/" + role + "@sha256:" + "a" * 64}
              for role in ("runtime", "p2", "fixtures", "probe")}
    pins = {role: {"agent_name": name, "agent_version": "17"} for role, name in delivery.AGENT_NAMES.items()}
    outputs = {"ADMIN_CONSOLE_URL": "https://synthetic.azure-api.net/admin/", "FAIRNESS_CLIENT_ID": "synthetic"}
    registrations = dict.fromkeys(("voiceDemoClientId", "adminClientId", "adminReaderGroupId",
                                  "adminOperatorGroupId", "adminOwnerGroupId", "contactCentreApiClientId"), "synthetic")

    def note(name, result=None):
        def action(*args, **kwargs):
            calls.append(name)
            return result
        return action

    def provision(_runner, _inputs, template, values):
        if template == "infra/main.bicep":
            calls.append("foundation")
            return {"foundation": foundation}
        if template == "infra/bootstrap.bicep":
            calls.append("bootstrap")
            return {"issuerJob": "issuer", "contentJob": "content", "fixturesJob": "fixtures"}
        assert template == COMPOSITION.workload_template
        calls.append("workloads")
        assert values["configuration"]["orchestratorImage"] == images["runtime"]["image"]
        return {"deployment": outputs}

    monkeypatch.setattr(delivery.Composition, "load", lambda _: COMPOSITION)
    monkeypatch.setattr(deploy, "environment", note("environment"))
    monkeypatch.setattr(create_agents, "provision", lambda *_, **__: {})
    monkeypatch.setattr(bootstrap, "release_approval", note("release approval", {}))
    monkeypatch.setattr(deploy, "provision", provision)
    monkeypatch.setattr(deploy, "image_artifacts", note("images", images))
    monkeypatch.setattr(bootstrap, "entra", note("entra", registrations))
    monkeypatch.setattr(bootstrap, "agents", note("agents", pins))
    monkeypatch.setattr(bootstrap, "gap_review_agent", lambda *_, **__: None)
    monkeypatch.setattr(bootstrap, "approve_search_link", note("search link"))
    monkeypatch.setattr(bootstrap, "initial_priors", note("initial priors", {"initialize": {"initialized": True}}))
    monkeypatch.setattr(bootstrap, "member_public_jwk", note("public key", {}))
    monkeypatch.setattr(deploy, "network_snapshot", lambda *_: None)
    monkeypatch.setattr(deploy, "run_job", lambda _runner, _inputs, name: calls.append(name) or name + "-execution")
    monkeypatch.setattr(source_upgrade, "load_upgrade", lambda *_: pytest.fail("ordinary delivery loaded an upgrade"))
    return SimpleNamespace(path=path, raw=raw, calls=calls, outputs=outputs, foundation=foundation)


def test_normal_delivery_preserves_baseline_order_and_never_loads_catalogue_migration(delivery_flow, monkeypatch):
    from scripts import configure_admin_console

    monkeypatch.setattr(configure_admin_console, "signed_in_user",
                        lambda: pytest.fail("an explicit operator must not be replaced"))
    inputs = delivery.Inputs.load(delivery_flow.path)
    runner = SimpleNamespace(run=lambda *_: "")
    monkeypatch.setattr(deploy, "readiness", lambda *_: delivery_flow.calls.append("readiness"))
    assert deploy.deploy(runner, inputs, COMPOSITION)["readiness"] == "passed"
    assert delivery_flow.calls == [
        "environment", "release approval", "foundation", "images", "entra", "agents", "search link",
        "bootstrap", "issuer", "public key", "initial priors", "content", "fixtures", "workloads", "readiness",
    ]


def test_deploy_bootstraps_source_imports_in_an_isolated_interpreter():
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import sys
        from types import SimpleNamespace

        sys.path.insert(0, sys.argv[1])
        from scripts import deploy

        def stop_before_writes(*_):
            raise RuntimeError("reached environment setup")

        deploy.environment = stop_before_writes
        deploy.stage = lambda *_args, **_kwargs: None
        inputs = SimpleNamespace(
            subscription="00000000-0000-0000-0000-000000000001",
            resource_group="rg-gg-dev-synthetic",
            naming_seed="00000000-0000-0000-0000-000000000002",
            location="swedencentral",
            operator_id="00000000-0000-0000-0000-000000000003",
        )
        try:
            deploy.deploy(None, inputs, SimpleNamespace(name="synthetic"))
        except RuntimeError as error:
            assert str(error) == "reached environment setup", str(error)
        else:
            raise AssertionError("deployment did not reach environment setup")
    """)
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(delivery.ROOT)],
        cwd=delivery.ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("operator_id", [None, ""])
def test_missing_operator_resolves_in_selected_tenant_before_foundation(delivery_flow, monkeypatch, operator_id):
    from governedgraph.strategy_service import provision_auth

    raw = delivery_flow.raw
    if operator_id is None:
        raw.pop("operator_id")
    else:
        raw["operator_id"] = operator_id
    original = json.dumps(raw)
    delivery_flow.path.write_text(original)
    inputs = delivery.Inputs.load(delivery_flow.path)
    resolved = "00000000-0000-0000-0000-000000000004"
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "different-subscription")

    def graph(method, path):
        assert (method, path) == ("GET", "me?$select=id")
        assert deploy.os.environ["AZURE_SUBSCRIPTION_ID"] == inputs.subscription
        delivery_flow.calls.append("operator")
        return {"id": resolved}

    original_environment = deploy.environment
    original_provision = deploy.provision

    def environment(runner, selected, composition):
        assert selected.operator_id == resolved
        original_environment(runner, selected, composition)

    def provision(runner, selected, template, values):
        assert selected.operator_id == resolved
        if template == "infra/main.bicep":
            assert values["principalId"] == resolved
        return original_provision(runner, selected, template, values)

    monkeypatch.setattr(provision_auth, "graph", graph)
    monkeypatch.setattr(deploy, "environment", environment)
    monkeypatch.setattr(deploy, "provision", provision)
    monkeypatch.setattr(deploy, "readiness", lambda *_: None)
    commands = []
    runner = SimpleNamespace(run=lambda args: commands.append(args) or "")
    assert deploy.deploy(runner, inputs, COMPOSITION)["readiness"] == "passed"
    assert delivery_flow.calls[:4] == ["operator", "environment", "release approval", "foundation"]
    settings = {args[3]: args[4] for args in commands if args[:3] == ["azd", "env", "set"]}
    assert settings["STRATEGY_RECOVERY_OPERATOR_ID"] == settings["VOICE_DEMO_OPERATOR_ID"] == resolved
    assert delivery_flow.path.read_text() == original


@pytest.mark.parametrize("error", [
    ValueError("signed-in user response has no valid object ID"),
    GraphError("GET", "me", 400, "delegated user context required"),
])
def test_operator_lookup_failure_stops_before_environment_writes_or_provisioning(
        delivery_flow, monkeypatch, error):
    from governedgraph.strategy_service import provision_auth

    delivery_flow.raw.pop("operator_id")
    delivery_flow.path.write_text(json.dumps(delivery_flow.raw))
    inputs = delivery.Inputs.load(delivery_flow.path)

    def unavailable(method, path):
        assert (method, path) == ("GET", "me?$select=id")
        raise error

    monkeypatch.setattr(provision_auth, "graph", unavailable)
    runner = SimpleNamespace(run=lambda *_: pytest.fail("identity failure must not write or provision"))
    with pytest.raises(delivery.DeliveryError, match="Cannot resolve operator_id"):
        deploy.deploy(runner, inputs, COMPOSITION)
    assert delivery_flow.calls == []


def test_conditional_access_denial_finishes_bootstrap_receipt_without_retry(delivery_flow, monkeypatch, capsys):
    from governedgraph.strategy_service.provision_auth import GraphError

    def deny(*args):
        delivery_flow.calls.append("entra denied")
        raise GraphError("POST", "applications", 400, "AADSTS53003; correlation ID: synthetic-correlation")

    monkeypatch.setattr(bootstrap, "entra", deny)
    monkeypatch.setattr(deploy, "select_target", lambda _runner, _path, _raw, inputs, **_: inputs)
    assert deploy.main(["--inputs", str(delivery_flow.path), "--reuse", "rg-gg-dev-existing"]) == 1
    receipt_path, = (delivery.ROOT / ".run/deliveries").glob("*/receipt.json")
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed" and receipt["stage"] == "bounded bootstrap"
    assert receipt["stages"][-1]["status"] == "failed"
    assert delivery_flow.calls == ["environment", "release approval", "foundation", "images", "entra denied"]
    stderr = capsys.readouterr().err
    assert "Entra Conditional Access blocked" in stderr
    assert "synthetic-correlation" in stderr and str(receipt_path) in stderr


def test_readiness_failure_keeps_staged_evidence_without_private_arguments_or_http_body(
        delivery_flow, monkeypatch, capsys):
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    request_id = "00000000-0000-0000-0000-000000000004"
    apps = [{"name": "ca-" + role, "tags": {"azd-service-name": role}, "properties": {
        "provisioningState": "Succeeded", "runningStatus": "Running", "latestReadyRevisionName": role + "--ready",
        "template": {"containers": [{"image": "synthetic@sha256:" + "a" * 64,
                                     "env": [{"name": "DO_NOT_RECORD", "value": "DO_NOT_RECORD"}]}]},
    }} for role in ("orchestrator", "strategy-service", "member-values", "contact-centre", "admin")]
    jobs = [{"name": name, "properties": {"configuration": {"triggerType": "Schedule"}}}
            for name in ("job-gg-p2-fairness", "job-gg-p2-pipeline")]

    def run(_runner, args):
        if args[:3] == ["az", "containerapp", "list"]:
            return json.dumps(apps)
        if args[:4] == ["az", "containerapp", "job", "list"]:
            return json.dumps(jobs)
        assert args[:3] == ["azd", "env", "set"]
        return ""

    def unavailable(url, *, timeout):
        assert timeout == 30
        headers = Message()
        headers["apim-request-id"] = request_id
        headers["Set-Cookie"] = "DO_NOT_RECORD"
        headers["Content-Type"] = "application/json"
        raise HTTPError(url, 500, "DO_NOT_RECORD", headers,
                        BytesIO(json.dumps({"activityId": request_id, "message": "DO_NOT_RECORD"}).encode()))

    monkeypatch.setattr(delivery.Runner, "run", run)
    monkeypatch.setattr(deploy, "select_target", lambda _runner, _path, _raw, inputs, **_: inputs)
    monkeypatch.setattr(deploy, "urlopen", unavailable)
    assert deploy.main(["--inputs", str(delivery_flow.path), "--new"]) == 1
    evidence_path, = (delivery.ROOT / ".run/deliveries").glob("*/receipt.json")
    text = evidence_path.read_text()
    evidence = json.loads(text)
    assert evidence["status"] == "failed" and evidence["stage"] == "readiness"
    assert evidence["http"][0]["http_status"] == 500
    assert evidence["http"][0]["request_ids"] == {"apim-request-id": request_id, "activityId": request_id}
    assert len(evidence["readiness"]["apps"]) == 5 and len(evidence["readiness"]["jobs"]) == 2
    assert evidence["images"]["runtime"]["image"].startswith("synthetic.azurecr.io/")
    assert evidence["stages"][-2]["name"] == "complete workloads"
    assert evidence["stages"][-2]["status"] == "succeeded"
    assert not (delivery.ROOT / ".run/delivery.json").exists()
    captured = capsys.readouterr()
    assert "HTTP 500 at https://synthetic.azure-api.net/admin/" in captured.err
    assert str(evidence_path) in captured.err and request_id in captured.err
    for private in ("DO_NOT_RECORD", delivery_flow.raw["operator_id"], delivery_flow.raw["on_call_email"]):
        assert private not in text and private not in captured.err


def test_each_submission_preserves_exact_templates_parameters_and_azure_correlation(private_delivery):
    path, raw = private_delivery
    evidence = Evidence(path.parent)
    compiled = {"metadata": {"_generator": {"templateHash": "123"}}, "resources": []}
    calls = []

    class Runner:
        evidence: Evidence

        def run(self, args):
            calls.append(args)
            if args[:3] == ["az", "bicep", "build"]:
                return json.dumps(compiled)
            assert args[:4] == ["az", "deployment", "group", "create"]
            return json.dumps({"id": "/synthetic/deployment", "properties": {
                "provisioningState": "Succeeded", "correlationId": "synthetic-correlation", "templateHash": "123",
                "outputs": {"result": {"value": "ok"}},
            }})
    runner = Runner()
    runner.evidence = evidence
    inputs = delivery.Inputs.from_dict({**raw, "resource_group": "rg-gg-dev"})
    for value in ("sign", "initialize"):
        assert delivery.provision(runner, inputs, "infra/bootstrap-job.bicep",
                                  {"role": value}, group=True) == {"result": "ok"}
    first, second = json.loads(evidence.path.read_text())["deployments"]
    assert first["name"] != second["name"] and len(second["name"]) <= 64
    for record, value in ((first, "sign"), (second, "initialize")):
        template = Path(record["template_file"])
        parameters = Path(record["parameters_file"])
        assert json.loads(template.read_text()) == compiled
        assert json.loads(parameters.read_text())["parameters"]["role"]["value"] == value
        assert record["template_sha256"] == hashlib.sha256(template.read_bytes()).hexdigest()
        assert record["parameters_sha256"] == hashlib.sha256(parameters.read_bytes()).hexdigest()
        assert record["correlation_id"] == "synthetic-correlation" and record["template_hash"] == "123"
        assert parameters.stat().st_mode & 0o777 == 0o600
        assert template.stat().st_mode & 0o777 == 0o600
    submissions = [args for args in calls if args[:4] == ["az", "deployment", "group", "create"]]
    assert len(submissions) == 2
    assert submissions[0][submissions[0].index("--template-file") + 1] == first["template_file"]


def test_failed_submission_collects_read_only_azure_state_without_resubmitting(private_delivery):
    path, raw = private_delivery
    evidence = Evidence(path.parent)
    calls = []

    class Runner:
        evidence: Evidence

        def run(self, args):
            calls.append(args)
            if args[:3] == ["az", "bicep", "build"]:
                return '{"resources":[]}'
            if args[:4] == ["az", "deployment", "group", "create"]:
                raise delivery.CommandError(args, 1, "DO_NOT_RECORD")
            if args[:4] == ["az", "deployment", "group", "show"]:
                return '{"state":"Failed","correlation_id":"synthetic-correlation","error_code":"DeploymentFailed"}'
            assert args[:5] == ["az", "deployment", "operation", "group", "list"]
            return '[{"state":"Failed","resource":"/synthetic/backend"}]'
    runner = Runner()
    runner.evidence = evidence
    inputs = delivery.Inputs.from_dict({**raw, "resource_group": "rg-gg-dev"})
    with pytest.raises(delivery.CommandError):
        delivery.provision(runner, inputs, "infra/bootstrap-job.bicep", {}, group=True)
    result = json.loads(evidence.path.read_text())
    assert result["deployments"][0]["status"] == "failed"
    observed = [item["result"] for item in result["observations"].values()]
    assert any(isinstance(item, dict) and item.get("correlation_id") == "synthetic-correlation" for item in observed)
    assert "DO_NOT_RECORD" not in evidence.path.read_text()
    assert sum(args[:4] == ["az", "deployment", "group", "create"] for args in calls) == 1
    assert all(args[args.index("--subscription") + 1] == inputs.subscription
               for args in calls if args[:2] == ["az", "deployment"])


@pytest.mark.parametrize("resource_group", [None, "rg-gg-dev-test"])
@pytest.mark.parametrize("operator_id", [None, "", "00000000-0000-0000-0000-000000000003"])
def test_plan_runs_nothing(private_delivery, monkeypatch, capsys, resource_group, operator_id):
    from scripts import configure_admin_console

    path, raw = private_delivery
    if operator_id is None:
        raw.pop("operator_id")
    else:
        raw["operator_id"] = operator_id
    if resource_group is not None:
        raw["resource_group"] = resource_group
    content = json.dumps(raw)
    path.write_text(content)
    monkeypatch.setattr(delivery.Composition, "load", lambda _: COMPOSITION)
    monkeypatch.setattr(delivery.Runner, "run", lambda *_args, **_kwargs: pytest.fail("plan must not call cloud"))
    monkeypatch.setattr(configure_admin_console, "signed_in_user",
                        lambda: pytest.fail("plan must not resolve the signed-in user"))
    inputs = delivery.Inputs.load(path)
    suffix = hashlib.sha256(bytes.fromhex("00000000000000000000000000000002")).hexdigest()[:12]
    assert inputs.resource_group == (resource_group or f"rg-gg-dev-{suffix}")
    assert delivery.Inputs.load(path) == inputs
    assert deploy.main(["--inputs", str(path), "--plan"]) == 0
    assert json.loads(capsys.readouterr().out)["sequence"] == list(deploy.SEQUENCE)
    assert path.read_text() == content


@pytest.mark.parametrize("voice_demo", [True, False])
@pytest.mark.parametrize("trailing_slash", ["", "/"])
def test_successful_delivery_prints_demo_and_admin_urls_after_the_json(
        private_delivery, monkeypatch, capsys, voice_demo, trailing_slash):
    path, raw = private_delivery
    raw["voice_demo"] = voice_demo
    path.write_text(json.dumps(raw))
    origin = "https://apim-gg-synthetic.azure-api.net"
    outputs = {"ADMIN_CONSOLE_URL": origin + "/admin/"}
    if voice_demo:
        outputs["VOICE_DEMO_ORIGIN"] = origin + trailing_slash
    receipt = {"readiness": "passed", "evaluation": "not_run", "outputs": outputs}
    monkeypatch.setattr(delivery.Composition, "load", lambda _: COMPOSITION)
    monkeypatch.setattr(deploy, "select_target", lambda _runner, _path, _raw, inputs, **_: inputs)
    monkeypatch.setattr(deploy, "deploy", lambda *_: receipt)
    monkeypatch.setattr(delivery.Runner, "run", lambda *_args, **_kwargs: pytest.fail("must not call cloud"))

    assert deploy.main(["--inputs", str(path), "--new"]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert json.loads(lines[0]) == receipt
    expected_demo = origin + "/demo/" if voice_demo else "disabled"
    assert lines[1:] == [f"Demo URL: {expected_demo}", f"Admin URL: {origin}/admin/"]
    assert captured.err == ""


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


def test_b8_the_release_approval_is_the_closed_human_tier2_decision_without_any_agent_binding():
    approval = bootstrap.release_approval(COMPOSITION)
    assert approval == json.loads(COMPOSITION.release_approval.read_text())
    assert set(approval) == {"tier", "approved", "decision_ref", "evidence_ref"}


def test_normal_release_approval_does_not_depend_on_upgrade_artifacts(tmp_path):
    directory = tmp_path / "catalogue"
    directory.mkdir()
    (directory / "source_upgrade_v1.json").write_text("deliberately invalid obsolete migration metadata")
    composition = SimpleNamespace(release_approval=COMPOSITION.release_approval, data_directory=tmp_path)
    assert bootstrap.release_approval(composition) == json.loads(COMPOSITION.release_approval.read_text())


@pytest.mark.parametrize("change", [
    lambda value: value.update(approved=False),
    lambda value: value.update(tier="tier1"),
    lambda value: value.update(decision_ref="automatic"),
    lambda value: value.update(evidence_ref=""),
    lambda value: value.update(extra="unknown"),
    lambda value: value.update(writers={"gg-counsellor": {"version": "1"}}),
])
def test_b7_b8_release_approval_is_closed(tmp_path, change):
    approval = json.loads(COMPOSITION.release_approval.read_text())
    change(approval)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval))
    with pytest.raises(delivery.DeliveryError):
        bootstrap.release_approval(SimpleNamespace(release_approval=path))


def test_b7_b8_duplicate_approval_fields_and_malformed_files_fail_closed(tmp_path):
    path = tmp_path / "approval.json"
    for content in ('{"tier":"tier2","tier":"tier1"}', "not-json"):
        path.write_text(content)
        with pytest.raises((delivery.DeliveryError, ValueError)):
            bootstrap.release_approval(SimpleNamespace(release_approval=path))


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
def test_every_foundry_role_requires_a_published_version(monkeypatch, role):
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
    with pytest.raises(delivery.DeliveryError, match="missing agent version"):
        bootstrap.agents("https://approved.services.ai.azure.com/api/projects/test",
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
    followed, versions, reads = [], [], []
    def create(**kwargs):
        assert kwargs["agent_name"] == delivery.AGENT_NAMES[role]
        version = SimpleNamespace(name=kwargs["agent_name"], version="1", metadata=kwargs["metadata"],
                                  definition=kwargs["definition"])
        versions.append(version)
        return version
    def list_versions(name):
        reads.append(name)
        return versions
    monkeypatch.setattr(create_agents, "follow_latest_endpoint",
                        lambda _project, *, agent_name: followed.append(agent_name))
    project = SimpleNamespace(agents=SimpleNamespace(list_versions=list_versions, create_version=create))
    assert create_agents.provision(None, apply=False, role=role)["agent_name"] == legacy[role]
    plan = create_agents.provision(None, apply=False, role=role, agent_name=delivery.AGENT_NAMES[role])
    assert plan["agent_name"] == delivery.AGENT_NAMES[role]
    for _ in range(2):
        result = create_agents.provision(project, apply=True, role=role, agent_name=delivery.AGENT_NAMES[role])
        assert result["agent_name"] == delivery.AGENT_NAMES[role] and result["agent_version"] == "1"
    assert len(versions) == 1 and reads == [delivery.AGENT_NAMES[role]] * 2
    assert followed == ([delivery.AGENT_NAMES[role]] * 2 if role == "fairness" else [])


def test_b8_bootstrap_rejects_an_unexpected_agent_identity(monkeypatch):
    from contextlib import nullcontext
    from scripts import create_agents

    results = writer_plans()
    results["fairness"]["agent_name"] = "legacy-synthetic-fairness"
    monkeypatch.setattr(create_agents, "project_client", lambda *_, **kw: nullcontext(object()))
    monkeypatch.setattr(create_agents, "provision", lambda _project, **kw: results[kw["role"]])
    with pytest.raises(delivery.DeliveryError, match="different deployment agent name"):
        bootstrap.agents("https://approved.services.ai.azure.com/api/projects/test",
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
        assert kwargs["transport"].session is not None
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


@pytest.mark.parametrize("verify, cert", [(True, None), ("synthetic-ca.pem", ("synthetic-cert.pem", "synthetic-key.pem"))])
def test_foundry_transport_preserves_verified_tls_and_explicit_trust(verify, cert):
    import ssl
    from requests import Request
    from scripts import create_agents

    url = "https://approved.services.ai.azure.com/api/projects/test"
    request = Request("GET", url).prepare()
    with create_agents._project_transport() as transport:
        adapter = transport.session.get_adapter(url)
        host, pool = adapter.build_connection_pool_key_attributes(request, verify, cert)
        context = pool["ssl_context"]
        assert host["host"] == "approved.services.ai.azure.com"
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
        assert context.maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED
        assert pool["cert_reqs"] == "CERT_REQUIRED"
        if isinstance(verify, str):
            assert pool["ca_certs"] == verify
            assert (pool["cert_file"], pool["key_file"]) == cert
        with pytest.raises(delivery.DeliveryError, match="certificate verification"):
            adapter.build_connection_pool_key_attributes(request, False, None)


@pytest.mark.parametrize("upgrade", [False, True])
@pytest.mark.parametrize("failure", ["ServiceRequestError", "ServiceResponseError"])
def test_foundry_transport_failure_finishes_delivery_evidence(private_delivery, monkeypatch, capsys, upgrade, failure):
    from contextlib import nullcontext
    import azure.ai.projects
    import azure.core.exceptions
    import azure.identity
    from scripts import create_agents, upgrade_catalogue
    from scripts.delivery_evidence import stage

    path, raw = private_delivery
    path.write_text(json.dumps(raw))
    entrypoint = upgrade_catalogue if upgrade else deploy
    endpoint = "https://approved.services.ai.azure.com/api/projects/test"
    error = getattr(azure.core.exceptions, failure)("ILLUSTRATIVE - synthetic TLS failure")
    monkeypatch.setattr(entrypoint, "ROOT", delivery.ROOT)
    monkeypatch.setattr(delivery.Composition, "load", lambda _: COMPOSITION)
    monkeypatch.setattr(entrypoint, "select_target", lambda _runner, _path, _raw, inputs, **_: inputs)
    monkeypatch.setattr(delivery.Runner, "run", lambda *_: "00000000-0000-0000-0000-000000000004")
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", lambda **_: nullcontext(object()))
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", lambda **_: nullcontext(object()))

    def perform(runner, inputs, composition, **kwargs):
        stage(runner, "bounded bootstrap")
        with create_agents.project_client(endpoint, subscription=inputs.subscription):
            raise error
        pytest.fail("transport failure continued delivery")

    monkeypatch.setattr(deploy, "deploy", perform)
    assert entrypoint.main(["--inputs", str(path), "--reuse", "rg-gg-dev-existing"]) == 1
    receipt_path, = (delivery.ROOT / ".run/deliveries").glob("*/receipt.json")
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed" and receipt["stage"] == "bounded bootstrap"
    assert receipt["stages"][-1]["status"] == "failed"
    assert receipt["error_type"] == "DeliveryError"
    assert not receipt["jobs"]
    stderr = capsys.readouterr().err
    assert failure in stderr and endpoint in stderr and str(receipt_path) in stderr
    assert "synthetic TLS failure" in stderr


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
    monkeypatch.setattr(sys, "argv", ["create_agents", "--apply", "--role", "fairness"])
    project = object()
    def scoped_client(endpoint, *, subscription):
        assert endpoint == create_agents.settings.project_endpoint
        assert subscription == "selected-subscription"
        return nullcontext(project)
    def provision(selected, *, apply, role):
        assert selected is project and apply is True and role == "fairness"
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


@pytest.mark.parametrize("crashes, completes", [(3, True), (4, False)])
def test_bootstrap_polling_survives_a_few_cli_crashes_without_starting_a_replacement(crashes, completes):
    polls = []

    class Runner:
        def run(self, args):
            assert "start" not in args
            if "list" in args:
                return json.dumps([{"name": "active-execution", "properties": {"status": "Running"}}])
            polls.append(args)
            if len(polls) <= crashes:
                raise delivery.CommandError(args, 1, "JSONDecodeError in the Azure CLI")
            return "Succeeded"
    inputs = SimpleNamespace(subscription="synthetic", resource_group="rg-gg-dev")
    if completes:
        assert delivery.run_job(Runner(), inputs, "bootstrap", sleep=lambda _: None) == "active-execution"
    else:
        with pytest.raises(delivery.CommandError):
            delivery.run_job(Runner(), inputs, "bootstrap", sleep=lambda _: None)


@pytest.mark.parametrize("catalogue_upgrade", [False, True])
def test_complete_workloads_are_provisioned_once_without_learning_or_evaluation(
        monkeypatch, capsys, catalogue_upgrade, synthetic_upgrade_composition):
    from dataclasses import replace
    from scripts import create_agents
    composition = synthetic_upgrade_composition if catalogue_upgrade else COMPOSITION
    monkeypatch.setattr(create_agents, "settings", replace(
        create_agents.settings, agent_name="legacy-synthetic-counsellor", fairness_agent_name="legacy-synthetic-fairness"))
    calls = []
    foundation = {"compute": {"registryName": "synthetic"},
                  "ai": {"projectEndpoint": "https://approved.services.ai.azure.com/api/projects/test"}}
    results = writer_plans()
    images = {target: {"image": f"crsynthetic.azurecr.io/{target}@sha256:" + "a" * 64}
              for target in ("runtime", "p2", "fixtures", "probe")}
    inputs = SimpleNamespace(environment="gg-dev", subscription="synthetic", resource_group="rg-gg-dev-test",
                             naming_seed="synthetic", location="swedencentral", search_location="swedencentral",
                             operator_id="synthetic", on_call_email="synthetic@example.invalid", voice_demo=True,
                             decision_deployment="synthetic-decision")
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
        "voiceDemoClientId", "adminClientId", "adminReaderGroupId", "adminOperatorGroupId", "adminOwnerGroupId",
        "contactCentreApiClientId")})
    def scoped_agents(*_, subscription):
        assert subscription == inputs.subscription
        return results
    monkeypatch.setattr(bootstrap, "agents", scoped_agents)
    def scoped_persona(endpoint, *, subscription):
        assert subscription == inputs.subscription and endpoint == foundation["ai"]["projectEndpoint"]
    monkeypatch.setattr(bootstrap, "persona_agent", scoped_persona)
    monkeypatch.setattr(bootstrap, "gap_review_agent", lambda endpoint, *, subscription: None)
    monkeypatch.setattr(bootstrap, "approve_search_link", lambda *_: None)
    monkeypatch.setattr(bootstrap, "member_public_jwk", lambda *_: {"kty": "RSA", "n": "AQAB", "e": "AQAB"})
    def initialize_in_selected_order(*_, **kwargs):
        assert kwargs.get("catalogue_upgrade", False) is catalogue_upgrade
        assert calls[-1][0] == (COMPOSITION.workload_template if catalogue_upgrade else "infra/bootstrap.bicep")
        return {"initialize": {"initialized": False}}
    monkeypatch.setattr(bootstrap, "initial_priors", initialize_in_selected_order)
    monkeypatch.setattr(deploy, "run_job", lambda *_: "execution")
    monkeypatch.setattr(deploy, "readiness", lambda *_: None)
    monkeypatch.setattr(deploy, "save_json", lambda *_: None)
    class Runner:
        def run(self, _args):
            return ""
    receipt = deploy.deploy(Runner(), inputs, composition, catalogue_upgrade=catalogue_upgrade)
    assert [name for name, _ in calls] == ["infra/main.bicep", "infra/bootstrap.bicep", COMPOSITION.workload_template]
    workload = calls[-1][1]["configuration"]
    assert workload["orchestratorImage"] == workload["strategyServiceImage"] == workload["memberValuesImage"]
    assert "p2MlArtifactContainers" not in workload
    assert workload["personaAgentPublished"] is True and workload["deployVoicePersona"] is True
    assert workload["agentsPublished"] is True and workload["gapReviewAgentPublished"] is True
    assert not any("Version" in key or key == "writerApproval" for key in workload)
    assert calls[0][1]["deployVoicePersona"] is True
    assert receipt["readiness"] == "passed" and receipt["evaluation"] == "not_run"
    assert capsys.readouterr().out.splitlines() == [
        "Deploying foundation...", "Preparing immutable images...", "Running bounded bootstrap...",
        "Deploying complete workloads...",
        *(["Upgrading governed catalogue state..."] if catalogue_upgrade else []),
        "Checking deployment readiness...",
    ]


def test_b8_unrelated_writer_approval_cannot_start_a_catalogue_upgrade(
        private_delivery, synthetic_upgrade_composition, monkeypatch):
    from governedgraph.common.contracts import ContractError

    path, raw = private_delivery
    approval_path = synthetic_upgrade_composition.release_approval
    approval = json.loads(approval_path.read_text())
    approval.update(decision_ref="HR-SYNTHETIC-UNRELATED", evidence_ref="ILLUSTRATIVE - unrelated synthetic approval")
    approval_path.write_text(json.dumps(approval))
    inputs = delivery.Inputs.from_dict({**raw, "resource_group": "rg-gg-dev"})
    monkeypatch.setattr(deploy, "environment", lambda *_: None)
    monkeypatch.setattr(deploy, "provision", lambda *_: pytest.fail("unapproved upgrade must not provision"))
    with pytest.raises(ContractError, match="matching explicit human release approval"):
        deploy.deploy(None, inputs, synthetic_upgrade_composition, catalogue_upgrade=True)


def test_explicit_catalogue_upgrade_command_requires_an_existing_target(private_delivery, monkeypatch, capsys):
    from scripts import upgrade_catalogue

    path, raw = private_delivery
    path.write_text(json.dumps(raw))
    with pytest.raises(SystemExit) as missing:
        upgrade_catalogue.main(["--inputs", str(path)])
    assert missing.value.code == 2
    calls = []
    monkeypatch.setattr(delivery.Composition, "load", lambda _: COMPOSITION)
    def select(runner, source, values, inputs, *, reuse, create_new):
        assert source == path and values == raw and reuse == "rg-gg-dev-existing" and create_new is False
        return inputs
    monkeypatch.setattr(upgrade_catalogue, "select_target", select)
    def perform(runner, inputs, composition, *, catalogue_upgrade):
        assert catalogue_upgrade is True and composition is COMPOSITION
        calls.append(inputs)
        return {"readiness": "passed", "bootstrap": {"priors": {"initialize": {"migrated": True}}}}
    monkeypatch.setattr(deploy, "deploy", perform)
    assert upgrade_catalogue.main(["--inputs", str(path), "--reuse", "rg-gg-dev-existing"]) == 0
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["strategy_initialization"] == {"migrated": True}


def test_explicit_catalogue_upgrade_plan_runs_nothing(private_delivery, monkeypatch, capsys):
    from scripts import upgrade_catalogue

    path, raw = private_delivery
    content = json.dumps(raw)
    path.write_text(content)
    monkeypatch.setattr(delivery.Composition, "load", lambda _: COMPOSITION)
    monkeypatch.setattr(upgrade_catalogue, "select_target", lambda *_, **__: pytest.fail("plan selected a live target"))
    monkeypatch.setattr(deploy, "deploy", lambda *_, **__: pytest.fail("plan started deployment"))
    assert upgrade_catalogue.main([
        "--inputs", str(path), "--reuse", "rg-gg-dev-existing", "--plan",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target"] == "rg-gg-dev-existing" and result["sequence"] == list(upgrade_catalogue.SEQUENCE)
    assert path.read_text() == content
