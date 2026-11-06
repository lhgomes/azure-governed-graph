"""The teardown script: scoped clean-up, progress reporting and refusals, against a simulated Azure."""
import json
import re
from pathlib import Path

import pytest
from scripts import configure_admin_console, teardown
from scripts.deploy import CommandError, Prompter

SUB = "00000000-0000-4000-8000-0000000000aa"
SEED = "627df32699264204a391842dd9fa673c"
GROUP = "rg-gg-dev-627df326"
OTHER_GROUP = "rg-someone-else"
TENANT_CALLS = ("ad ",)


class Clock:
    def __init__(self, azure):
        self.now, self.azure = 0.0, azure

    def sleep(self, seconds):
        self.now += seconds
        self.azure.tick()

    def monotonic(self):
        return self.now


class FakeAzure:
    """Resources vanish two per poll once the group is deleting; soft-deleted records follow the group."""

    def __init__(self, *, resources=7, state="Succeeded", tag="gg-dev", record_delay=0, vault_protected=True,
                 entra_error=False, purge_error=False):
        types = ["Microsoft.ApiManagement/service", "Microsoft.CognitiveServices/accounts"] + ["Microsoft.App/containerApps"] * 5
        self.resources = [{"type": types[i % len(types)], "name": f"r{i}"} for i in range(resources)]
        if resources >= 2:
            self.resources[0:2] = [{"type": "Microsoft.ApiManagement/service", "name": "apim-gg-x"},
                                   {"type": "Microsoft.CognitiveServices/accounts", "name": "ais-gg-x"}]
        self.exists, self.state, self.tag = True, state, tag
        self.record_delay, self.vault_protected, self.entra_error, self.purge_error = record_delay, vault_protected, entra_error, purge_error
        self.deleted_ai, self.deleted_apim, self.deleted_vaults = [], [], []
        self.apps = {n: f"app-{i}" for i, n in enumerate(teardown.entra_names(SEED)["apps"])}
        self.apps["GovernedGraph gg-dev Voice Demo-other"] = "foreign-app"
        self.groups = {n: f"grp-{i}" for i, n in enumerate(teardown.entra_names(SEED)["groups"])}
        self.calls, self.gone_ticks = [], 0
        self.foreign_deleted = [{"name": "ais-foreign", "location": "swedencentral",
                                 "id": f"/subscriptions/{SUB}/locations/swedencentral/resourceGroups/{OTHER_GROUP}/deletedAccounts/ais-foreign"}]

    def tick(self):
        if self.state == "Deleting" and self.resources:
            del self.resources[:2]
            if not self.resources:
                self.exists = False
        elif not self.exists:
            self.gone_ticks += 1
        if not self.exists and self.gone_ticks >= self.record_delay and not self.deleted_ai and not self.purged_any():
            self._create_records()

    def purged_any(self):
        return any(c.startswith(("az cognitiveservices account purge", "az apim deletedservice purge")) for c in self.calls)

    def _create_records(self):
        self.deleted_ai = [{"name": "ais-gg-x", "location": "swedencentral",
                            "id": f"/subscriptions/{SUB}/locations/swedencentral/resourceGroups/{GROUP}/deletedAccounts/ais-gg-x"}]
        self.deleted_apim = [{"name": "apim-gg-x", "location": "swedencentral",
                              "serviceId": f"/subscriptions/{SUB}/resourceGroups/{GROUP}/providers/Microsoft.ApiManagement/service/apim-gg-x"}]
        self.deleted_vaults = [{"name": "kv-gg-x", "location": "swedencentral", "properties": {
            "vaultId": f"/subscriptions/{SUB}/resourceGroups/{GROUP}/providers/Microsoft.KeyVault/vaults/kv-gg-x",
            "purgeProtectionEnabled": self.vault_protected, "scheduledPurgeDate": "2027-02-01"}}]

    def run(self, args, *, stream=False, check=True, env=None, quiet=False):
        command = " ".join(args[1:])
        for token in ("--subscription", SUB, "--only-show-errors", "-o json"):
            command = command.replace(token, "")
        command = " ".join(command.split())
        self.calls.append("az " + command)
        assert args[0] == "az"
        if command.startswith("group show"):
            if not self.exists:
                raise CommandError(args, 3, f"(ResourceGroupNotFound) Resource group '{GROUP}' could not be found.")
            return json.dumps({"name": GROUP, "tags": {"azd-env-name": self.tag} if self.tag else {},
                               "properties": {"provisioningState": self.state}})
        if command.startswith("group delete"):
            self.state = "Deleting"
            return ""
        if command.startswith("resource list"):
            return json.dumps(self.resources)
        if command.startswith("cognitiveservices account list-deleted"):
            return json.dumps(self.deleted_ai + self.foreign_deleted)
        if command.startswith("apim deletedservice list"):
            return json.dumps(self.deleted_apim)
        if command.startswith("keyvault list-deleted"):
            return json.dumps(self.deleted_vaults)
        if command.startswith("cognitiveservices account purge"):
            if self.purge_error:
                raise CommandError(args, 1, "AuthorizationFailed: not allowed to purge")
            assert f"--resource-group {GROUP}" in command
            self.deleted_ai = [d for d in self.deleted_ai if d["name"] not in command]
            return ""
        if command.startswith("apim deletedservice purge"):
            self.deleted_apim = [d for d in self.deleted_apim if d["name"] not in command]
            return ""
        if command.startswith("keyvault purge"):
            self.deleted_vaults = [d for d in self.deleted_vaults if d["name"] not in command]
            return ""
        if command.startswith("ad "):
            if self.entra_error:
                raise CommandError(args, 1, "ERROR: Please run 'az login' to setup account.")
            if command.startswith("ad app list"):
                name = re.search(r"displayName eq '([^']+)'", command).group(1)
                return json.dumps([{"id": f"obj-{self.apps[name]}", "appId": self.apps[name]}] if name in self.apps else [])
            if command.startswith("ad group list"):
                name = re.search(r"displayName eq '([^']+)'", command).group(1)
                return json.dumps([{"id": self.groups[name]}] if name in self.groups else [])
            if command.startswith("ad app delete"):
                self.apps = {n: i for n, i in self.apps.items() if i not in command}
                return ""
            if command.startswith("ad group delete"):
                self.groups = {n: i for n, i in self.groups.items() if i not in command}
                return ""
        raise AssertionError(f"unexpected command: {command}")


@pytest.fixture
def workspace(tmp_path):
    state = tmp_path / "run" / "deploy-state.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"answers": {"subscription_id": SUB, "resource_group": GROUP, "naming_seed": SEED}}))
    environment = tmp_path / "azd"
    environment.mkdir()
    (environment / ".env").write_text("AZURE_RESOURCE_GROUP=\"" + GROUP + "\"\n")
    return tmp_path, state, environment


def go(workspace, azure, *flags, interactive=False, typed=""):
    _, state, environment = workspace
    clock = Clock(azure)
    prompter = Prompter(interactive=interactive, ask=lambda _: typed, echo=lambda *_: None)
    code = teardown.main([*flags, "--poll-seconds", "30", "--timeout-minutes", "60"], runner=azure, prompter=prompter,
                         state_path=state, environment_path=environment, sleep=clock.sleep, monotonic=clock.monotonic)
    return code


def mutating(azure):
    return [c for c in azure.calls if re.match(r"az (group delete|.* purge|ad (app|group) delete)", c)]


def test_plan_reports_counts_and_changes_nothing(workspace, capsys):
    azure = FakeAzure()
    assert go(workspace, azure, "--plan") == 0
    out = capsys.readouterr().out
    assert "7 resources" in out and "1 AI account(s), 1 API Management service(s)" in out
    assert "4 app registration(s) and 2 security group(s)" in out
    assert mutating(azure) == []
    assert workspace[1].exists()


def test_full_teardown_reports_progress_and_cleans_everything(workspace, capsys):
    azure = FakeAzure(record_delay=2)
    assert go(workspace, azure, "--yes") == 0
    out = capsys.readouterr().out
    assert "7 of 7 resources still to remove" in out and "1 of 7 resources still to remove" in out
    assert "waiting for 2 soft-deleted record(s)" in out
    assert "key vault kv-gg-x kept: purge protection until 2027-02-01" in out
    assert "6 object(s) deleted" in out and "app registration" not in out.split("Teardown finished")[-1]
    assert azure.deleted_ai == [] and azure.deleted_apim == [] and azure.deleted_vaults != []
    assert azure.apps == {"GovernedGraph gg-dev Voice Demo-other": "foreign-app"}, "only this seed's apps are deleted"
    assert azure.groups == {}
    assert azure.foreign_deleted, "another resource group's soft-deleted account is never purged"
    assert not any("kv-gg-x" in c for c in mutating(azure)), "purge-protected vaults are never purged"
    assert not workspace[1].exists()
    assert list((workspace[0] / "run" / "deploy-history" / SEED).glob("torn-down*/state.json"))
    assert list((workspace[0] / "run" / "deploy-history" / SEED).glob("torn-down*/azd-environment"))
    assert "fresh" in out


def test_a_delete_already_running_is_monitored_not_repeated(workspace):
    azure = FakeAzure(state="Deleting", resources=4)
    assert go(workspace, azure, "--yes") == 0
    assert not any(c.startswith("az group delete") for c in azure.calls)


def test_unprotected_vault_is_purged(workspace):
    azure = FakeAzure(vault_protected=False)
    assert go(workspace, azure, "--yes") == 0
    assert azure.deleted_vaults == []


def test_resource_group_without_the_environment_tag_is_refused(workspace, capsys):
    azure = FakeAzure(tag="production")
    assert go(workspace, azure, "--yes") == 1
    assert "refusing to touch it" in capsys.readouterr().err
    assert mutating(azure) == []


def test_unattended_runs_need_yes_and_typed_confirmation_must_match(workspace, capsys):
    azure = FakeAzure()
    assert go(workspace, azure) == 1
    assert "pass --yes" in capsys.readouterr().err
    assert go(workspace, azure, interactive=True, typed="wrong-name") == 0
    assert "Nothing was changed" in capsys.readouterr().out
    assert mutating(azure) == []
    assert go(workspace, azure, interactive=True, typed=GROUP) == 0
    assert mutating(azure)


def test_unreadable_entra_fails_the_run_and_keeps_local_state(workspace, capsys):
    azure = FakeAzure(entra_error=True)
    assert go(workspace, azure, "--yes") == 1
    out = capsys.readouterr().out
    assert "az login" in out and "left in place" in out
    assert workspace[1].exists()
    assert azure.deleted_ai == [] and not azure.exists


def test_purge_failures_are_reported_and_fail_the_run(workspace, capsys):
    azure = FakeAzure(purge_error=True)
    assert go(workspace, azure, "--yes") == 1
    assert "could not purge AI account ais-gg-x" in capsys.readouterr().out


def test_keep_flags_leave_entra_and_local_state(workspace):
    azure = FakeAzure()
    assert go(workspace, azure, "--yes", "--keep-entra", "--keep-local") == 0
    assert len(azure.apps) == 5 and workspace[1].exists()
    assert not any(c.startswith("az ad app delete") for c in azure.calls)


def test_a_group_that_never_finishes_deleting_times_out_with_a_rerun_hint(workspace, capsys):
    azure = FakeAzure(resources=400)
    assert go(workspace, azure, "--yes") == 1
    assert "re-run this command to keep monitoring" in capsys.readouterr().err


def test_rerun_after_the_group_is_gone_only_finishes_the_rest(workspace):
    azure = FakeAzure()
    azure.exists, azure.resources = False, []
    azure._create_records()
    assert go(workspace, azure, "--yes") == 0
    assert not any(c.startswith("az group delete") for c in azure.calls)
    assert azure.deleted_ai == [] and azure.groups == {}


def test_entra_names_match_what_the_deployment_registers():
    names = teardown.entra_names(SEED)
    assert names["apps"][3] == configure_admin_console.names("gg-dev", f"{SEED}")["app"]
    assert names["groups"] == [configure_admin_console.names("gg-dev", SEED)[k] for k in ("reader", "operator")]
    root = Path(__file__).resolve().parents[1]
    for source, fragment in (("scripts/configure_voice_demo.py", '"GovernedGraph gg-dev Voice Demo" + suffix'),
                             ("scripts/configure_contact_centre.py", 'f"GovernedGraph {environment} synthetic contact centre{suffix}"'),
                             ("src/governedgraph/strategy_service/provision_auth.py", '"gg-strategy-" + environment + suffix')):
        assert fragment in (root / source).read_text(), f"{source} renamed its Entra object; update scripts/teardown.py"
    assert teardown.entra_names("")["apps"][2] == "gg-strategy-gg-dev"


def test_missing_state_asks_for_explicit_scope(tmp_path, capsys):
    assert teardown.main(["--plan"], runner=FakeAzure(), prompter=Prompter(interactive=False),
                         state_path=tmp_path / "none.json", environment_path=tmp_path / "azd") == 1
    assert "--resource-group" in capsys.readouterr().err
