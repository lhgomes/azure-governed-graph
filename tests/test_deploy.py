"""The deployment script: questions, ordering, idempotent resume, and refusals, against a simulated Azure."""
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import deploy
from scripts.deploy import CommandError, DeployError

ORIGINAL_RELEASE_APPROVAL = deploy.Deployer.release_approval
FAKE_DIGESTS = {"gg-counsellor": "1" * 64, "gg-counsellor-voice-compact": "2" * 64}
APPROVED_RELEASE = {
    "tier": "tier2", "approved": True, "decision_ref": "GB-SYNTHETIC",
    "evidence_ref": "ILLUSTRATIVE - synthetic acceptance reference",
    "writers": {name: {"definition_sha256": digest} for name, digest in FAKE_DIGESTS.items()},
}


@pytest.fixture(autouse=True)
def approved_release(monkeypatch):
    monkeypatch.setattr(deploy.Deployer, "release_approval", lambda self: copy.deepcopy(APPROVED_RELEASE))

SUB = "00000000-0000-4000-8000-0000000000aa"
OPERATOR = "00000000-0000-4000-8000-0000000000bb"
PRINCIPALS = {"STRATEGY_PUBLISHER_PRINCIPAL_ID": "00000000-0000-4000-8000-000000000001",
              "STRATEGY_FAIRNESS_PRINCIPAL_ID": "00000000-0000-4000-8000-000000000002",
              "STRATEGY_RUNTIME_PRINCIPAL_ID": "00000000-0000-4000-8000-000000000003",
              "STRATEGY_ADMIN_PRINCIPAL_ID": "00000000-0000-4000-8000-000000000004"}
PUBLIC_JWK = {"kty": "RSA", "alg": "RS256", "use": "sig", "n": "abc", "e": "AQAB"}
ARGS = ["--yes", "--non-interactive", "--subscription", SUB, "--location", "swedencentral",
        "--resource-group", "rg-demo", "--on-call-email", "ops@example.com", "--no-voice-demo"]
DEFAULT_ARGS = [a for a in ARGS if a != "--no-voice-demo"]   # the runner's real default: presenter, capture and lookup on
GATEWAY = "https://apim-gg-x.azure-api.net"
WORKSPACE = "00000000-0000-4000-8000-0000000000e1"


class FakeGateway:
    """The deployed console as the gateway shows it; tests change fields to simulate a broken deployment."""

    def __init__(self):
        self.page_status, self.overview_status, self.unlisted_status, self.health_status = 200, 401, 404, 200
        self.requests = []

    def __call__(self, url, headers=None, timeout=30):
        self.requests.append((url, dict(headers or {})))
        path = url.removeprefix(GATEWAY)
        if path == "/health/admin/healthz":
            return self.health_status, '{"ok":true}'
        if path == "/admin/":
            return self.page_status, '<meta name="admin-session" content="capability">'
        if path == "/admin/api/overview":
            return self.overview_status, "{}"
        return self.unlisted_status, "{}"


@pytest.fixture(autouse=True)
def gateway(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(deploy, "http_request", fake)
    return fake


class FakeAzure:
    """Just enough of az/azd and the helper scripts to run the whole flow."""

    def __init__(self):
        self.env, self.calls, self.fail = {}, [], {}
        self.rg_exists, self.link_description = "false", "GovernedGraph approved-content indexer - owner approval required"
        self.link_status, self.logs, self.versions = "Pending", json.dumps(PUBLIC_JWK), iter(range(1, 50))
        self.agent_failures = 0
        self.approvals = []
        self.search_services = []
        self.search_locations = [region for region in deploy.SEARCH_REGIONS]
        self.search_operations = None
        self.provision_errors = []
        self.deleted_vaults, self.deleted_accounts, self.recovered_ids = [], [], set()
        self.process_envs = []
        self.environment_path = None
        self.service_targets = []
        self.proof_status = "Succeeded"
        self.capture_spans, self.capture_queries = "7", []
        self.proof_output = "job-gg-dev-member-issuer-proof1\n"
        self.voice_proof_output = "job-gg-dev-member-issuer-voiceproof1\n"
        self.voice_proof_status = "Succeeded"
        self.voice_image_digest = "sha256:" + "b" * 64
        self.p2_output = ['{"cycle_id": "cycle-synthetic", "stage": "audit"}',
                          '{"cycle_id": "cycle-synthetic", "pipeline": "Completed"}']
        self.fairness_output = ['{"fairness_agent": "Succeeded", "execution": "job-gg-p2-fairness-proof1"}']

    def mutating(self):
        read = ("get-values", "list", "show", "exists", "usage", "--check-status", "signed-in-user")
        return [c for c in self.calls if not any(r in c for r in read)]

    def run(self, args, *, stream=False, check=True, env=None, quiet=False):
        args = [str(a) for a in args]
        self.calls.append(args)
        self.process_envs.append((args, env))
        text = " ".join(args)
        if args[:2] == ["azd", "provision"] and self.provision_errors:
            raise CommandError(args, 1, self.provision_errors.pop(0))
        for needle, error in self.fail.items():
            if needle in text:
                if error[0] > 0:
                    self.fail[needle] = (error[0] - 1, error[1])
                    raise CommandError(args, 1, error[1])
        tool = args[0]
        if tool == "azd":
            return self.azd(args[1:])
        if tool == "az":
            if "--subscription" in args and (args[1] in ("ad", "extension") or args[1:3] == ["account", "list"]):
                raise CommandError(args, 2, "unrecognized arguments: --subscription")
            return self.az(args[1:])
        if "provision_auth" in text:
            assert args[args.index("--admin") + 1] == PRINCIPALS["STRATEGY_ADMIN_PRINCIPAL_ID"]
            return json.dumps({"application": "x"}) + "\nSTRATEGY_API_CLIENT_ID=00000000-0000-4000-8000-0000000000c1\n"
        if "configure_contact_centre" in text:
            return json.dumps({"client_id": "00000000-0000-4000-8000-0000000000c2", "audience": "api://cc"}, indent=2)
        if "configure_admin_console" in text:
            assert args[args.index("--member") + 1] == OPERATOR and args[args.index("--origin") + 1] == "https://apim-gg-x.azure-api.net"
            return json.dumps({"apply": True, "client_id": "00000000-0000-4000-8000-0000000000c4",
                               "reader_group_id": "00000000-0000-4000-8000-0000000000d4",
                               "operator_group_id": "00000000-0000-4000-8000-0000000000d5"}, indent=2)
        if "configure_strategy_recovery" in text:
            assert "--apply" in args
            api = args[args.index("--api-client-id") + 1]
            human = args[args.index("--human-id") + 1]
            assert env["AZURE_ENV_NAME"] == "gg-dev"
            assert api == env["STRATEGY_API_CLIENT_ID"] == self.env["STRATEGY_API_CLIENT_ID"]
            assert human == env["STRATEGY_RECOVERY_OPERATOR_ID"] == OPERATOR
            return json.dumps({"environment": "gg-dev", "api_client_id": api, "human_id": human,
                               "scope": "Strategy.Recover", "apply": True, "new_application_roles": False,
                               "credentials": "none"})
        if "create_agents" in text:
            role = args[args.index("--role") + 1]
            name = {"counsellor": "gg-counsellor", "voice-compact": "gg-counsellor-voice-compact"}.get(role, f"gg-{role}-agent")
            digest = FAKE_DIGESTS.get(name, "3" * 64)
            if "--apply" not in args:
                return json.dumps({"agent_name": name, "definition_sha256": digest, "action": "plan only"}) + "\n"
            if self.agent_failures:
                self.agent_failures -= 1
                raise CommandError(args, 1, "AuthorizationFailed")
            assert env["GOVERNEDGRAPH_MODE"] == "azure" and "AZURE_AI_PROJECT_ENDPOINT" in env
            return json.dumps({"agent_name": name, "definition_sha256": digest,
                               "agent_version": str(next(self.versions)), "action": "created"}) + "\n"
        if "scripts.writer_approval" in text:
            record = Path(args[args.index("--file") + 1])
            assert record.stat().st_mode & 0o077 == 0
            self.approvals.append(json.loads(record.read_text()))
            if "--apply" in args:
                self.env["GOVERNEDGRAPH_WRITER_APPROVAL_JSON"] = json.dumps(self.approvals[-1], sort_keys=True, separators=(",", ":"))
            return ""
        if "run_dev_proof" in text:
            return self.voice_proof_output if "voice" in args else self.proof_output
        if "run_p2_container_jobs" in text:
            assert "--image" in args and "@sha256:" in args[args.index("--image") + 1]
            lines = list(self.p2_output)
            if "--fairness-agent-proof" in args:
                lines += self.fairness_output
            return "\n".join(lines) + "\n"
        return ""

    def azd(self, a):
        if a[:2] == ["env", "get-values"]:
            escaped = {k: v.replace("\\", "\\\\").replace('"', '\\"') for k, v in self.env.items()}   # as azd prints them
            return "".join(f'{k}="{v}"\n' for k, v in escaped.items())
        if a[:2] == ["env", "list"]:
            exists = self.env and (self.environment_path is None or self.environment_path.exists())
            return json.dumps([{"Name": "gg-dev"}] if exists else [])
        if a[:2] == ["env", "new"]:
            self.env.clear()
            if self.environment_path is not None:
                self.environment_path.mkdir(parents=True, exist_ok=True)
            self.env.update(AZURE_ENV_NAME="gg-dev", AZURE_SUBSCRIPTION_ID=a[a.index("--subscription") + 1],
                            AZURE_LOCATION=a[a.index("--location") + 1])
        elif a[:2] == ["env", "set"]:
            self.env[a[2]] = a[3]
        elif a[0] == "provision":
            services = ["orchestrator", "strategy-service", "member-values"]
            if self.env.get("GOVERNEDGRAPH_DEPLOY_ADMIN_CONSOLE", "true") != "false":
                services.append("admin")
            if self.env.get("GOVERNEDGRAPH_SYNTHETIC_CONTACT_CENTRE") == "true":
                services.append("contact-centre")
            self.service_targets = [{"azd-env-name": "gg-dev", "azd-service-name": service} for service in services]
            self.env.update(AZURE_RESOURCE_GROUP=self.env["GOVERNEDGRAPH_RESOURCE_GROUP_NAME"], **PRINCIPALS,
                            CONTENT_STORAGE_RESOURCE_ID="/subscriptions/s/storageAccounts/content",
                            AZURE_AI_PROJECT_ENDPOINT="https://x.services.ai.azure.com/api/projects/p",
                            ORCHESTRATOR_URL="https://ca-gg-orchestrator-x.y.swedencentral.azurecontainerapps.io",
                            APIM_GATEWAY_URL="https://apim-gg-x.azure-api.net", VOICE_DEMO_ORIGIN="https://apim-gg-x.azure-api.net",
                            LOG_ANALYTICS_WORKSPACE_ID=WORKSPACE)
            self.env["AZURE_CONTAINER_REGISTRY_ENDPOINT"] = "registry.azurecr.io"
            self.env.update(
                AZURE_ML_REGISTRY_ID="/subscriptions/s/resourceGroups/rg/providers/Microsoft.ContainerRegistry/registries/crmlsynthetic",
                AZURE_ML_STORAGE_ID="/subscriptions/s/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/mlsynthetic",
                FAIRNESS_CLIENT_ID="00000000-0000-4000-8000-0000000000d1")
            if self.env.get("DEPLOY_VOICE_DEMO") == "true":
                self.env.setdefault("VOICE_DEMO_CLIENT_ID", "00000000-0000-4000-8000-0000000000c3")
        elif a[0] == "deploy":
            required = [s for s in deploy.SERVICES
                        if s != "admin" or self.env.get("GOVERNEDGRAPH_DEPLOY_ADMIN_CONSOLE", "true") != "false"]
            targets = [a[1]] if not a[1].startswith("-") else required
            for service in targets:
                if not any(tags.get("azd-service-name") == service for tags in self.service_targets):
                    raise CommandError(["azd", *a], 1, f"unable to find a resource tagged with 'azd-service-name: {service}'")
            self.env["SERVICE_ORCHESTRATOR_IMAGE_NAME"] = "registry.azurecr.io/orch@sha256:" + "a" * 64
        return ""

    def az(self, a):
        text = " ".join(a)
        if a[:2] == ["extension", "show"] or a[:2] == ["extension", "add"]:
            return ""
        if a[:3] == ["monitor", "log-analytics", "query"]:
            self.capture_queries.append(a)
            assert a[a.index("-w") + 1] == WORKSPACE and "synthetic_conversation" in a[a.index("--analytics-query") + 1]
            return self.capture_spans
        if a[:3] == ["monitor", "log-analytics", "workspace"]:
            return WORKSPACE
        if a[:2] == ["storage", "container-rm"]:
            return json.dumps(["azureml", "azureml-blobstore-ffe1fe22-7acf-4919-bfaf-29f7afc39311", "other-data"])
        if a[:2] == ["acr", "build"]:
            return ""
        if a[:3] == ["acr", "repository", "show"]:
            return self.voice_image_digest
        if a[:2] == ["resource", "list"]:
            assert a[a.index("--resource-type") + 1] == "Microsoft.App/containerApps"
            assert a[a.index("--query") + 1] == "[].tags"
            return json.dumps(self.service_targets)
        if a[:2] == ["keyvault", "list-deleted"]:
            return json.dumps(self.deleted_vaults)
        if a[:3] == ["cognitiveservices", "account", "list-deleted"]:
            return json.dumps(self.deleted_accounts)
        if a[:2] == ["keyvault", "recover"] or a[:3] == ["cognitiveservices", "account", "recover"]:
            name, group = a[a.index("--name") + 1], a[a.index("--resource-group") + 1]
            provider = "Microsoft.KeyVault/vaults" if a[0] == "keyvault" else "Microsoft.CognitiveServices/accounts"
            self.recovered_ids.add(f"/subscriptions/{SUB}/resourceGroups/{group}/providers/{provider}/{name}")
            return ""
        if a[:2] == ["resource", "show"]:
            resource_id = a[a.index("--ids") + 1]
            return resource_id if resource_id in self.recovered_ids else ""
        if a[:2] == ["account", "list"]:
            return json.dumps([{"id": SUB, "name": "Dev", "isDefault": True}])
        if a[:2] == ["account", "show"]:
            return SUB + "\n"
        if a[0] == "ad":
            return OPERATOR + "\n"
        if a[:2] == ["role", "assignment"]:
            return json.dumps(["Owner"])
        if a[:2] == ["provider", "show"]:
            if "--namespace" in a:
                return json.dumps(self.search_locations)
            return "Registered\n"
        if a[:4] == ["deployment", "operation", "group", "list"]:
            return json.dumps(self.search_operations if self.search_operations is not None else [capacity_operation()])
        if a[:3] == ["search", "service", "list"]:
            return json.dumps(self.search_services)
        if a[:2] == ["cognitiveservices", "usage"]:
            return json.dumps([{"name": {"value": n}, "currentValue": 0, "limit": 100} for n in deploy.QUOTA_NEEDS])
        if a[:2] == ["group", "exists"]:
            return self.rg_exists
        if a[:2] == ["group", "show"]:
            return "swedencentral\n"
        if a[:2] == ["network", "private-endpoint-connection"] and a[2] == "list":
            other = {"id": "/pe/other", "properties": {"privateLinkServiceConnectionState": {"status": "Pending", "description": "someone else"}}}
            mine = {"id": "/pe/mine", "properties": {"privateLinkServiceConnectionState": {
                "status": self.link_status, "description": self.link_description}}}
            return json.dumps([other, mine])
        if a[:3] == ["containerapp", "job", "start"]:
            return "exec1\n"
        if a[:4] == ["containerapp", "job", "execution", "show"]:
            if a[a.index("--job-execution-name") + 1] == self.voice_proof_output.strip():
                return self.voice_proof_status + "\n"
            if a[a.index("--job-execution-name") + 1] != "exec1":
                return self.proof_status + "\n"
            return "Succeeded\n"
        if a[:3] == ["containerapp", "job", "logs"]:
            return self.logs
        return ""


def capacity_operation():
    return {"properties": {
        "provisioningState": "Failed", "timestamp": datetime.now(timezone.utc).isoformat(),
        "targetResource": {"resourceType": "Microsoft.Search/searchServices",
                           "id": f"/subscriptions/{SUB}/resourceGroups/rg-demo/providers/Microsoft.Search/searchServices/srch-gg-test"},
        "statusMessage": {"error": {"code": "InsufficientResourcesAvailable", "message": "region is out of resources"}},
    }}


class Answers:
    def __init__(self, *values):
        self.values, self.asked = list(values), []

    def __call__(self, prompt):
        self.asked.append(prompt)
        return self.values.pop(0) if self.values else ""


def run(azure, tmp_path, argv=ARGS, **kw):
    azure.environment_path = tmp_path / "azd" / "gg-dev"
    return deploy.main(argv, runner=azure, state_path=tmp_path / "state.json", sleep=lambda s: None,
                       environment_path=azure.environment_path, **kw)


def test_the_whole_flow_runs_in_order_and_ends_with_the_real_key(tmp_path, capsys):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    steps = [c for c in azure.mutating() if c[0] == "azd" and c[1] in ("provision", "deploy")]
    assert [c[1] for c in steps] == ["provision", "deploy", "provision", "provision", "provision", "provision"]
    flat = [" ".join(c) for c in azure.mutating()]
    order = [next(i for i, c in enumerate(flat) if needle in c) for needle in (
        "env new gg-dev", "azd provision", "approve --id /pe/mine", "provision_auth", "create_agents.py --role counsellor --apply",
        "azd deploy", "containerapp job start")]
    order.append(max(i for i, c in enumerate(flat) if "env set MEMBER_TOKEN_PUBLIC_JWK" in c))   # the real key, not the placeholder
    order.append(next(i for i, c in enumerate(flat) if "run_dev_proof" in c))
    assert order == sorted(order)
    last_provision = max(i for i, c in enumerate(flat) if c.startswith("azd provision"))
    assert order[-2] < last_provision < order[-1]        # the final provision applies the real key, then the proof runs
    assert json.loads(azure.env["MEMBER_TOKEN_PUBLIC_JWK"]) == PUBLIC_JWK
    assert azure.env["GOVERNEDGRAPH_RESOURCE_GROUP_NAME"] == "rg-demo" and azure.env["ON_CALL_EMAIL"] == "ops@example.com"
    assert azure.env["STRATEGY_API_CLIENT_ID"].endswith("c1") and azure.env["CONTACT_CENTRE_AUDIENCE"] == "api://cc"
    assert azure.env["GOVERNEDGRAPH_ADMIN_CLIENT_ID"].endswith("c4")
    assert azure.env["GOVERNEDGRAPH_ADMIN_READER_GROUP_ID"].endswith("d4")
    assert azure.env["GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID"].endswith("d5")
    registration = next(i for i, c in enumerate(flat) if "configure_admin_console" in c)
    assert flat.index(next(c for c in flat if c.startswith("azd provision"))) < registration \
        < next(i for i, c in enumerate(flat) if "create_agents.py --role counsellor --apply" in c)
    assert azure.env["STRATEGY_RECOVERY_OPERATOR_ID"] == OPERATOR
    assert {azure.env[v] for v in deploy.AGENT_ROLES.values()} == {str(i) for i in range(1, 7)}
    assert "Deployment complete." in capsys.readouterr().out
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["sample_proof_executions"] == {
        "channel": azure.proof_output.strip(), "voice": azure.voice_proof_output.strip(),
    }
    checks = [c for c in azure.calls if c[:3] == [sys.executable, "-m", "pytest"]
              or c[:3] == [sys.executable, "-m", "pension_wtp.evaluation.run_evals"]
              or c[:3] == [sys.executable, "-m", "pension_wtp.demo.run_demo"]]
    assert len(checks) == 3
    assert max(azure.calls.index(c) for c in checks) < next(
        i for i, c in enumerate(azure.calls) if c[:2] == ["azd", "provision"])
    builds = {c[c.index("--target") + 1]: c for c in azure.calls if c[:3] == ["az", "acr", "build"]}
    assert set(builds) == {"p2", "dev-fixtures", "dev-voice"}
    assert all(c[c.index("--build-arg") + 1] == "SAMPLE=pension_wtp" for c in builds.values())
    assert azure.env["GOVERNEDGRAPH_SAMPLE"] == "pension_wtp"
    voice = next(c for c in azure.calls if "scripts.run_dev_proof" in c and "voice" in c)
    assert voice[voice.index("--image") + 1] == "registry.azurecr.io/sample-voice-proof@" + azure.voice_image_digest


def test_b1_b2_b7_sample_evaluation_failure_stops_before_azure_provisioning(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["pension_wtp.evaluation.run_evals"] = (1, "synthetic sample gate failed")
    assert run(azure, tmp_path) == 1
    assert not any(c[0] == "az" or c[0] == "azd" for c in azure.calls)
    state = json.loads((tmp_path / "state.json").read_text())
    assert "sample" not in state["completed"] and "provision-1" not in state["completed"]
    assert "sample pension_wtp" in capsys.readouterr().err
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0


def test_b8_deployment_configures_only_its_operator_before_agents_and_learning(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    setup = next(i for i, c in enumerate(azure.calls) if "scripts/configure_strategy_recovery.py" in c)
    registration = next(i for i, c in enumerate(azure.calls) if c[:3] == ["azd", "env", "set"]
                        and "STRATEGY_API_CLIENT_ID" in c)
    agents = next(i for i, c in enumerate(azure.calls) if "scripts/create_agents.py" in c and "--apply" in c)
    learning = next(i for i, c in enumerate(azure.calls) if "scripts/run_p2_container_jobs.py" in c)
    assert registration < setup < agents < learning
    assert "recovery-access" in json.loads((tmp_path / "state.json").read_text())["completed"]


def test_b8_resume_repairs_legacy_missing_recovery_access_without_redeploying(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    path = tmp_path / "state.json"
    state = json.loads(path.read_text())
    state["completed"] = [s for s in state["completed"] if s != "recovery-access"]
    path.write_text(json.dumps(state))
    azure.calls.clear()
    assert run(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 0
    assert any("scripts/configure_strategy_recovery.py" in c for c in azure.calls)
    assert not any(c[:2] in (["azd", "provision"], ["azd", "deploy"])
                   or "scripts/run_p2_container_jobs.py" in c for c in azure.calls)
    assert not any("scripts/lift_dev_freeze.py" in c for c in azure.calls)


def test_b8_recovery_access_setup_failure_stops_before_agents_or_learning(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["scripts/configure_strategy_recovery.py"] = (1, "Authorization_RequestDenied")
    assert run(azure, tmp_path) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert "recovery-access" not in state["completed"] and "learning" not in state["completed"]
    assert not any("scripts/run_p2_container_jobs.py" in c
                   or ("scripts/create_agents.py" in c and "--apply" in c) for c in azure.calls)
    assert "delegated recovery access setup failed" in capsys.readouterr().err


@pytest.mark.parametrize("change", [
    None, {"apply": False}, {"human_id": SUB}, {"api_client_id": SUB}, {"scope": "Other"},
    {"new_application_roles": True}, {"credentials": "not-none"}, {"environment": "other-dev"},
])
def test_b8_recovery_setup_result_must_confirm_the_restricted_configuration(tmp_path, capsys, monkeypatch, change):
    azure = FakeAzure()
    original = azure.run

    def output(args, **kwargs):
        result = original(args, **kwargs)
        if "scripts/configure_strategy_recovery.py" in args:
            return "not-json" if change is None else json.dumps({**json.loads(result), **change})
        return result

    monkeypatch.setattr(azure, "run", output)
    assert run(azure, tmp_path) == 1
    assert "recovery-access" not in json.loads((tmp_path / "state.json").read_text())["completed"]
    assert "delegated recovery access setup did not" in capsys.readouterr().err


def test_b8_targeted_access_repair_does_not_run_pending_learning_or_lift_freeze(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    path = tmp_path / "state.json"
    state = json.loads(path.read_text())
    state["completed"] = [s for s in state["completed"] if s not in ("recovery-access", "learning", "verify")]
    state["learning_receipts"] = {}
    path.write_text(json.dumps(state))
    azure.calls.clear()
    assert run(azure, tmp_path, ["--mode", "resume", "--only", "recovery-access", "--yes", "--non-interactive"]) == 0
    assert any("scripts/configure_strategy_recovery.py" in c for c in azure.calls)
    assert not any(c[:2] in (["azd", "provision"], ["azd", "deploy"])
                   or "scripts/run_p2_container_jobs.py" in c or "scripts/lift_dev_freeze.py" in c for c in azure.calls)
    assert "learning" not in json.loads(path.read_text())["completed"]


@pytest.mark.parametrize("target", [
    "tests/test_p2_components.py", "tests/test_container_p2.py",
    "tests/test_p2_fairness_agent_proof.py", "tests/test_scheduled_p2.py",
    "tests/test_voice_live.py", "tests/test_hosted_voice.py",
    "tests/test_channel_probe.py",
])
def test_learning_and_voice_regressions_are_checked_before_any_cloud_job_or_provision(tmp_path, capsys, target):
    azure = FakeAzure()
    azure.fail[target] = (1, "synthetic acceptance regression")
    assert run(azure, tmp_path) == 1
    assert not any(c[0] in ("az", "azd") for c in azure.calls)
    assert not any("scripts/run_p2_container_jobs.py" in c for c in azure.calls)
    state = json.loads((tmp_path / "state.json").read_text())
    assert "sample" not in state["completed"] and "learning" not in state["completed"]
    assert "no provisioning was attempted" in capsys.readouterr().err


def test_b8_sample_voice_failure_is_not_a_complete_deployment(tmp_path, capsys):
    azure = FakeAzure()
    azure.voice_proof_status = "Failed"
    assert run(azure, tmp_path) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert not state["channel_proof_execution"] and not state["sample_proof_executions"]
    assert "verify" not in state["completed"]
    output = capsys.readouterr()
    assert "voice proof" in output.err and "Deployment complete." not in output.out
    azure.voice_proof_status = "Succeeded"
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0


def test_b8_sample_voice_image_must_be_resolved_to_an_immutable_digest(tmp_path, capsys):
    azure = FakeAzure()
    azure.voice_image_digest = "latest"
    assert run(azure, tmp_path) == 1
    assert not any("scripts.run_dev_proof" in c and "voice" in c for c in azure.calls)
    assert "immutable image digest" in capsys.readouterr().err


def test_changed_sample_or_runtime_inputs_invalidate_local_and_live_acceptance(tmp_path, monkeypatch):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    azure.calls.clear()
    monkeypatch.setattr(deploy.SampleDefinition, "fingerprint", lambda self: "c" * 64)
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    assert any("pension_wtp.evaluation.run_evals" in c for c in azure.calls)
    assert any(c[:2] == ["azd", "deploy"] for c in azure.calls)
    assert [c[c.index("scripts.run_dev_proof") + 1] for c in azure.calls if "scripts.run_dev_proof" in c] == ["channel", "voice"]
    assert json.loads((tmp_path / "state.json").read_text())["sample_fingerprint"] == "c" * 64


def test_b8_legacy_channel_success_alone_cannot_skip_sample_voice_acceptance(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    path = tmp_path / "state.json"
    state = json.loads(path.read_text())
    state.pop("sample_proof_executions")
    path.write_text(json.dumps(state))
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    assert len([c for c in azure.calls if "scripts.run_dev_proof" in c]) == 2


def test_only_the_search_indexer_private_link_is_ever_approved(tmp_path):
    azure = FakeAzure()
    run(azure, tmp_path)
    approvals = [c for c in azure.calls if "approve" in c]
    assert len(approvals) == 1 and "/pe/mine" in approvals[0] and "/pe/other" not in approvals[0]


def test_contact_centre_is_enabled_before_the_first_provision(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    first = next(env for args, env in azure.process_envs if args[:2] == ["azd", "provision"])
    assert first["GOVERNEDGRAPH_SYNTHETIC_CONTACT_CENTRE"] == "true"
    checks = [args for args in azure.calls if args[:2] == ["az", "resource"] and "list" in args]
    assert len(checks) == 1
    assert checks[0][checks[0].index("--resource-group") + 1] == "rg-demo"
    assert checks[0][checks[0].index("--subscription") + 1] == SUB
    check_index = azure.calls.index(checks[0])
    assert check_index < next(i for i, args in enumerate(azure.calls) if args[:2] == ["azd", "deploy"])


def test_opting_out_of_console_sign_in_makes_no_entra_change_and_still_completes(tmp_path, gateway, capsys):
    class OptOut(FakeAzure):
        def azd(self, a):
            result = super().azd(a)
            if a[0] == "provision":
                self.env["GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL"] = "false"
            return result

    azure = OptOut()
    gateway.overview_status = 200   # an open console serves evidence read-only
    assert run(azure, tmp_path) == 0
    assert not any("configure_admin_console" in " ".join(c) for c in azure.calls)
    assert "GOVERNEDGRAPH_ADMIN_CLIENT_ID" not in azure.env
    assert "stays open with an access-control banner" in capsys.readouterr().out


def test_service_target_preflight_matches_the_azd_manifest():
    manifest = (deploy.REPO_ROOT / "azure.yaml").read_text()
    services = re.findall(r"^  ([a-z][a-z-]+):\n    project:", manifest, re.M)
    assert set(deploy.SERVICES) == set(services)


def test_verification_launch_failure_is_not_completion_and_resume_retries_only_the_proof(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["scripts.run_dev_proof"] = (1, "proof could not start")
    assert run(azure, tmp_path) == 1
    captured = capsys.readouterr()
    assert "proof could not start" in captured.err and "Deployment complete." not in captured.out
    before = json.loads((tmp_path / "state.json").read_text())
    assert "verify" not in before["completed"] and not before["channel_proof_execution"]
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    after = json.loads((tmp_path / "state.json").read_text())
    assert after["channel_proof_execution"] == azure.proof_output.strip()
    assert "verify" in after["completed"] and after["answers"] == before["answers"]
    assert not any(args[:2] in (["azd", "provision"], ["azd", "deploy"]) for args in azure.calls)
    assert "Deployment complete." in capsys.readouterr().out


@pytest.mark.parametrize("status", ["Failed", "Stopped", "Running", "Pending", "Processing", ""])
def test_unsuccessful_or_timed_out_channel_proofs_block_completion(tmp_path, capsys, status):
    azure = FakeAzure()
    azure.proof_status = status
    assert run(azure, tmp_path) == 1
    captured = capsys.readouterr()
    assert "did not succeed" in captured.err and "Deployment complete." not in captured.out
    state = json.loads((tmp_path / "state.json").read_text())
    assert "verify" not in state["completed"] and not state["channel_proof_execution"]
    polls = [args for args in azure.calls if args[:5] == ["az", "containerapp", "job", "execution", "show"]
             and azure.proof_output.strip() in args]
    assert len(polls) == (1 if status in ("Failed", "Stopped") else 60)


@pytest.mark.parametrize("output", ["", "one\ntwo\n", "invalid/execution"])
def test_invalid_proof_execution_output_is_an_explicit_failure(tmp_path, capsys, output):
    azure = FakeAzure()
    azure.proof_output = output
    assert run(azure, tmp_path) == 1
    captured = capsys.readouterr()
    assert "valid execution name" in captured.err and "Deployment complete." not in captured.out
    assert "verify" not in json.loads((tmp_path / "state.json").read_text())["completed"]


def test_legacy_completion_without_proof_evidence_is_reverified_on_resume(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    path = tmp_path / "state.json"
    state = json.loads(path.read_text())
    state.pop("channel_proof_execution")
    path.write_text(json.dumps(state))
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    assert json.loads(path.read_text())["channel_proof_execution"] == azure.proof_output.strip()
    assert any("scripts.run_dev_proof" in args for args in azure.calls)
    assert not any(args[:2] in (["azd", "provision"], ["azd", "deploy"]) for args in azure.calls)


def test_failed_explicit_reverification_removes_previous_success(tmp_path, capsys):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    capsys.readouterr()
    azure.proof_status = "Failed"
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume", "--only", "verify"]) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert "verify" not in state["completed"] and not state["channel_proof_execution"]
    assert "Deployment complete." not in capsys.readouterr().out


def test_resume_repairs_a_missing_contact_centre_without_recreating_agents_or_names(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["azd deploy"] = (1, "simulated interrupted deployment")
    assert run(azure, tmp_path) == 1
    before = json.loads((tmp_path / "state.json").read_text())
    assert before["completed"][-1] == "agents"
    azure.service_targets = [tags for tags in azure.service_targets if tags["azd-service-name"] != "contact-centre"]
    azure.env.pop("GOVERNEDGRAPH_SYNTHETIC_CONTACT_CENTRE")
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    steps = [args[1] for args in azure.calls if args[:2] in (["azd", "provision"], ["azd", "deploy"])]
    assert steps == ["provision", "deploy", "provision", "provision", "provision", "provision"]
    assert "Missing azd service targets: contact-centre" in capsys.readouterr().out
    assert not any("scripts/create_agents.py" in args for args in azure.calls)
    assert not any("scripts/configure_contact_centre.py" in args for args in azure.calls)
    assert json.loads((tmp_path / "state.json").read_text())["answers"] == before["answers"]


def test_missing_service_targets_stop_before_builds_if_reprovision_does_not_fix_them(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["azd deploy"] = (1, "interrupted")
    assert run(azure, tmp_path) == 1
    azure.service_targets = []
    azure.fail.clear()
    azure.calls.clear()
    original = azure.azd

    def without_targets(args):
        result = original(args)
        if args[0] == "provision":
            azure.service_targets = []
        return result

    azure.azd = without_targets
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 1
    assert "azd service targets are still missing" in capsys.readouterr().err
    assert len([args for args in azure.calls if args[:2] == ["azd", "provision"]]) == 1
    assert not any(args[:2] == ["azd", "deploy"] for args in azure.calls)
    assert "deploy" not in json.loads((tmp_path / "state.json").read_text())["completed"]


@pytest.mark.parametrize("problem", ["duplicate", "foreign-environment", "inventory-failure"])
def test_unsafe_or_unreadable_service_targets_never_trigger_reprovision_or_builds(tmp_path, capsys, problem):
    azure = FakeAzure()
    azure.fail["azd deploy"] = (1, "interrupted")
    assert run(azure, tmp_path) == 1
    azure.calls.clear()
    azure.fail.clear()
    if problem == "duplicate":
        azure.service_targets.append(dict(azure.service_targets[-1]))
        expected = "multiple Container Apps"
    elif problem == "foreign-environment":
        azure.service_targets[-1]["azd-env-name"] = "another-environment"
        expected = "tagged for another environment"
    else:
        azure.fail["az resource list"] = (1, "AuthorizationFailed")
        expected = "AuthorizationFailed"
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 1
    assert expected in capsys.readouterr().err
    assert not any(args[:2] in (["azd", "provision"], ["azd", "deploy"]) for args in azure.calls)


def test_the_placeholder_key_is_used_first_and_never_overwrites_a_real_key(tmp_path):
    azure = FakeAzure()
    run(azure, tmp_path)
    placeholder_sets = [c for c in azure.calls if c[:4] == ["azd", "env", "set", "MEMBER_TOKEN_PUBLIC_JWK"]]
    assert len(placeholder_sets) == 2           # the placeholder, then the real key
    before = azure.env["MEMBER_TOKEN_PUBLIC_JWK"]
    run(azure, tmp_path, ARGS + ["--force"])    # a forced rerun of every step
    assert json.loads(azure.env["MEMBER_TOKEN_PUBLIC_JWK"])["n"] == json.loads(before)["n"] == "abc"


def test_a_failure_stops_cleanly_and_the_rerun_resumes_without_repeating_provisioning(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["create_agents.py --role intent"] = (99, "Forbidden")
    assert run(azure, tmp_path) == 1
    err = capsys.readouterr().err
    assert "intent" in err and "--mode resume" in err
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["completed"] == ["sample", "preflight", "environment", "provision-1", "private-link", "entra",
                                  "recovery-access"]
    azure.fail.clear()
    provisions_before = len([c for c in azure.calls if c[:2] == ["azd", "provision"]])
    assert run(azure, tmp_path, ["--yes", "--non-interactive"]) == 0
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == provisions_before + 4   # passes 2 and 3, then the learning plane
    assert "[skip] provision-1" in capsys.readouterr().out


def test_agent_creation_waits_for_role_propagation(tmp_path, capsys):
    azure = FakeAzure()
    azure.agent_failures = 2
    assert run(azure, tmp_path) == 0
    assert "waiting for permissions to propagate" in capsys.readouterr().out


@pytest.mark.parametrize("args,scoped", [
    (("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"), False),
    (("account", "list"), False),
    (("login",), False),
    (("account", "show"), True),
    (("role", "assignment", "list"), True),
    (("provider", "show", "-n", "Microsoft.Search"), True),
    (("cognitiveservices", "usage", "list", "-l", "swedencentral"), True),
    (("group", "exists", "-n", "rg-demo"), True),
    (("search", "service", "list", "-g", "rg-demo"), True),
    (("keyvault", "list-deleted"), True),
    (("deployment", "operation", "group", "list", "-g", "rg-demo", "-n", "data"), True),
    (("network", "private-endpoint-connection", "list", "--id", "/synthetic"), True),
    (("containerapp", "job", "start", "-g", "rg-demo", "-n", deploy.ISSUER_JOB), True),
])
def test_subscription_flags_are_only_added_to_subscription_scoped_commands(tmp_path, args, scoped):
    azure = FakeAzure()
    state = deploy.State(answers=deploy.asdict(deploy.Answers(subscription_id=SUB)))
    deployer = deploy.Deployer(azure, deploy.Prompter(interactive=False), state, state_path=tmp_path / "state.json")
    deployer.az(*args)
    command = azure.calls[-1]
    assert ("--subscription" in command) is scoped
    if scoped:
        assert command[command.index("--subscription") + 1] == SUB
    assert command[-1] == "--only-show-errors"


@pytest.mark.parametrize("flag", ["--subscription", "-s"])
def test_an_explicit_subscription_is_not_duplicated(tmp_path, flag):
    azure = FakeAzure()
    state = deploy.State(answers=deploy.asdict(deploy.Answers(subscription_id=SUB)))
    deployer = deploy.Deployer(azure, deploy.Prompter(interactive=False), state, state_path=tmp_path / "state.json")
    deployer.az("account", "set", flag, SUB)
    command = azure.calls[-1]
    assert command.count(SUB) == 1
    assert command.count("--subscription") + command.count("-s") == 1


def test_interactive_login_and_signed_in_user_preflight_do_not_receive_subscription(tmp_path):
    azure = FakeAzure()
    azure.fail["az account show"] = (1, "not logged in")
    prompter = deploy.Prompter(interactive=True, ask=Answers(), echo=lambda *_: None)
    assert run(azure, tmp_path, ARGS + ["--no-voice-demo"], prompter=prompter) == 0
    login = next(c for c in azure.calls if c[:2] == ["az", "login"])
    identity = next(c for c in azure.calls if c[:3] == ["az", "ad", "signed-in-user"])
    assert "--subscription" not in login and "--subscription" not in identity
    assert ["az", "account", "set", "--subscription", SUB, "--only-show-errors"] in azure.calls


def test_plan_runs_nothing(tmp_path, capsys):
    azure = FakeAzure()
    assert run(azure, tmp_path, ["--plan", "--resource-group", "rg-demo"]) == 0
    assert azure.calls == [] and not (tmp_path / "state.json").exists()
    out = capsys.readouterr().out
    assert "rg-demo" in out and "Nothing was run" in out and "member-key" in out


def test_unattended_runs_need_every_answer_and_yes(tmp_path, capsys):
    assert run(FakeAzure(), tmp_path, ["--non-interactive", "--yes", "--subscription", SUB]) == 1
    assert "On-call email" in capsys.readouterr().err
    assert run(FakeAzure(), tmp_path, ["--non-interactive", "--subscription", SUB, "--on-call-email", "a@b.co"]) == 1
    assert "--yes" in capsys.readouterr().err


def test_the_questions_validate_and_offer_defaults():
    asked = Answers("", "bad name!", "rg-ok", "not-an-email", "ops@example.com", "y")
    prompter = deploy.Prompter(interactive=True, ask=asked, echo=lambda *_: None)
    assert prompter.question("Azure region", "swedencentral", deploy.validate_location) == "swedencentral"
    assert prompter.question("Resource group", "rg-gg-dev", deploy.validate_rg) == "rg-ok"
    assert prompter.question("Email", "", deploy.validate_email) == "ops@example.com"
    assert prompter.confirm("Presenter?") is True
    assert "[swedencentral]" in asked.asked[0] and "[rg-gg-dev]" in asked.asked[1]


@pytest.mark.parametrize("name,ok", [("rg-gg-dev", True), ("My_RG.1", True), ("trailing.", False), ("", False),
                                     ("a" * 91, False), ("bad name", False), ("x" * 90, True)])
def test_resource_group_names_follow_azure_rules(name, ok):
    assert (deploy.validate_rg(name) is None) is ok


def test_interactive_questions_drive_the_answers_and_saved_answers_are_reused(tmp_path, capsys):
    azure = FakeAzure()
    answers = Answers("", "", "swedencentral", "rg-asked", "ops@example.com", "y")  # mode, sub, region, rg, email, start
    prompter = deploy.Prompter(interactive=True, ask=answers, echo=print)
    assert run(azure, tmp_path, [], prompter=prompter) == 0
    assert azure.env["GOVERNEDGRAPH_RESOURCE_GROUP_NAME"] == "rg-asked"
    azure2 = FakeAzure()
    assert run(azure2, tmp_path, ["--yes", "--non-interactive", "--force"]) == 0   # no questions: answers are saved
    assert azure2.env["GOVERNEDGRAPH_RESOURCE_GROUP_NAME"] == "rg-asked"


def test_changing_an_answer_after_provisioning_is_refused(tmp_path, capsys):
    azure = FakeAzure()
    run(azure, tmp_path)
    capsys.readouterr()
    assert run(FakeAzure(), tmp_path, ["--yes", "--non-interactive", "--resource-group", "rg-other"]) == 1
    assert "--reconfigure" in capsys.readouterr().err
    assert run(FakeAzure(), tmp_path, ["--yes", "--non-interactive", "--reconfigure"] + ARGS[2:]) == 1
    assert "already provisioned" in capsys.readouterr().err


def test_an_existing_group_in_another_region_is_refused(tmp_path, capsys):
    azure = FakeAzure()
    azure.rg_exists = "true"
    assert run(azure, tmp_path, ARGS[:ARGS.index("--location")] + ["--location", "francecentral"] + ARGS[ARGS.index("--location") + 2:]) == 1
    assert "already exists in swedencentral" in capsys.readouterr().err
    assert not [c for c in azure.calls if c[:2] == ["azd", "provision"]]


def test_missing_model_quota_stops_an_unattended_run_unless_overridden(tmp_path, capsys):
    azure = FakeAzure()
    original = azure.az

    def low(a):
        if a[:2] == ["cognitiveservices", "usage"]:
            return json.dumps([{"name": {"value": "OpenAI.DataZoneStandard.gpt-4.1"}, "currentValue": 90, "limit": 100}])
        return original(a)

    azure.az = low
    assert run(azure, tmp_path) == 1
    assert "model quota" in capsys.readouterr().err
    assert not [c for c in azure.calls if c[:2] == ["azd", "provision"]]       # fails before the 30-minute step
    findings = deploy.quota_findings(json.loads(low(["cognitiveservices", "usage"])))
    assert any("gpt-4.1 " in f and "10 of the 50" in f for f in findings) and any("could not confirm" in f for f in findings)
    assert run(azure, tmp_path, ARGS + ["--ignore-warnings"]) == 0


def test_the_member_key_is_taken_only_if_it_is_a_pure_public_key():
    assert deploy.extract_public_jwk("noise " + json.dumps(PUBLIC_JWK) + " noise") == PUBLIC_JWK
    assert deploy.extract_public_jwk(json.dumps(json.dumps(PUBLIC_JWK))) == PUBLIC_JWK       # JSON-escaped log line
    with pytest.raises(deploy.UnsafeKey):
        deploy.extract_public_jwk(json.dumps({**PUBLIC_JWK, "d": "secret"}))
    with pytest.raises(DeployError, match="no public key"):
        deploy.extract_public_jwk("nothing here")


def test_unreadable_issuer_logs_fail_an_unattended_run_with_a_clear_action(tmp_path, capsys):
    azure = FakeAzure()
    azure.logs = "no key in here"
    assert run(azure, tmp_path) == 1
    assert "ContainerAppConsoleLogs" in capsys.readouterr().err
    assert "member-key" not in json.loads((tmp_path / "state.json").read_text())["completed"]


def test_the_presenter_gets_a_second_pass_for_its_new_client_id(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path, ARGS + ["--voice-demo"]) == 0
    assert azure.env["DEPLOY_VOICE_DEMO"] == "true"
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == 6
    azure = FakeAzure()
    run(azure, tmp_path / "other", ARGS + ["--no-voice-demo"])
    assert "DEPLOY_VOICE_DEMO" not in azure.env and len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == 5


def test_a_fresh_run_by_default_matches_the_running_deployment_including_conversation_text(tmp_path, gateway, capsys):
    azure = FakeAzure()
    assert run(azure, tmp_path, DEFAULT_ARGS) == 0
    assert azure.env["DEPLOY_VOICE_DEMO"] == "true"
    assert azure.env["GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS"] == "true"
    assert azure.env["GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP"] == "true"
    assert azure.env["GOVERNEDGRAPH_ADMIN_CLIENT_ID"].endswith("c4")
    out = capsys.readouterr().out
    assert "(with synthetic conversation capture and the console's conversation view)" in out
    assert "Conversation capture verified: 7 synthetic conversation turns" in out
    flat = [" ".join(c) for c in azure.calls]
    assert next(i for i, c in enumerate(flat) if "run_dev_proof" in c) < next(
        i for i, c in enumerate(flat) if "monitor log-analytics query" in c)
    state = json.loads((tmp_path / "state.json").read_text())
    assert "admin-console" in state["completed"] and state["completed"][-1] == "admin-console"
    urls = [url.removeprefix(GATEWAY) for url, _ in gateway.requests]
    assert {"/health/admin/healthz", "/admin/", "/admin/api/overview", "/admin/docs"} <= set(urls)
    assert next(h for u, h in gateway.requests if u.endswith("/admin/api/overview")) == {"X-Admin-Session": "capability"}


def test_without_the_presenter_capture_and_lookup_are_off_and_not_checked(tmp_path, capsys):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    assert azure.env["GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS"] == "false"
    assert azure.env["GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP"] == "false"
    assert azure.capture_queries == []
    assert "Conversation capture skipped" in capsys.readouterr().out


@pytest.mark.parametrize("field,value,message", [
    ("overview_status", 200, "evidence without a sign-in returned HTTP 200, expected 401"),
    ("page_status", 500, "the console page returned HTTP 500"),
    ("health_status", 503, "the console health route returned HTTP 503"),
    ("unlisted_status", 200, "the unlisted route /docs returned HTTP 200, expected 404"),
])
def test_a_console_that_is_down_open_or_exposing_routes_blocks_completion_and_resumes(
        tmp_path, gateway, capsys, field, value, message):
    azure = FakeAzure()
    setattr(gateway, field, value)
    assert run(azure, tmp_path) == 1
    assert message in capsys.readouterr().err
    completed = json.loads((tmp_path / "state.json").read_text())["completed"]
    assert "verify" in completed and "admin-console" not in completed
    setattr(gateway, field, {"overview_status": 401, "page_status": 200, "health_status": 200, "unlisted_status": 404}[field])
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    assert not any(c[:2] == ["azd", "provision"] or c[:2] == ["azd", "deploy"] for c in azure.calls)
    assert "Deployment complete." in capsys.readouterr().out


def test_an_open_console_without_sign_in_is_read_only_and_checked_as_such(tmp_path, gateway, capsys):
    class OptOut(FakeAzure):
        def azd(self, a):
            result = super().azd(a)
            if a[0] == "provision":
                self.env["GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL"] = "false"
            return result

    azure = OptOut()
    gateway.overview_status = 200
    assert run(azure, tmp_path) == 0
    assert "evidence access is open, read-only" in capsys.readouterr().out


def test_conversations_that_never_reach_the_workspace_block_completion_after_fifteen_minutes(tmp_path, capsys):
    azure = FakeAzure()
    azure.capture_spans = "0"
    assert run(azure, tmp_path, DEFAULT_ARGS) == 1
    err = capsys.readouterr().err
    assert "within 15 minutes" in err and "GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS=true" in err
    assert len(azure.capture_queries) == 15
    assert "admin-console" not in json.loads((tmp_path / "state.json").read_text())["completed"]


def test_missing_log_analytics_read_access_is_explained_not_reported_as_missing_conversations(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["monitor log-analytics query"] = (99, "AuthorizationFailed")
    assert run(azure, tmp_path, DEFAULT_ARGS) == 1
    err = capsys.readouterr().err
    assert "needs read access to it" in err and "AuthorizationFailed" in err and "within 15 minutes" not in err


def test_the_console_check_is_skipped_when_the_console_is_not_deployed(tmp_path, monkeypatch, gateway, capsys):
    monkeypatch.setattr(deploy.Deployer, "admin_enabled", lambda self: False)
    assert run(FakeAzure(), tmp_path, DEFAULT_ARGS) == 0
    assert "Admin console not deployed; nothing to check." in capsys.readouterr().out
    assert gateway.requests == []


def test_a_resumed_older_deployment_without_the_workspace_output_looks_it_up(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path, DEFAULT_ARGS) == 0
    state = json.loads((tmp_path / "state.json").read_text())
    state["completed"].remove("admin-console")
    (tmp_path / "state.json").write_text(json.dumps(state))
    azure.env.pop("LOG_ANALYTICS_WORKSPACE_ID")
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    assert any(c[:4] == ["az", "monitor", "log-analytics", "workspace"] for c in azure.calls)
    assert azure.capture_queries


def test_a_provision_failure_tells_the_operator_to_fix_and_rerun(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["azd provision"] = (1, "QuotaExceeded")
    assert run(azure, tmp_path) == 1
    err = capsys.readouterr().err
    assert "azd provision failed" in err and "re-run" in err
    assert "provision-1" not in json.loads((tmp_path / "state.json").read_text())["completed"]


@pytest.fixture
def fixture_pull_case(tmp_path, monkeypatch):
    azure = FakeAzure()
    scope = f"/subscriptions/{SUB}/resourceGroups/rg-demo/providers/"
    registry = scope + "Microsoft.ContainerRegistry/registries/crmlsynthetic"
    identity = scope + "Microsoft.ManagedIdentity/userAssignedIdentities/id-gg-dev-fixtures-synthetic"
    principal = "00000000-0000-4000-8000-000000000099"
    digest = "sha256:" + "d" * 64
    image = f"crmlsynthetic.azurecr.io/governedgraph/dev-fixtures@{digest}"
    azure.env.update(AZURE_ML_REGISTRY_ID=registry, DEV_FIXTURES_IMAGE=image, DEPLOY_DEV_FIXTURES="true",
                     GOVERNEDGRAPH_RESOURCE_GROUP_NAME="rg-demo")
    data = {
        "config": {"roleAssignmentMode": "LegacyRegistryPermissions",
                   "policies": {"azureAdAuthenticationAsArmPolicy": {"status": "enabled"}}},
        "principal": principal, "digest": digest,
        "assignments": [{"principalId": principal, "scope": registry, "condition": None,
                         "roleDefinitionId": "/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"}],
    }
    original = azure.az
    reads = []

    def az(args):
        reads.append(args)
        if args[:2] == ["acr", "show"]:
            assert args[args.index("--name") + 1] == "crmlsynthetic"
            assert args[args.index("--resource-group") + 1] == "rg-demo"
            return json.dumps(data["config"])
        if args[:2] == ["identity", "show"]:
            assert args[args.index("--name") + 1] == "id-gg-dev-fixtures-synthetic"
            assert args[args.index("--resource-group") + 1] == "rg-demo"
            return data["principal"]
        if args[:3] == ["role", "assignment", "list"]:
            return json.dumps(data["assignments"])
        if args[:3] == ["acr", "repository", "show"]:
            return data["digest"]
        return original(args)

    monkeypatch.setattr(azure, "az", az)
    waits = []
    deployer = deploy.Deployer(azure, deploy.Prompter(interactive=False), deploy.State(),
                               state_path=tmp_path / "state.json", sleep=waits.append)
    deployer.answers = deploy.Answers(subscription_id=SUB, resource_group="rg-demo")
    error = ("InvalidParameterValueInContainerTemplate: Field 'template.containers.publisher.image' is invalid: "
             f"'Invalid value: \"{image}\": unable to pull image using Managed identity {identity} "
             "for registry crmlsynthetic.azurecr.io'")
    return SimpleNamespace(deployer=deployer, azure=azure, data=data, error=error, waits=waits, reads=reads)


def test_fixture_pull_propagation_retries_with_same_scope_image_and_permissions(fixture_pull_case, capsys):
    case = fixture_pull_case
    case.azure.provision_errors = [case.error, case.error]
    before = dict(case.azure.env)
    case.deployer.provision()
    assert case.waits == [60, 60]
    assert len([c for c in case.azure.calls if c[:2] == ["azd", "provision"]]) == 3
    assert "retry 2/5" in capsys.readouterr().out
    for key in ("DEV_FIXTURES_IMAGE", "DEPLOY_DEV_FIXTURES"):
        assert case.azure.env[key] == before[key]
    assert not [c for c in case.azure.calls if c[:4] == ["azd", "env", "set", "AZURE_ML_REGISTRY_ID"]]
    assert case.deployer.answers.subscription_id == SUB and case.deployer.answers.resource_group == "rg-demo"
    assert not [c for c in case.azure.calls if "create" in c or "delete" in c or "recover" in c]
    assert all(c[c.index("--subscription") + 1] == SUB for c in case.azure.calls if c[0] == "az")


def test_persistent_fixture_pull_failure_is_bounded_and_never_completes(fixture_pull_case):
    case = fixture_pull_case
    case.azure.provision_errors = [case.error] * 6
    with pytest.raises(deploy.DeployError, match="still fails after five retries"):
        case.deployer.provision()
    assert case.waits == [60] * 5
    assert len([c for c in case.azure.calls if c[:2] == ["azd", "provision"]]) == 6
    assert case.deployer.state.completed == [] and not case.deployer.state_path.exists()


@pytest.mark.parametrize("change,message", [
    (lambda d: d.update(assignments=[]), "no unconditional registry-scoped AcrPull"),
    (lambda d: d["assignments"][0].update(scope="/subscriptions/other"), "no unconditional registry-scoped AcrPull"),
    (lambda d: d["assignments"][0].update(principalId="other"), "no unconditional registry-scoped AcrPull"),
    (lambda d: d["assignments"][0].update(condition="restricted"), "no unconditional registry-scoped AcrPull"),
    (lambda d: d.update(digest="sha256:" + "e" * 64), "exact fixture image digest"),
    (lambda d: d["config"].update(roleAssignmentMode="AbacRepositoryPermissions"), "not configured for AcrPull"),
    (lambda d: d["config"]["policies"]["azureAdAuthenticationAsArmPolicy"].update(status="disabled"), "not configured for AcrPull"),
])
def test_fixture_pull_configuration_errors_stop_without_retry(fixture_pull_case, change, message):
    case = fixture_pull_case
    change(case.data)
    case.azure.provision_errors = [case.error]
    with pytest.raises(deploy.DeployError, match=message):
        case.deployer.provision()
    assert case.waits == []


@pytest.mark.parametrize("change", [
    lambda text: text.replace("rg-demo/providers", "rg-other/providers"),
    lambda text: text.replace("id-gg-dev-fixtures-", "id-gg-runtime-"),
    lambda text: text.replace("crmlsynthetic.azurecr.io'", "other.azurecr.io'"),
    lambda text: text.replace("template.containers.publisher.image", "template.containers.runtime.image"),
    lambda text: text.replace("sha256:" + "d" * 64, "sha256:" + "e" * 64),
    lambda text: text + "\nAuthorizationFailed: another resource failed",
    lambda text: text + "\nInsufficientResourcesAvailable: another resource failed",
])
def test_unrelated_or_mixed_pull_errors_do_not_trigger_fixture_retries(fixture_pull_case, change):
    case = fixture_pull_case
    case.azure.provision_errors = [change(case.error)]
    with pytest.raises(deploy.DeployError, match="azd provision"):
        case.deployer.provision()
    assert case.waits == [] and case.reads == []


def test_fixture_pull_diagnostic_failure_is_explicit_and_never_retried(fixture_pull_case, monkeypatch):
    case = fixture_pull_case
    case.azure.provision_errors = [case.error]
    case.azure.fail["acr show"] = (1, "synthetic diagnostic read denied")
    with pytest.raises(deploy.DeployError, match="Could not verify.*synthetic diagnostic read denied"):
        case.deployer.provision()
    assert case.waits == []


def test_search_capacity_retries_other_eu_regions_and_persists_the_choice(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["azd provision"] = (2, "InsufficientResourcesAvailable")
    assert run(azure, tmp_path) == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["search_capacity_regions"] == ["swedencentral", "germanywestcentral"]
    assert state["answers"]["search_location"] == azure.env["AZURE_SEARCH_LOCATION"] == "northeurope"
    assert azure.env["AZURE_LOCATION"] == "swedencentral"
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == 7
    assert "retrying in germanywestcentral" in capsys.readouterr().out
    assert not [c for c in azure.calls if "delete" in c]
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--only", "provision-2"]) == 0
    assert azure.env["AZURE_SEARCH_LOCATION"] == "northeurope"


def test_search_fallback_skips_unsupported_regions_and_never_uses_non_eu(tmp_path):
    azure = FakeAzure()
    azure.search_locations = ["East US", "UK South", "Norway East", "France Central"]
    azure.fail["azd provision"] = (1, "InsufficientResourcesAvailable")
    assert run(azure, tmp_path) == 0
    assert azure.env["AZURE_SEARCH_LOCATION"] == "francecentral"


def test_search_capacity_exhaustion_is_bounded_and_resume_does_not_cycle(tmp_path, capsys):
    azure = FakeAzure()
    azure.search_locations = ["Sweden Central", "Germany West Central"]
    azure.fail["azd provision"] = (99, "InsufficientResourcesAvailable")
    assert run(azure, tmp_path) == 1
    assert "alternatives exhausted" in capsys.readouterr().err
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == 2
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["search_capacity_regions"] == ["swedencentral", "germanywestcentral"]
    assert "provision-1" not in state["completed"]
    assert run(azure, tmp_path, ["--yes", "--non-interactive"]) == 1
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == 3


@pytest.mark.parametrize("existing_state", ["Succeeded", "Failed"])
def test_capacity_fallback_never_relocates_or_deletes_an_existing_search_service(tmp_path, capsys, existing_state):
    azure = FakeAzure()
    azure.search_services = [{"id": capacity_operation()["properties"]["targetResource"]["id"],
                              "provisioningState": existing_state}]
    azure.fail["azd provision"] = (1, "InsufficientResourcesAvailable")
    assert run(azure, tmp_path) == 1
    assert "already exists" in capsys.readouterr().err
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"
    assert not [c for c in azure.calls if "delete" in c]


@pytest.mark.parametrize("change", ["other-resource", "old-operation", "wrong-error", "other-group", "no-operation"])
def test_capacity_fallback_requires_a_current_search_capacity_error(tmp_path, capsys, change):
    azure = FakeAzure()
    operation = capacity_operation()
    props = operation["properties"]
    if change == "other-resource":
        props["targetResource"]["resourceType"] = "Microsoft.CognitiveServices/accounts/deployments"
    elif change == "old-operation":
        props["timestamp"] = "2020-01-01T00:00:00Z"
    elif change == "wrong-error":
        props["statusMessage"]["error"]["code"] = "AuthorizationFailed"
    elif change == "other-group":
        props["targetResource"]["id"] = props["targetResource"]["id"].replace("rg-demo", "rg-other")
    azure.search_operations = [] if change == "no-operation" else [operation]
    # A future timestamp keeps all but the explicitly stale case within the current attempt.
    if change != "old-operation":
        props["timestamp"] = "2099-01-01T00:00:00Z"
    azure.fail["azd provision"] = (1, "InsufficientResourcesAvailable")
    assert run(azure, tmp_path) == 1
    assert "could not be confirmed" in capsys.readouterr().err
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == 1


def test_string_encoded_nested_arm_capacity_errors_are_supported(tmp_path):
    azure = FakeAzure()
    operation = capacity_operation()
    operation["properties"]["timestamp"] = "2099-01-01T00:00:00Z"
    operation["properties"]["statusMessage"] = json.dumps(
        {"error": {"code": "ResourceDeploymentFailure", "details": [{"code": "InsufficientResourcesAvailable"}]}})
    azure.search_operations = [operation]
    azure.fail["azd provision"] = (1, "InsufficientResourcesAvailable")
    assert run(azure, tmp_path) == 0
    assert azure.env["AZURE_SEARCH_LOCATION"] == "germanywestcentral"


@pytest.mark.parametrize("command", ["az deployment operation", "az search service list", "az provider show --namespace"])
def test_capacity_diagnostic_failures_stop_without_changing_region(tmp_path, capsys, command):
    azure = FakeAzure()
    azure.fail["azd provision"] = (1, "InsufficientResourcesAvailable")
    azure.fail[command] = (1, "AuthorizationFailed")
    assert run(azure, tmp_path) == 1
    assert "fallback could not finish" in capsys.readouterr().err
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"


def test_resuming_after_a_failed_env_update_synchronizes_the_saved_fallback(tmp_path):
    azure = FakeAzure()
    azure.fail["azd provision"] = (1, "InsufficientResourcesAvailable")
    azure.fail["env set AZURE_SEARCH_LOCATION germanywestcentral"] = (1, "temporarily unavailable")
    assert run(azure, tmp_path) == 1
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"
    assert json.loads((tmp_path / "state.json").read_text())["answers"]["search_location"] == "germanywestcentral"
    assert run(azure, tmp_path, ["--yes", "--non-interactive"]) == 0
    assert azure.env["AZURE_SEARCH_LOCATION"] == "germanywestcentral"


def test_original_search_preference_is_accepted_on_resume_after_automatic_fallback(tmp_path):
    azure = FakeAzure()
    azure.fail["azd provision"] = (1, "InsufficientResourcesAvailable")
    argv = ARGS + ["--search-location", "swedencentral"]
    assert run(azure, tmp_path, argv) == 0
    assert azure.env["AZURE_SEARCH_LOCATION"] == "germanywestcentral"
    assert run(azure, tmp_path, argv + ["--only", "provision-2"]) == 0
    assert azure.env["AZURE_SEARCH_LOCATION"] == "germanywestcentral"


def deleted_resource_failure(azure):
    scope = f"/subscriptions/{SUB}/resourceGroups/rg-demo/providers/"
    tags = {"azd-env-name": "gg-dev"}
    azure.deleted_vaults = [{"properties": {"vaultId": scope + "Microsoft.KeyVault/vaults/kv-gg-test",
                                           "location": "swedencentral", "tags": tags.copy()}}]
    azure.deleted_accounts = [
        {"id": scope + "Microsoft.CognitiveServices/accounts/" + name, "location": location, "tags": tags.copy()}
        for name, location in (("ais-gg-test", "swedencentral"), ("cs-gg-test", "francecentral"))]
    return ("(x) Failed: Key Vault: kv-gg-test (1s)\n(x) Failed: Search service: srch-gg-test (2s)\n"
            "Deployment Error Details:\nInvalidTemplateDeployment: AI validation failed\n" +
            "\n".join(f"FlagMustBeSetForRestore: An existing resource with ID '{v['id']}' has been soft-deleted."
                      for v in azure.deleted_accounts) +
            "\nConflictError: A vault with the same name already exists in deleted state.\n"
            "InsufficientResourcesAvailable: Search region has no capacity.")


def test_mixed_deleted_resource_failures_are_recovered_before_search_retry(tmp_path, capsys):
    azure = FakeAzure()
    azure.provision_errors = [deleted_resource_failure(azure), "InsufficientResourcesAvailable"]
    assert run(azure, tmp_path, ARGS + ["--recover-soft-deleted"]) == 0
    recoveries = [c for c in azure.calls if "recover" in c]
    assert len(recoveries) == 3 and recoveries[0][1] == "keyvault"
    assert len(azure.recovered_ids) == 3
    assert azure.env["AZURE_SEARCH_LOCATION"] == "germanywestcentral"
    flat = [" ".join(c) for c in azure.calls]
    assert max(i for i, c in enumerate(flat) if " recover " in c) < next(
        i for i, c in enumerate(flat) if "env set AZURE_SEARCH_LOCATION germanywestcentral" in c)
    assert all("--subscription" in c and c[c.index("--subscription") + 1] == SUB for c in recoveries)
    assert not [c for c in azure.calls if "purge" in c or "delete" in c]
    assert "preserving its data, keys and region" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["wrong-env", "wrong-region", "missing-account", "wrong-group"])
def test_deleted_resource_recovery_requires_proven_scope_and_ownership(tmp_path, capsys, change):
    azure = FakeAzure()
    error = deleted_resource_failure(azure)
    if change == "wrong-env":
        azure.deleted_vaults[0]["properties"]["tags"]["azd-env-name"] = "other-dev"
    elif change == "wrong-region":
        azure.deleted_accounts[0]["location"] = "eastus"
    elif change == "missing-account":
        azure.deleted_accounts.pop()
    else:
        error = error.replace("resourceGroups/rg-demo", "resourceGroups/rg-other")
    azure.provision_errors = [error]
    assert run(azure, tmp_path, ARGS + ["--recover-soft-deleted"]) == 1
    assert "review" in capsys.readouterr().err.lower() or change == "wrong-group"
    assert not azure.recovered_ids
    assert not [c for c in azure.calls if "recover" in c or "purge" in c]
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"


def test_recovery_does_not_repeat_when_a_conflict_persists(tmp_path, capsys):
    azure = FakeAzure()
    error = deleted_resource_failure(azure)
    azure.provision_errors = [error, error]
    assert run(azure, tmp_path, ARGS + ["--recover-soft-deleted"]) == 1
    assert "refusing repeated recovery" in capsys.readouterr().err
    assert len([c for c in azure.calls if "recover" in c]) == 3
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == 2
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"


def test_recovery_failure_preserves_the_error_and_does_not_try_another_region(tmp_path, capsys):
    azure = FakeAzure()
    azure.provision_errors = [deleted_resource_failure(azure)]
    azure.fail["keyvault recover"] = (1, "AuthorizationFailed: recovery permission denied")
    assert run(azure, tmp_path, ARGS + ["--recover-soft-deleted"]) == 1
    assert "AuthorizationFailed" in capsys.readouterr().err
    assert not azure.recovered_ids
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"


def test_recovery_must_be_verified_before_reprovisioning(tmp_path, capsys):
    azure = FakeAzure()
    azure.provision_errors = [deleted_resource_failure(azure)]
    original = azure.az

    def missing(a):
        return "" if a[:2] == ["resource", "show"] else original(a)

    azure.az = missing
    assert run(azure, tmp_path, ARGS + ["--recover-soft-deleted"]) == 1
    assert "Recovery could not be verified" in capsys.readouterr().err
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == 1


@pytest.mark.parametrize("error", [
    "Deployment Error Details:\nInsufficientResourcesAvailable: Search capacity\nAuthorizationFailed: AI not authorized",
    "(x) Failed: Storage account: stggtest (1s)\nInsufficientResourcesAvailable: Search capacity",
])
def test_search_retry_does_not_hide_unrelated_deployment_failures(tmp_path, capsys, error):
    azure = FakeAzure()
    azure.provision_errors = [error]
    assert run(azure, tmp_path) == 1
    assert "failures besides Search capacity" in capsys.readouterr().err
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"
    assert not [c for c in azure.calls if c[:4] == ["az", "deployment", "operation", "group"]]


def test_every_azd_operation_is_bound_to_gg_dev_and_resumed_configuration_is_repaired(tmp_path, monkeypatch):
    azure = FakeAzure()
    monkeypatch.setenv("AZURE_ENV_NAME", "other-dev")
    monkeypatch.setenv("AZURE_SEARCH_LOCATION", "eastus")
    azure.fail["azd provision"] = (1, "QuotaExceeded")
    assert run(azure, tmp_path) == 1
    azure.env.pop("GOVERNEDGRAPH_RESOURCE_GROUP_NAME")
    azure.env.pop("ON_CALL_EMAIL")
    assert run(azure, tmp_path, ["--yes", "--non-interactive"]) == 0
    assert azure.env["GOVERNEDGRAPH_RESOURCE_GROUP_NAME"] == "rg-demo"
    for args, env in azure.process_envs:
        if args[0] != "azd":
            continue
        assert env["AZURE_ENV_NAME"] == "gg-dev"
        if args[1] in ("provision", "deploy") or args[1:3] in (["env", "get-values"], ["env", "set"]):
            assert args[args.index("--environment") + 1] == "gg-dev"
        if args[1] == "provision":
            assert env["AZURE_SEARCH_LOCATION"] == "swedencentral"
    proof = next(c for c in azure.calls if "scripts.run_dev_proof" in c)
    assert proof[proof.index("--environment") + 1] == "gg-dev"


@pytest.mark.parametrize("key,value", [
    ("AZURE_ENV_NAME", "other-dev"), ("AZURE_SUBSCRIPTION_ID", OPERATOR),
    ("AZURE_LOCATION", "eastus"), ("AZURE_RESOURCE_GROUP", "rg-other"),
])
def test_resuming_refuses_a_mismatched_environment_before_any_provision(tmp_path, capsys, key, value):
    azure = FakeAzure()
    azure.fail["azd provision"] = (1, "QuotaExceeded")
    assert run(azure, tmp_path) == 1
    azure.env[key] = value
    count = len([c for c in azure.calls if c[:2] == ["azd", "provision"]])
    assert run(azure, tmp_path, ["--yes", "--non-interactive"]) == 1
    expected = "another subscription" if key == "AZURE_SUBSCRIPTION_ID" else "differs from the saved deployment scope"
    assert expected in capsys.readouterr().err
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == count


@pytest.mark.parametrize("bad_response", ["invalid-json", '{"unexpected":"object"}'])
def test_invalid_capacity_diagnostics_fail_explicitly_without_switching_region(tmp_path, capsys, bad_response):
    azure = FakeAzure()
    original = azure.az

    def malformed(a):
        if a[:4] == ["deployment", "operation", "group", "list"]:
            return bad_response
        return original(a)

    azure.az = malformed
    azure.fail["azd provision"] = (1, "InsufficientResourcesAvailable")
    assert run(azure, tmp_path) == 1
    assert "fallback could not finish" in capsys.readouterr().err
    assert azure.env["AZURE_SEARCH_LOCATION"] == "swedencentral"


def test_the_preferred_search_region_is_validated_and_cannot_silently_change_on_resume(tmp_path, capsys):
    azure = FakeAzure()
    assert run(azure, tmp_path, ARGS + ["--search-location", "eastus"]) == 1
    assert "approved EU regions" in capsys.readouterr().err
    assert run(azure, tmp_path, ARGS + ["--search-location", "francecentral"]) == 0
    assert azure.env["AZURE_SEARCH_LOCATION"] == "francecentral"
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--search-location", "westeurope"]) == 1
    assert "--search-location differs" in capsys.readouterr().err


def test_streamed_commands_keep_the_error_while_printing_progress():
    printed = []
    runner = deploy.Runner(echo=printed.append)
    with pytest.raises(CommandError) as error:
        runner.run([sys.executable, "-c",
                    "import sys; print('progress'); print('InsufficientResourcesAvailable', file=sys.stderr); sys.exit(1)"],
                   stream=True)
    assert "InsufficientResourcesAvailable" in error.value.stderr
    assert "progress" in printed and "InsufficientResourcesAvailable" in printed


def test_fresh_generates_one_persisted_seed_and_resume_never_changes_it(tmp_path, monkeypatch):
    azure = FakeAzure()
    azure.fail["azd provision"] = (1, "QuotaExceeded")
    assert run(azure, tmp_path) == 1
    before = json.loads((tmp_path / "state.json").read_text())
    seed = before["answers"]["naming_seed"]
    assert re.fullmatch(r"[a-f0-9]{32}", seed)
    assert azure.env["GOVERNEDGRAPH_NAMING_SEED"] == seed
    monkeypatch.setattr(deploy, "uuid4", lambda: pytest.fail("resume must not generate another seed"))
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    assert json.loads((tmp_path / "state.json").read_text())["answers"]["naming_seed"] == seed
    for args, env in azure.process_envs:
        if args[:2] == ["azd", "provision"]:
            assert env["GOVERNEDGRAPH_NAMING_SEED"] == seed
    for args in azure.calls:
        if "governedgraph.strategy_service.provision_auth" in args or "scripts/configure_contact_centre.py" in args:
            assert args[args.index("--naming-seed") + 1] == seed


def test_fresh_archives_previous_state_and_config_without_reusing_old_outputs(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    previous = json.loads((tmp_path / "state.json").read_text())
    (azure.environment_path / ".env").write_text("VOICE_DEMO_CLIENT_ID=previous-deployment\n")
    azure.env["UNRELATED_OLD_OUTPUT"] = "previous-deployment"
    assert run(azure, tmp_path, ARGS + ["--mode", "fresh", "--resource-group", "rg-fresh"]) == 0
    current = json.loads((tmp_path / "state.json").read_text())
    seed = current["answers"]["naming_seed"]
    assert seed != previous["answers"]["naming_seed"]
    assert current["answers"]["resource_group"] == "rg-fresh"
    archive = tmp_path / "deploy-history" / seed
    assert json.loads((archive / "state.json").read_text()) == previous
    assert (archive / "azd-environment" / ".env").read_text() == "VOICE_DEMO_CLIENT_ID=previous-deployment\n"
    assert "UNRELATED_OLD_OUTPUT" not in azure.env
    assert azure.env["GOVERNEDGRAPH_NAMING_SEED"] == seed
    assert not [c for c in azure.calls if "delete" in c or "purge" in c or "recover" in c]


def test_fresh_does_not_inherit_exported_images_client_ids_or_keys_from_an_old_deployment(tmp_path, monkeypatch):
    for key in ("SERVICE_ORCHESTRATOR_IMAGE_NAME", "STRATEGY_API_CLIENT_ID", "VOICE_DEMO_CLIENT_ID", "MEMBER_TOKEN_PUBLIC_JWK"):
        monkeypatch.setenv(key, "old-deployment-value")
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    first = next(env for args, env in azure.process_envs if args[:2] == ["azd", "provision"])
    assert first["SERVICE_ORCHESTRATOR_IMAGE_NAME"] is None
    assert first["STRATEGY_API_CLIENT_ID"] is None and first["VOICE_DEMO_CLIENT_ID"] is None
    assert first["MEMBER_TOKEN_PUBLIC_JWK"] != "old-deployment-value"


def test_runner_removes_explicitly_unset_inherited_configuration(monkeypatch):
    monkeypatch.setenv("GOVERNEDGRAPH_NAMING_SEED", "old-deployment-value")
    runner = deploy.Runner(echo=lambda *_: None)
    output = runner.run([sys.executable, "-c", "import os; print('GOVERNEDGRAPH_NAMING_SEED' in os.environ)"],
                        env={"GOVERNEDGRAPH_NAMING_SEED": None})
    assert output.strip() == "False"


def test_writer_plans_and_creation_do_not_inherit_agent_settings_from_a_previous_deployment(tmp_path, monkeypatch):
    expected = {
        "AZURE_AI_AGENT_NAME": "gg-counsellor",
        "AZURE_AI_MODEL_DEPLOYMENT": "gpt-counsellor",
        "AZURE_AI_COMPACT_DEPLOYMENT": "gpt-compact",
        "AZURE_AI_FAIRNESS_AGENT_NAME": "gg-fairness-explainability",
    }
    for key in expected:
        monkeypatch.setenv(key, "old-deployment-value")
    monkeypatch.setenv("GOVERNEDGRAPH_MODE", "azure")
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    plans, creation = [], []
    for command, env in azure.process_envs:
        if "scripts/create_agents.py" not in command:
            continue
        assert {key: env[key] for key in expected} == expected
        (creation if "--apply" in command else plans).append(env)
    assert len(plans) == 2 and len(creation) == len(deploy.AGENT_ROLES)
    assert all(env["GOVERNEDGRAPH_MODE"] == "local" for env in plans)
    assert all(env["GOVERNEDGRAPH_MODE"] == "azure" for env in creation)


def test_agent_tools_use_selected_azd_configuration_instead_of_ambient_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT", "old-model")
    azure = FakeAzure()
    azure.env.update(AZURE_AI_MODEL_DEPLOYMENT="selected-model", AZURE_AI_COMPACT_DEPLOYMENT="selected-compact")
    deployer = deploy.Deployer(azure, deploy.Prompter(interactive=False), deploy.State(), state_path=tmp_path / "s.json")
    env = deployer.tool_env()
    assert env["AZURE_AI_MODEL_DEPLOYMENT"] == "selected-model"
    assert env["AZURE_AI_COMPACT_DEPLOYMENT"] == "selected-compact"
    assert env["AZURE_AI_AGENT_NAME"] == "gg-counsellor"


@pytest.mark.parametrize("plan,diagnostic", [
    ({}, "expected agent name"),
    ({"agent_name": "old-counsellor", "definition_sha256": "1" * 64}, "expected agent name"),
    ({"agent_name": "gg-counsellor"}, "valid SHA-256"),
    ({"agent_name": "gg-counsellor", "definition_sha256": None}, "valid SHA-256"),
    ({"agent_name": "gg-counsellor", "definition_sha256": int("1" * 64)}, "valid SHA-256"),
    ({"agent_name": "gg-counsellor", "definition_sha256": "not-a-digest"}, "valid SHA-256"),
])
def test_b8_invalid_definition_plans_fail_closed_with_the_actual_contract_error(tmp_path, plan, diagnostic):
    class InvalidPlanRunner:
        def run(self, *args, **kwargs):
            return json.dumps(plan)

    deployer = deploy.Deployer(InvalidPlanRunner(), deploy.Prompter(interactive=False),
                              deploy.State(), state_path=tmp_path / "s.json")
    with pytest.raises(DeployError, match=diagnostic):
        deployer.current_writer_digests()


def test_fresh_defaults_to_a_new_group_and_warns_that_old_resources_remain(tmp_path, capsys):
    azure = FakeAzure()
    argv = [value for index, value in enumerate(ARGS) if index not in (ARGS.index("--resource-group"),
                                                                     ARGS.index("--resource-group") + 1)]
    assert run(azure, tmp_path, argv + ["--mode", "fresh"]) == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["answers"]["resource_group"] == "rg-gg-dev-" + state["answers"]["naming_seed"][:8]
    assert "may still incur costs" in capsys.readouterr().out


def test_declining_fresh_preserves_previous_state_and_local_environment(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    previous = (tmp_path / "state.json").read_bytes()
    marker = azure.environment_path / ".env"
    marker.write_text("unchanged")
    prompter = deploy.Prompter(interactive=True, ask=Answers("n"), echo=lambda *_: None)
    argv = [arg for arg in ARGS if arg != "--yes"]
    assert run(azure, tmp_path, argv + ["--mode", "fresh"], prompter=prompter) == 0
    assert (tmp_path / "state.json").read_bytes() == previous and marker.read_text() == "unchanged"


def test_interactive_mode_choice_starts_fresh_after_an_existing_deployment(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    previous = azure.env["GOVERNEDGRAPH_NAMING_SEED"]
    prompter = deploy.Prompter(interactive=True, ask=Answers("fresh"), echo=lambda *_: None)
    assert run(azure, tmp_path, ARGS + ["--resource-group", "rg-fresh"], prompter=prompter) == 0
    assert azure.env["GOVERNEDGRAPH_NAMING_SEED"] != previous


def test_redeploy_reruns_steps_but_keeps_seed_group_and_existing_key(tmp_path, monkeypatch):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    seed, key = azure.env["GOVERNEDGRAPH_NAMING_SEED"], azure.env["MEMBER_TOKEN_PUBLIC_JWK"]
    azure.rg_exists = "true"
    count = len([c for c in azure.calls if c[:2] == ["azd", "provision"]])
    monkeypatch.setattr(deploy, "uuid4", lambda: pytest.fail("redeploy must preserve the seed"))
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "redeploy"]) == 0
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == count + 5
    assert azure.env["GOVERNEDGRAPH_NAMING_SEED"] == seed
    assert azure.env["GOVERNEDGRAPH_RESOURCE_GROUP_NAME"] == "rg-demo"
    assert azure.env["MEMBER_TOKEN_PUBLIC_JWK"] == key


@pytest.mark.parametrize("mode", ["resume", "redeploy"])
def test_legacy_state_keeps_unseeded_names_on_resume_or_redeploy(tmp_path, monkeypatch, mode):
    state = deploy.State(answers=deploy.asdict(deploy.Answers(
        subscription_id=SUB, resource_group="rg-demo", on_call_email="ops@example.com",
        search_location="swedencentral")))
    state.answers.pop("naming_seed")
    state.save(tmp_path / "state.json")
    monkeypatch.setattr(deploy, "uuid4", lambda: pytest.fail("legacy deployment names must not change"))
    azure = FakeAzure()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", mode]) == 0
    assert json.loads((tmp_path / "state.json").read_text())["answers"]["naming_seed"] == ""
    assert azure.env["GOVERNEDGRAPH_NAMING_SEED"] == ""
    assert all(env["GOVERNEDGRAPH_NAMING_SEED"] == "" for args, env in azure.process_envs
               if args[:2] == ["azd", "provision"])


def test_fresh_refuses_an_existing_group_even_with_ignore_warnings(tmp_path, capsys):
    azure = FakeAzure()
    azure.rg_exists = "true"
    assert run(azure, tmp_path, ARGS + ["--mode", "fresh", "--ignore-warnings"]) == 1
    assert "requires a new resource group" in capsys.readouterr().err
    assert not [c for c in azure.calls if c[:2] == ["azd", "provision"]]
    assert run(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive", "--ignore-warnings"]) == 1
    assert not [c for c in azure.calls if c[:2] == ["azd", "provision"]]


def test_soft_deleted_recovery_is_never_implicit_or_persisted(tmp_path, capsys):
    azure = FakeAzure()
    azure.provision_errors = [deleted_resource_failure(azure)]
    assert run(azure, tmp_path) == 1
    assert "--recover-soft-deleted" in capsys.readouterr().err
    assert not [c for c in azure.calls if "recover" in c or "list-deleted" in c]
    assert "recover_soft_deleted" not in json.loads((tmp_path / "state.json").read_text())
    azure.provision_errors = [deleted_resource_failure(azure)]
    assert run(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive", "--recover-soft-deleted"]) == 0
    assert len(azure.recovered_ids) == 3


@pytest.mark.parametrize("mode", ["resume", "redeploy"])
def test_existing_modes_need_saved_state_and_do_not_create_it(tmp_path, capsys, mode):
    azure = FakeAzure()
    assert run(azure, tmp_path, ARGS + ["--mode", mode]) == 1
    assert "needs saved deployment state" in capsys.readouterr().err
    assert azure.calls == [] and not (tmp_path / "state.json").exists()


def test_fresh_plan_does_not_change_seed_state_or_environment(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    before, calls = (tmp_path / "state.json").read_bytes(), list(azure.calls)
    assert run(azure, tmp_path, ["--mode", "fresh", "--plan"]) == 0
    assert (tmp_path / "state.json").read_bytes() == before and azure.calls == calls


def test_naming_seed_mismatch_stops_before_provisioning_existing_resources(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["azd provision"] = (1, "QuotaExceeded")
    assert run(azure, tmp_path) == 1
    azure.env["GOVERNEDGRAPH_NAMING_SEED"] = "f" * 32
    count = len([c for c in azure.calls if c[:2] == ["azd", "provision"]])
    assert run(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 1
    assert "GOVERNEDGRAPH_NAMING_SEED differs" in capsys.readouterr().err
    assert len([c for c in azure.calls if c[:2] == ["azd", "provision"]]) == count


def test_resume_finishes_an_interrupted_local_environment_archive_without_new_names(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    (azure.environment_path / ".env").write_text("old config")
    azure.fail["cognitiveservices usage"] = (1, "not available")
    assert run(azure, tmp_path, ARGS + ["--mode", "fresh", "--resource-group", "rg-fresh"]) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    seed = state["answers"]["naming_seed"]
    archive = tmp_path / "deploy-history" / seed / "azd-environment"
    azure.environment_path.rename(archive)  # interrupted after rename but before the state checkpoint
    assert run(azure, tmp_path, ["--mode", "resume", "--yes", "--non-interactive"]) == 0
    assert azure.env["GOVERNEDGRAPH_NAMING_SEED"] == seed
    assert (archive / ".env").read_text() == "old config"


def test_the_runner_prints_commands_but_the_script_never_handles_secrets(monkeypatch):
    source = open(deploy.__file__).read()
    assert "client_secret" not in source and "get-access-token" not in source and "--secret" not in source
    printed = []
    runner = deploy.Runner(echo=printed.append)
    runner.run([sys.executable, "-c", "print(1)"])
    assert printed and printed[0].startswith("$ ")
    with pytest.raises(CommandError):
        runner.run([sys.executable, "-c", "import sys; sys.exit(3)"], quiet=True)


def provision_envs(azure):
    return [env for args, env in azure.process_envs if args[:2] == ["azd", "provision"]]


def test_b4_b5_b6_learning_runs_from_the_main_deployment_and_schedule_follows_acceptance(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["learning_receipts"]["p2-cycle"] == "cycle-synthetic"
    assert state["learning_receipts"]["fairness-agent"] == "job-gg-p2-fairness-proof1"
    assert state["learning_receipts"]["schedule"] == "enabled"
    assert re.fullmatch(r"crmlsynthetic\.azurecr\.io/governedgraph/p2@sha256:[0-9a-f]{64}", azure.env["P2_CONTAINER_IMAGE"])
    assert azure.env["DEV_FIXTURES_IMAGE"].startswith("crmlsynthetic.azurecr.io/governedgraph/dev-fixtures@sha256:")
    assert azure.env["DEPLOY_DEV_FIXTURES"] == "true" and azure.env["P2_SCHEDULE_ENABLED"] == "true"
    assert azure.env["FAIRNESS_MANAGED_IDENTITY_CLIENT_ID"] == azure.env["FAIRNESS_CLIENT_ID"]
    first, second = provision_envs(azure)[-2:]
    assert first["P2_SCHEDULE_ENABLED"] == "false" and second["P2_SCHEDULE_ENABLED"] == "true"
    flat = [" ".join(c) for c in azure.calls]
    order = [next(i for i, c in enumerate(flat) if needle in c) for needle in (
        "env set MEMBER_TOKEN_PUBLIC_JWK", "--target p2", "start -g rg-demo -n job-gg-dev-learning-fixtures",
        "run_p2_container_jobs", "env set P2_SCHEDULE_ENABLED true", "run_dev_proof channel")]
    assert order == sorted(order)
    p2 = next(c for c in azure.calls if "run_p2_container_jobs" in " ".join(c))
    assert "--fairness-agent-proof" in p2 and p2[p2.index("--environment") + 1] == "gg-dev"


def test_b5_b6_failed_learning_acceptance_never_enables_the_schedule_or_completes(tmp_path, capsys):
    azure = FakeAzure()
    azure.p2_output = ['{"cycle_id": "cycle-synthetic", "stage": "audit"}']
    assert run(azure, tmp_path) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert "learning" not in state["completed"] and "verify" not in state["completed"]
    assert azure.env["P2_SCHEDULE_ENABLED"] == "false"
    assert "P2 cycle did not report completion" in capsys.readouterr().err
    azure.p2_output = ['{"cycle_id": "cycle-synthetic", "pipeline": "Completed"}']
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    assert not any(c[:2] == ["azd", "deploy"] or "create_agents" in " ".join(c) for c in azure.calls)
    assert azure.env["P2_SCHEDULE_ENABLED"] == "true"


def test_b6_b9_the_fairness_agent_proof_is_required_before_recurrence(tmp_path, capsys):
    azure = FakeAzure()
    azure.fairness_output = []
    assert run(azure, tmp_path) == 1
    assert azure.env["P2_SCHEDULE_ENABLED"] == "false"
    assert "fairness-agent proof did not report success" in capsys.readouterr().err
    assert not json.loads((tmp_path / "state.json").read_text())["learning_receipts"]


def test_b3_learning_images_must_resolve_to_immutable_digests(tmp_path, capsys):
    azure = FakeAzure()
    azure.voice_image_digest = "latest"
    assert run(azure, tmp_path) == 1
    assert "immutable image digest" in capsys.readouterr().err
    assert "P2_CONTAINER_IMAGE" not in azure.env and not any("run_p2_container_jobs" in " ".join(c) for c in azure.calls)


def test_learning_failure_in_a_fixture_job_stops_before_the_cycle(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["containerapp job start"] = (1, "synthetic fixture failure")
    assert run(azure, tmp_path) == 1
    assert not any("run_p2_container_jobs" in " ".join(c) for c in azure.calls)


def test_legacy_deployments_without_learning_receipts_run_the_learning_plane_on_resume(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    path = tmp_path / "state.json"
    state = json.loads(path.read_text())
    state.pop("learning_receipts")
    state["completed"].remove("learning")
    path.write_text(json.dumps(state))
    azure.calls.clear()
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume"]) == 0
    assert any("run_p2_container_jobs" in " ".join(c) for c in azure.calls)
    assert not any(c[:2] == ["azd", "deploy"] for c in azure.calls)


def test_b8_the_release_approval_binds_only_the_new_deployments_immutable_writer_versions(tmp_path):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    assert len(azure.approvals) == 1
    record = azure.approvals[0]
    assert set(record) == {"tier", "approved", "decision_ref", "evidence_ref", "project_endpoint", "writers"}
    assert record["project_endpoint"] == azure.env["AZURE_AI_PROJECT_ENDPOINT"]
    assert record["writers"] == {
        "gg-counsellor": {"version": azure.env["AZURE_AI_AGENT_VERSION"], "definition_sha256": FAKE_DIGESTS["gg-counsellor"]},
        "gg-counsellor-voice-compact": {"version": azure.env["AZURE_AI_VOICE_COMPACT_VERSION"],
                                         "definition_sha256": FAKE_DIGESTS["gg-counsellor-voice-compact"]},
    }
    assert json.loads(azure.env["GOVERNEDGRAPH_WRITER_APPROVAL_JSON"]) == record
    assert not (tmp_path / "writer-approval.json").exists()
    assert not any(c[:2] == ["azd", "provision"] and "--file" in c for c in azure.calls)


def test_b8_owner_authorized_sample_release_is_consumed_without_per_deployment_approval(tmp_path, monkeypatch):
    release = copy.deepcopy(APPROVED_RELEASE)
    release["decision_ref"] = "HR-SYNTHETIC"
    monkeypatch.setattr(deploy.Deployer, "release_approval", lambda self: copy.deepcopy(release))
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    assert len(azure.approvals) == 1
    assert azure.approvals[0]["decision_ref"] == release["decision_ref"]
    assert azure.approvals[0]["writers"]["gg-counsellor"]["definition_sha256"] == FAKE_DIGESTS["gg-counsellor"]
    assert azure.approvals[0]["project_endpoint"] == azure.env["AZURE_AI_PROJECT_ENDPOINT"]
    assert azure.approvals[0]["writers"]["gg-counsellor"]["version"] == azure.env["AZURE_AI_AGENT_VERSION"]


def test_b8_unapproved_release_stops_before_any_azure_change_and_names_the_exact_digests(tmp_path, monkeypatch, capsys):
    azure = FakeAzure()
    monkeypatch.setattr(deploy.Deployer, "release_approval", lambda self: {**APPROVED_RELEASE, "approved": False})
    assert run(azure, tmp_path) == 1
    error = capsys.readouterr().err
    assert "no human Tier-2 approval" in error and FAKE_DIGESTS["gg-counsellor"] in error and "GB-" in error
    assert not any(c[0] in ("az", "azd") for c in azure.calls) and not azure.env
    assert "sample" not in json.loads((tmp_path / "state.json").read_text())["completed"]


def test_b8_an_approval_for_other_definitions_is_stale_and_cannot_release_new_writers(tmp_path, monkeypatch, capsys):
    azure = FakeAzure()
    stale = copy.deepcopy(APPROVED_RELEASE)
    stale["writers"]["gg-counsellor"]["definition_sha256"] = "9" * 64
    monkeypatch.setattr(deploy.Deployer, "release_approval", lambda self: stale)
    assert run(azure, tmp_path) == 1
    assert "does not cover the current writer definitions" in capsys.readouterr().err
    assert not azure.env


def test_b8_revoking_the_release_clears_the_gateway_approval_on_an_existing_deployment(tmp_path, monkeypatch, capsys):
    azure = FakeAzure()
    assert run(azure, tmp_path) == 0
    assert azure.env["GOVERNEDGRAPH_WRITER_APPROVAL_JSON"]
    monkeypatch.setattr(deploy.Deployer, "release_approval", lambda self: {**APPROVED_RELEASE, "approved": False})
    assert run(azure, tmp_path, ["--yes", "--non-interactive", "--mode", "resume", "--only", "agents"]) == 0
    assert azure.env["GOVERNEDGRAPH_WRITER_APPROVAL_JSON"] == ""
    assert "Generated answers stay suspended" in capsys.readouterr().out


def test_b8_a_failed_live_definition_check_clears_approval_and_never_continues(tmp_path, capsys):
    azure = FakeAzure()
    azure.fail["scripts.writer_approval"] = (1, "live immutable definition differs")
    assert run(azure, tmp_path) == 1
    assert azure.env.get("GOVERNEDGRAPH_WRITER_APPROVAL_JSON", "") == ""
    assert "remain suspended" in capsys.readouterr().err
    assert "agents" not in json.loads((tmp_path / "state.json").read_text())["completed"]


def sample_with_approval(tmp_path, value):
    import dataclasses
    path = tmp_path / "release-approval.json"
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return dataclasses.replace(deploy.SampleDefinition.load(deploy.DEFAULT_SAMPLE_DEFINITION), release_approval=path)


@pytest.mark.parametrize("change", [
    lambda r: r.update(age_band="synthetic"),
    lambda r: r.pop("evidence_ref"),
    lambda r: r.update(tier="tier1"),
    lambda r: r.update(approved="true"),
    lambda r: r.update(approved=1),
    lambda r: r.update(decision_ref="automatic"),
    lambda r: r.update(evidence_ref=" "),
    lambda r: r.update(evidence_ref="x" * 257),
    lambda r: r["writers"].pop("gg-counsellor"),
    lambda r: r["writers"].update({"gg-intent-agent": r["writers"]["gg-counsellor"]}),
    lambda r: r["writers"]["gg-counsellor"].update(version="14"),
    lambda r: r["writers"]["gg-counsellor"].update(definition_sha256="short"),
])
def test_b7_b8_release_approval_is_closed_and_has_no_numeric_version_exception(tmp_path, monkeypatch, change):
    record = copy.deepcopy(APPROVED_RELEASE)
    change(record)
    monkeypatch.setattr(deploy.Deployer, "sample", property(lambda self: sample_with_approval(tmp_path, record)))
    deployer = deploy.Deployer(FakeAzure(), deploy.Prompter(interactive=False), deploy.State(), state_path=tmp_path / "s.json")
    with pytest.raises(DeployError):
        ORIGINAL_RELEASE_APPROVAL(deployer)


def test_b7_b8_duplicate_approval_fields_and_malformed_files_fail_closed(tmp_path, monkeypatch):
    for text in ('{"approved":false,"approved":true}', "{", "[]"):
        monkeypatch.setattr(deploy.Deployer, "sample", property(lambda self, text=text: sample_with_approval(tmp_path, text)))
        deployer = deploy.Deployer(FakeAzure(), deploy.Prompter(interactive=False), deploy.State(), state_path=tmp_path / "s.json")
        with pytest.raises(DeployError):
            ORIGINAL_RELEASE_APPROVAL(deployer)


def test_b8_the_sample_authorization_covers_only_its_exact_shipped_writer_definitions():
    from scripts.create_agents import provision

    sample = deploy.SampleDefinition.load(deploy.DEFAULT_SAMPLE_DEFINITION)
    shipped = json.loads(sample.release_approval.read_text())
    assert set(shipped) == {"tier", "approved", "decision_ref", "evidence_ref", "writers"}
    assert shipped["approved"] is True
    assert shipped["decision_ref"] == "HR-PENSION-WTP-WELCOME-2026-11-04"
    assert shipped["evidence_ref"] == "docs/evaluation.md#welcoming-letter-help-release"
    for role, name in deploy.WRITERS.items():
        current = provision(None, apply=False, role=role)
        assert current["agent_name"] == name
        assert shipped["writers"][name] == {"definition_sha256": current["definition_sha256"]}
    assert all(set(writer) == {"definition_sha256"} for writer in shipped["writers"].values())
