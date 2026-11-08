"""Delivery contracts, not a state-machine test runner. Runtime boundary suites remain controlling."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap, delivery, deploy
from scripts.azd_env import EnvError

COMPOSITION = delivery.Composition.load(delivery.ROOT / "examples/pension_wtp/deployment.json")


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
    def build(_runner, registry, target, dockerfile, paths, args):
        calls.append((registry, target, dockerfile, args))
        return {"image": registry + "/" + target}
    monkeypatch.setattr(deploy, "build", build)
    deploy.image_artifacts(None, {"compute": {"registryName": "runtime-registry"},
                                "learning": {"mlRegistryId": "/p2-registry"}}, COMPOSITION)
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
    inputs = SimpleNamespace(operator_id="approved-operator", environment="gg-dev",
                             naming_seed="synthetic", voice_demo=True)
    security = {key: "synthetic" for key in ("pipelinePrincipalId", "fairnessPrincipalId",
                                           "runtimePrincipalId", "adminPrincipalId")}
    assert bootstrap.entra(inputs, {"security": security, "gateway": {"gatewayUrl": "https://synthetic.invalid"}})[
        "strategyApiClientId"] == "exact-api"


def writer_plans():
    from scripts.create_agents import provision

    return {role: {**provision(None, apply=False, role=role), "agent_version": "17"}
            for role in delivery.AGENT_ROLES}


def test_local_value_adapter_requires_explicit_domain_provider(monkeypatch):
    from governedgraph.agents import clients
    monkeypatch.setattr(clients, "LOCAL_VALUES_PROVIDER", "")
    with pytest.raises(ValueError, match="explicit GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER"):
        clients.LocalValuesClient().get("synthetic", "synthetic")


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


def test_plan_runs_nothing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps({"subscription": "00000000-0000-0000-0000-000000000001",
        "resource_group": "rg-gg-dev-test", "naming_seed": "00000000-0000-0000-0000-000000000002",
        "operator_id": "00000000-0000-0000-0000-000000000003", "on_call_email": "synthetic@example.invalid",
        "composition": "examples/pension_wtp/deployment.json"}))
    monkeypatch.setattr(delivery.Runner, "run", lambda *_args, **_kwargs: pytest.fail("plan must not call cloud"))
    assert deploy.main(["--inputs", str(path), "--plan"]) == 0
    assert json.loads(capsys.readouterr().out)["sequence"] == list(deploy.SEQUENCE)


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


def test_immutable_build_is_reused_without_another_build(capsys):
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
                            [delivery.ROOT / "requirements-azure.txt"], {})
    assert result["image"].endswith("@sha256:" + "a" * 64)
    assert len(calls) == 3 and not any("build" in call for call in calls)
    assert capsys.readouterr().out == "Reusing governedgraph/framework-runtime...\n"


def test_registry_observation_failure_never_starts_a_build():
    class Runner:
        def run(self, args):
            assert "build" not in args
            raise delivery.CommandError(args, 1, "network unavailable")
    with pytest.raises(delivery.CommandError):
        delivery.build(Runner(), "crsynthetic", "runtime", "Dockerfile", [], {})


def test_absent_immutable_image_is_built_exactly_once(capsys):
    calls = []
    class Runner:
        def run(self, args):
            calls.append(args)
            return "[]" if "list" in args else "sha256:" + "b" * 64 if "show" in args else ""
    result = delivery.build(Runner(), "crsynthetic", "runtime", "Dockerfile", [], {})
    assert sum("build" in call for call in calls) == 1
    assert result["image"].endswith("@sha256:" + "b" * 64)
    assert len(result["source_sha256"]) == 64
    assert capsys.readouterr().out == "Building governedgraph/framework-runtime...\n"


@pytest.mark.parametrize("role", delivery.AGENT_ROLES)
def test_every_foundry_role_requires_an_actual_immutable_version(monkeypatch, role):
    from contextlib import nullcontext
    import azure.ai.projects
    import azure.identity
    from scripts import create_agents
    results = writer_plans()
    results[role]["agent_version"] = "latest"
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", lambda: nullcontext(object()))
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", lambda **_: nullcontext(object()))
    monkeypatch.setattr(create_agents, "provision", lambda _project, *, apply, role: results[role])
    with pytest.raises(delivery.DeliveryError, match="immutable numeric pins"):
        bootstrap.agents(COMPOSITION, "https://approved.services.ai.azure.com/api/projects/test")


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
    monkeypatch.setattr(deploy, "image_artifacts", lambda *_: images)
    monkeypatch.setattr(bootstrap, "entra", lambda *_: {key: "synthetic" for key in (
        "voiceDemoClientId", "adminClientId", "adminReaderGroupId", "adminOperatorGroupId", "contactCentreApiClientId")})
    monkeypatch.setattr(bootstrap, "agents", lambda *_: (results, bootstrap.approved_writers(
        COMPOSITION, results, foundation["ai"]["projectEndpoint"])))
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
