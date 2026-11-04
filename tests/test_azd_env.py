"""Deployment details come from the azd environment; the helpers validate them and never invent values."""
import json
import subprocess

import pytest

from scripts import azd_env, run_dev_proof

TENANT = "00000000-0000-4000-8000-000000000001"
CLIENT = "00000000-0000-4000-8000-000000000002"
DIGEST = "a" * 64
VALUES = {
    "AZURE_ENV_NAME": "gg-dev", "AZURE_RESOURCE_GROUP": "rg-gg-dev", "AZURE_SUBSCRIPTION_ID": TENANT,
    "VOICE_DEMO_ORIGIN": "https://apim-gg-synthetic.azure-api.net", "VOICE_DEMO_CLIENT_ID": CLIENT,
    "ORCHESTRATOR_URL": "https://ca-gg-orchestrator-synthetic.synthetic.swedencentral.azurecontainerapps.io",
    "APIM_GATEWAY_URL": "https://apim-gg-synthetic.azure-api.net",
    "STRATEGY_TENANT_ID": TENANT, "STRATEGY_API_CLIENT_ID": CLIENT,
    "STRATEGY_ADMIN_URL": "https://apim-gg-synthetic.azure-api.net/strategy-admin",
    "EXPLAINABILITY_STORAGE_URL": "https://synthetic.blob.core.windows.net",
    "EXPLAINABILITY_CONTAINER": "explainability-records",
    "JOURNEY_STORAGE_URL": "https://synthetic.blob.core.windows.net",
    "AZURE_CONTAINER_REGISTRY_ENDPOINT": "registry.azurecr.io",
    "AZURE_ML_WORKSPACE": "mlw-gg-synthetic", "AZURE_ML_REGISTRY_ID": "/registries/synthetic",
    "AZURE_ML_STORAGE_ID": "/storageAccounts/synthetic",
    "AZURE_AI_PROJECT_ENDPOINT": "https://synthetic.services.ai.azure.com/api/projects/synthetic",
    "AZURE_AI_MODEL_DEPLOYMENT": "gpt-counsellor",
}


def env_file(tmp_path, values=VALUES):
    path = tmp_path / "azd.env"
    path.write_text("".join(f'{key}="{value}"\n' for key, value in values.items()))
    return str(path)


def test_values_are_loaded_from_the_environment_file_and_empty_values_are_missing(tmp_path):
    path = env_file(tmp_path, {**VALUES, "VOICE_DEMO_CLIENT_ID": ""})
    loaded = azd_env.load(path)
    assert loaded["AZURE_ENV_NAME"] == "gg-dev" and "VOICE_DEMO_CLIENT_ID" not in loaded
    assert azd_env.problems("voice-benchmark", loaded) == ["missing VOICE_DEMO_CLIENT_ID"]


def test_every_task_is_ready_for_a_complete_deployment():
    for task in azd_env.TASKS:
        assert azd_env.problems(task, VALUES) == [], task


@pytest.mark.parametrize("change,message", [
    ({"STRATEGY_TENANT_ID": "tenant"}, "STRATEGY_TENANT_ID is not a GUID"),
    ({"VOICE_DEMO_ORIGIN": "https://example.com"}, "not an apim-gg-<suffix>.azure-api.net gateway"),
    ({"ORCHESTRATOR_URL": "http://insecure"}, "ORCHESTRATOR_URL must be https"),
    ({"STRATEGY_ADMIN_URL": "https://apim-gg-synthetic.azure-api.net/other"}, "must end in /strategy-admin"),
])
def test_malformed_deployment_values_are_reported_not_used(change, message):
    issues = [issue for task in azd_env.TASKS for issue in azd_env.problems(task, {**VALUES, **change})]
    assert any(message in issue for issue in issues)


def test_export_emits_quoted_shell_assignments_and_derived_values_only_for_the_task(tmp_path, capsys):
    path = env_file(tmp_path, {**VALUES, "VOICE_DEMO_ORIGIN": "https://apim-gg-synthetic.azure-api.net"})
    assert azd_env.main(["export", "--task", "presenter", "--env-file", path]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "export PRESENTER_URL=https://apim-gg-synthetic.azure-api.net/voice-demo/" in out
    assert not any("STRATEGY" in line for line in out)
    assert azd_env.main(["export", "--task", "reconcile", "--env-file", path]) == 0
    assert "export GOVERNEDGRAPH_MODE=azure" in capsys.readouterr().out


def test_export_refuses_when_the_deployment_does_not_provide_a_required_value(tmp_path, capsys):
    values = {key: value for key, value in VALUES.items() if key != "STRATEGY_ADMIN_URL"}
    assert azd_env.main(["export", "--task", "recovery", "--env-file", env_file(tmp_path, values)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "missing STRATEGY_ADMIN_URL" in captured.err


def test_show_reports_ready_and_not_ready_tasks(tmp_path, capsys):
    values = {key: value for key, value in VALUES.items() if key != "VOICE_DEMO_CLIENT_ID"}
    assert azd_env.main(["show", "--env-file", env_file(tmp_path, values)]) == 1
    text = capsys.readouterr().out
    assert "[voice-benchmark]" in text and "NOT READY" in text and "(MISSING)" in text and "[reconcile]" in text
    assert azd_env.main(["show", "--task", "reconcile", "--env-file", env_file(tmp_path)]) == 0


def test_a_missing_azd_or_environment_is_a_clear_error(monkeypatch, capsys):
    def no_azd(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(azd_env.subprocess, "check_output", no_azd)
    assert azd_env.main(["show"]) == 2 and "azd" in capsys.readouterr().err

    def no_env(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "azd", stderr="no environment")

    monkeypatch.setattr(azd_env.subprocess, "check_output", no_env)
    assert azd_env.main(["show"]) == 2 and "no selected environment" in capsys.readouterr().err.replace("azd has ", "")


def test_an_explicit_environment_is_forwarded_to_azd(monkeypatch):
    calls = []

    def get_values(args, **kw):
        calls.append(args)
        return 'AZURE_ENV_NAME="gg-dev"\n'

    monkeypatch.setattr(azd_env.subprocess, "check_output", get_values)
    assert azd_env.load(environment="gg-dev")["AZURE_ENV_NAME"] == "gg-dev"
    assert calls == [["azd", "env", "get-values", "--environment", "gg-dev"]]


IMAGE = f"registry.azurecr.io/governedgraph/voice-issuer@sha256:{DIGEST}"


def test_proofs_run_only_in_gg_dev_and_voice_needs_an_immutable_image():
    run_dev_proof.validate(VALUES, "channel", None)
    run_dev_proof.validate(VALUES, "voice", IMAGE)
    with pytest.raises(azd_env.EnvError, match="gg-dev"):
        run_dev_proof.validate({**VALUES, "AZURE_ENV_NAME": "production"}, "channel", None)
    for image in (None, "registry.azurecr.io/x:latest", "evil.example/x@sha256:" + DIGEST,
                  "other.azurecr.io/governedgraph/voice-issuer@sha256:" + DIGEST):
        with pytest.raises(azd_env.EnvError, match="image"):
            run_dev_proof.validate(VALUES, "voice", image)
    with pytest.raises(azd_env.EnvError, match="ORCHESTRATOR_URL"):
        run_dev_proof.validate({**VALUES, "ORCHESTRATOR_URL": "https://example.com"}, "channel", None)


def test_the_proof_overrides_only_the_execution_and_never_touches_the_stored_job(tmp_path, monkeypatch, capsys):
    stored = {"containers": [{"name": "issuer", "image": "stored", "command": ["x"], "args": ["--public-jwk"]}]}
    calls, started = [], {}

    def fake_az(*args):
        calls.append(args)
        if args[:3] == ("containerapp", "job", "show"):
            return json.dumps(stored)
        assert args[:3] == ("containerapp", "job", "start")
        started.update(json.loads(open(args[args.index("--yaml") + 1]).read()))
        return "job-gg-dev-member-issuer-abc\n"

    monkeypatch.setattr(run_dev_proof, "az", fake_az)
    assert run_dev_proof.main(["voice", "--image", IMAGE, "--env-file", env_file(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "job-gg-dev-member-issuer-abc"
    container = started["containers"][0]
    assert container["image"] == IMAGE
    assert container["args"] == ["--probe-voice", "--orchestrator-url", VALUES["ORCHESTRATOR_URL"]]
    assert all(call[:3] in (("containerapp", "job", "show"), ("containerapp", "job", "start")) for call in calls)
    assert all(call[call.index("--subscription") + 1] == VALUES["AZURE_SUBSCRIPTION_ID"] for call in calls)
    assert not any("update" in call or "create" in call for call in calls)
    assert "Bearer" not in json.dumps(started) and "eyJ" not in json.dumps(started)


def test_channel_proof_passes_the_gateway_so_member_report_protections_are_verified(tmp_path, monkeypatch):
    started = {}

    def fake_az(*args):
        if args[:3] == ("containerapp", "job", "show"):
            return json.dumps({"containers": [{"name": "issuer", "image": "stored"}]})
        started.update(json.loads(open(args[args.index("--yaml") + 1]).read()))
        return "execution\n"

    monkeypatch.setattr(run_dev_proof, "az", fake_az)
    assert run_dev_proof.main(["channel", "--env-file", env_file(tmp_path)]) == 0
    container = started["containers"][0]
    assert container["image"] == "stored"
    assert container["args"] == ["--probe-channel", "--orchestrator-url", VALUES["ORCHESTRATOR_URL"],
                                 "--apim-url", VALUES["APIM_GATEWAY_URL"]]


@pytest.mark.parametrize("mode", ["channel", "voice"])
def test_gateway_journey_output_resolves_the_existing_private_orchestrator(tmp_path, monkeypatch, mode):
    values = {**VALUES, "ORCHESTRATOR_URL": VALUES["APIM_GATEWAY_URL"] + "/journey"}
    assert azd_env.problems("voice-proof", values) == []
    calls, started = [], {}
    stored = {"containers": [{"name": "issuer", "image": "stored", "env": [{"name": "AZURE_CLIENT_ID", "value": CLIENT}]}]}

    def fake_az(*args):
        calls.append(args)
        if args[:2] == ("containerapp", "list"):
            assert args[args.index("-g") + 1] == VALUES["AZURE_RESOURCE_GROUP"]
            query = args[args.index("--query") + 1]
            assert 'tags."azd-service-name" == \'orchestrator\'' in query
            assert 'tags."azd-env-name" == \'gg-dev\'' in query
            return json.dumps([VALUES["ORCHESTRATOR_URL"].removeprefix("https://")])
        if args[:3] == ("containerapp", "job", "show"):
            return json.dumps(stored)
        assert args[:3] == ("containerapp", "job", "start")
        started.update(json.loads(open(args[args.index("--yaml") + 1]).read()))
        return "proof-execution\n"

    monkeypatch.setattr(run_dev_proof, "az", fake_az)
    image = ["--image", IMAGE] if mode == "voice" else []
    assert run_dev_proof.main([mode, *image, "--env-file", env_file(tmp_path, values)]) == 0
    args = started["containers"][0]["args"]
    assert args[:3] == [f"--probe-{mode}", "--orchestrator-url", VALUES["ORCHESTRATOR_URL"]]
    if mode == "channel":
        assert args[3:] == ["--apim-url", VALUES["APIM_GATEWAY_URL"]]
    assert started["containers"][0]["env"] == stored["containers"][0]["env"]
    assert all(call[call.index("--subscription") + 1] == VALUES["AZURE_SUBSCRIPTION_ID"] for call in calls)
    assert not any("update" in call or "create" in call for call in calls)
    assert "Bearer" not in json.dumps(started) and "eyJ" not in json.dumps(started)


@pytest.mark.parametrize("change", [
    {"ORCHESTRATOR_URL": "https://apim-gg-another.azure-api.net/journey"},
    {"ORCHESTRATOR_URL": VALUES["APIM_GATEWAY_URL"]},
    {"ORCHESTRATOR_URL": VALUES["APIM_GATEWAY_URL"] + "/other"},
    {"ORCHESTRATOR_URL": VALUES["ORCHESTRATOR_URL"] + "/journeys"},
    {"ORCHESTRATOR_URL": VALUES["ORCHESTRATOR_URL"] + "?query=value"},
    {"ORCHESTRATOR_URL": VALUES["ORCHESTRATOR_URL"] + "#fragment"},
    {"ORCHESTRATOR_URL": VALUES["ORCHESTRATOR_URL"] + ":443"},
    {"ORCHESTRATOR_URL": VALUES["ORCHESTRATOR_URL"].replace("https://", "https://user@")},
    {"APIM_GATEWAY_URL": VALUES["APIM_GATEWAY_URL"].replace("https://", "http://")},
    {"APIM_GATEWAY_URL": VALUES["APIM_GATEWAY_URL"] + "/journey"},
])
def test_proof_readiness_and_execution_reject_the_same_unsafe_endpoints(change):
    values = {**VALUES, **change}
    assert azd_env.problems("voice-proof", values)
    with pytest.raises(azd_env.EnvError):
        run_dev_proof.validate(values, "channel", None)


@pytest.mark.parametrize("inventory", [
    [],
    ["ca-gg-orchestrator-one.synthetic.azurecontainerapps.io", "ca-gg-orchestrator-two.synthetic.azurecontainerapps.io"],
    ["example.com"],
    ["ca-gg-orchestrator-one.synthetic.azurecontainerapps.io/path"],
    [None],
    {"fqdn": "example.com"},
])
def test_missing_ambiguous_or_unapproved_private_targets_cannot_start_a_proof(tmp_path, monkeypatch, capsys, inventory):
    values = {**VALUES, "ORCHESTRATOR_URL": VALUES["APIM_GATEWAY_URL"] + "/journey"}
    calls = []

    def fake_az(*args):
        calls.append(args)
        assert args[:2] == ("containerapp", "list")
        return json.dumps(inventory)

    monkeypatch.setattr(run_dev_proof, "az", fake_az)
    assert run_dev_proof.main(["channel", "--env-file", env_file(tmp_path, values)]) == 2
    assert len(calls) == 1 and capsys.readouterr().err.startswith("error:")


def test_unreadable_private_endpoint_inventory_is_an_explicit_failure(tmp_path, monkeypatch, capsys):
    values = {**VALUES, "ORCHESTRATOR_URL": VALUES["APIM_GATEWAY_URL"] + "/journey"}

    def fake_az(*args):
        assert args[:2] == ("containerapp", "list")
        return "not JSON"

    monkeypatch.setattr(run_dev_proof, "az", fake_az)
    assert run_dev_proof.main(["channel", "--env-file", env_file(tmp_path, values)]) == 2
    assert "endpoint inventory as JSON" in capsys.readouterr().err
