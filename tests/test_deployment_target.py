"""Synthetic, read-only CLI doubles for deployment selection and scope persistence."""
import json
import re
import stat
from types import SimpleNamespace
from uuid import UUID

import pytest

from scripts import delivery, deploy, deployment_target as target


@pytest.fixture(autouse=True)
def no_real_schedule_pause(monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(deploy, "paused", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr("scripts.apps_only.paused", lambda *_a, **_k: nullcontext())

SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"
SEED = "00000000-0000-0000-0000-000000000002"
OTHER_SEED = "00000000-0000-0000-0000-000000000004"
GROUP = "rg-gg-dev-existing"


def group(name=GROUP, **changes):
    return {
        "name": name, "location": "swedencentral", "state": "Succeeded", "managedBy": None,
        "tags": {"workload": "gg-journey-graph", "azd-env-name": "gg-dev", "data": "synthetic"},
        **changes,
    }


def record(seed=SEED, **changes):
    return {"group": GROUP, "seed": seed, "environment": "gg-dev", "state": "Succeeded", **changes}


class Runner:
    def __init__(self, *, groups=None, records=None, exists=False):
        self.groups = [group()] if groups is None else groups
        self.records = [record()] if records is None else records
        self.exists = exists
        self.calls = []

    def run(self, args):
        self.calls.append(args)
        if args[:3] == ["az", "group", "list"]:
            return json.dumps(self.groups)
        if args[:3] == ["az", "group", "exists"]:
            return json.dumps(self.exists)
        if args[:4] == ["az", "deployment", "sub", "list"]:
            return json.dumps(self.records)
        pytest.fail("selection must use only explicit read-only Azure commands")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    path = tmp_path / ".azure" / "private-delivery.json"
    path.parent.mkdir()
    raw = {
        "subscription": SUBSCRIPTION, "operator_id": "00000000-0000-0000-0000-000000000003",
        "on_call_email": "synthetic@example.invalid", "composition": "examples/pension_wtp/deployment.json",
    }
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(target.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    return SimpleNamespace(path=path, raw=raw, root=tmp_path)


def save_environment(workspace, *, seed=SEED, name=GROUP):
    path = workspace.root / ".azure" / "gg-dev" / ".env"
    path.parent.mkdir(parents=True)
    path.write_text(
        f'AZURE_SUBSCRIPTION_ID="{SUBSCRIPTION}"\nAZURE_RESOURCE_GROUP="{name}"\n'
        f'GOVERNEDGRAPH_NAMING_SEED="{seed}"\n'
    )
    return path


def test_discovery_excludes_unrelated_non_eu_and_service_managed_groups(workspace):
    rows = [
        group("rg-b"), group("rg-a"),
        group("foreign", tags={"workload": "other", "azd-env-name": "gg-dev", "data": "synthetic"}),
        group("production", tags={"workload": "gg-journey-graph", "azd-env-name": "gg-prod", "data": "synthetic"}),
        group("real-data", tags={"workload": "gg-journey-graph", "azd-env-name": "gg-dev", "data": "real"}),
        group("wrong-region", location="westus"),
        group("managed-child", managedBy="/synthetic/managedEnvironments/existing"),
    ]
    runner = Runner(groups=rows)
    assert [row["name"] for row in target.discover(runner, target.new_target(workspace.raw))] == ["rg-a", "rg-b"]
    assert runner.calls[0][runner.calls[0].index("--subscription") + 1] == SUBSCRIPTION


@pytest.mark.parametrize("rows", [{}, [None], [{"name": "unknown"}], [group(), group()]])
def test_malformed_or_duplicate_discovery_is_not_treated_as_no_deployments(workspace, rows):
    with pytest.raises(delivery.DeliveryError, match="metadata|duplicate"):
        target.discover(Runner(groups=rows), target.new_target(workspace.raw))


def test_interactive_default_reuses_saved_scope_without_archiving_it(workspace, monkeypatch):
    env_file = save_environment(workspace)
    original = env_file.read_text()
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "")
    runner = Runner()
    selected = target.select_target(runner, workspace.path, workspace.raw, target.new_target(workspace.raw))
    assert prompts == ["Deployment [1]: "]
    assert selected.resource_group == GROUP and selected.naming_seed == SEED
    assert env_file.read_text() == original
    assert not (workspace.root / ".azure" / "archived").exists()
    saved = json.loads(workspace.path.read_text())
    assert saved["resource_group"] == GROUP and saved["naming_seed"] == SEED


def test_explicit_reuse_recovers_the_original_seed_from_azure_without_local_scope(workspace, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("--reuse must not prompt"))
    runner = Runner()
    selected = target.select_target(runner, workspace.path, workspace.raw, target.new_target(workspace.raw),
                                    reuse=GROUP.upper())
    assert selected.resource_group == GROUP and selected.naming_seed == SEED
    assert runner.calls[-1][:4] == ["az", "deployment", "sub", "list"]
    assert "seed:properties.parameters.namingSeed.value" in runner.calls[-1][runner.calls[-1].index("--query") + 1]


def test_new_deployment_generates_and_retains_scope_preserving_the_old_environment(workspace, monkeypatch):
    env_file = save_environment(workspace)
    original_env = env_file.read_text()
    workspace.raw.update(resource_group=GROUP, naming_seed=SEED)
    workspace.path.write_text(json.dumps(workspace.raw))
    candidate = target.new_target(workspace.raw)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("--new must not prompt"))
    runner = Runner()
    selected = target.select_target(runner, workspace.path, workspace.raw, candidate, create_new=True)
    assert selected == candidate
    assert UUID(selected.naming_seed).version == 4 and selected.naming_seed != SEED
    assert re.fullmatch(r"rg-gg-dev-[0-9a-f]{12}", selected.resource_group)
    assert selected.resource_group == delivery.resource_group_name(selected.naming_seed)
    saved = json.loads(workspace.path.read_text())
    assert saved["naming_seed"] == selected.naming_seed and saved["resource_group"] == selected.resource_group
    assert saved["operator_id"] == workspace.raw["operator_id"]
    assert stat.S_IMODE(workspace.path.stat().st_mode) == 0o600
    archived = list((workspace.root / ".azure" / "archived").glob("*/gg-dev/.env"))
    assert len(archived) == 1 and archived[0].read_text() == original_env
    assert not env_file.exists()
    assert [call[:3] for call in runner.calls] == [["az", "group", "list"], ["az", "group", "exists"]]
    assert target.new_target(workspace.raw).naming_seed != selected.naming_seed


def test_interactive_new_choice_and_invalid_answers(workspace, monkeypatch, capsys):
    answers = iter(["invalid", "9", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    candidate = target.new_target(workspace.raw)
    assert target.select_target(Runner(), workspace.path, workspace.raw, candidate) == candidate
    assert "Enter a number from 1 to 2" in capsys.readouterr().err


def test_no_existing_groups_offers_only_a_new_deployment(workspace, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "" if prompt == "Deployment [1]: " else pytest.fail(prompt))
    candidate = target.new_target(workspace.raw)
    assert target.select_target(Runner(groups=[]), workspace.path, workspace.raw, candidate) == candidate
    assert "1) New deployment" in capsys.readouterr().out


def test_noninteractive_selection_requires_an_explicit_flag_before_cloud_reads(workspace, monkeypatch):
    monkeypatch.setattr(target.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    runner = Runner()
    before = workspace.path.read_text()
    with pytest.raises(delivery.DeliveryError, match="--reuse RESOURCE_GROUP or --new"):
        target.select_target(runner, workspace.path, workspace.raw, target.new_target(workspace.raw))
    assert runner.calls == [] and workspace.path.read_text() == before


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt()])
def test_cancelled_selection_does_not_change_private_inputs_or_environment(workspace, monkeypatch, failure):
    env_file = save_environment(workspace)
    before_inputs, before_env = workspace.path.read_text(), env_file.read_text()

    def interrupted(_):
        raise failure

    monkeypatch.setattr("builtins.input", interrupted)
    expected = delivery.DeliveryError if isinstance(failure, EOFError) else KeyboardInterrupt
    with pytest.raises(expected):
        target.select_target(Runner(), workspace.path, workspace.raw, target.new_target(workspace.raw))
    assert workspace.path.read_text() == before_inputs and env_file.read_text() == before_env
    assert not (workspace.root / ".azure" / "archived").exists()


@pytest.mark.parametrize("name", ["unrelated", "missing"])
def test_reuse_never_accepts_an_undiscovered_group(workspace, name):
    before = workspace.path.read_text()
    with pytest.raises(delivery.DeliveryError, match="--reuse must name a discovered"):
        target.select_target(Runner(), workspace.path, workspace.raw, target.new_target(workspace.raw), reuse=name)
    assert workspace.path.read_text() == before


@pytest.mark.parametrize("exists", [True, "false", None])
def test_new_group_collision_or_unknown_existence_does_not_switch_scope(workspace, exists):
    env_file = save_environment(workspace)
    before_inputs, before_env = workspace.path.read_text(), env_file.read_text()
    with pytest.raises(delivery.DeliveryError, match="cannot confirm"):
        target.select_target(Runner(exists=exists), workspace.path, workspace.raw,
                             target.new_target(workspace.raw), create_new=True)
    assert workspace.path.read_text() == before_inputs and env_file.read_text() == before_env


@pytest.mark.parametrize("records", [
    [],
    [record(environment="gg-prod")],
    [record(), record(seed=OTHER_SEED)],
])
def test_missing_or_conflicting_original_seed_never_gets_a_new_seed(workspace, records):
    before = workspace.path.read_text()
    with pytest.raises(delivery.DeliveryError, match="original naming seed is missing or conflicting"):
        target.select_target(Runner(records=records), workspace.path, workspace.raw,
                             target.new_target(workspace.raw), reuse=GROUP)
    assert workspace.path.read_text() == before


def test_conflicting_local_and_cloud_seed_is_rejected(workspace):
    env_file = save_environment(workspace, seed=OTHER_SEED)
    before = env_file.read_text()
    with pytest.raises(delivery.DeliveryError, match="original naming seed is missing or conflicting"):
        target.select_target(Runner(), workspace.path, workspace.raw, target.new_target(workspace.raw), reuse=GROUP)
    assert env_file.read_text() == before


@pytest.mark.parametrize("seed", ["not-a-uuid", 42])
def test_invalid_seed_metadata_is_reported_without_switching_scope(workspace, seed):
    before = workspace.path.read_text()
    runner = Runner(records=[record(seed=seed)])
    with pytest.raises(delivery.DeliveryError, match="original naming seed must be a UUID"):
        target.select_target(runner, workspace.path, workspace.raw, target.new_target(workspace.raw), reuse=GROUP)
    assert workspace.path.read_text() == before


@pytest.mark.parametrize("literal", [
    "aabbccdd00112233445566778899aabb",
    "AABBCCDD00112233445566778899AABB",
    "AABBCCDD-0011-2233-4455-66778899AABB",
])
def test_seed_validation_never_normalizes_the_literal_naming_input(literal):
    assert target.original_seed(literal) == literal


def test_successful_original_seed_wins_over_failed_normalized_redeployment(workspace, capsys):
    literal = "aabbccdd00112233445566778899aabb"
    normalized = str(UUID(literal))
    save_environment(workspace, seed=normalized)
    workspace.raw.update(resource_group=GROUP, naming_seed=normalized)
    workspace.path.write_text(json.dumps(workspace.raw))
    runner = Runner(records=[record(seed=normalized, state="Failed"), record(seed=literal)])
    selected = target.select_target(runner, workspace.path, workspace.raw,
                                    target.new_target(workspace.raw), reuse=GROUP)
    assert selected.naming_seed == literal and len(selected.naming_seed) == 32
    assert json.loads(workspace.path.read_text())["naming_seed"] == literal
    assert "Restoring the exact naming seed" in capsys.readouterr().err


def test_uuid_equivalent_but_differently_spelled_successful_seeds_are_ambiguous(workspace):
    literal = SEED.replace("-", "")
    before = workspace.path.read_text()
    with pytest.raises(delivery.DeliveryError, match="original naming seed is missing or conflicting"):
        target.select_target(Runner(records=[record(seed=literal), record()]), workspace.path, workspace.raw,
                             target.new_target(workspace.raw), reuse=GROUP)
    assert workspace.path.read_text() == before


def test_exact_resource_group_spelling_is_recovered_from_successful_parameters(workspace):
    original_name = GROUP.upper()
    selected = target.select_target(Runner(records=[record(group=original_name)]), workspace.path, workspace.raw,
                                    target.new_target(workspace.raw), reuse=GROUP)
    assert selected.resource_group == original_name


@pytest.mark.parametrize("state", ["Failed", "Running"])
def test_partial_first_deployment_can_resume_only_its_exact_saved_seed(workspace, state):
    literal = SEED.replace("-", "")
    save_environment(workspace, seed=literal)
    selected = target.select_target(Runner(records=[record(seed=literal, state=state)]),
                                    workspace.path, workspace.raw, target.new_target(workspace.raw), reuse=GROUP)
    assert selected.naming_seed == literal


def test_failed_seed_without_saved_scope_is_not_authoritative(workspace):
    with pytest.raises(delivery.DeliveryError, match="original naming seed is missing or conflicting"):
        target.select_target(Runner(records=[record(state="Failed")]), workspace.path, workspace.raw,
                             target.new_target(workspace.raw), reuse=GROUP)


def test_same_scope_requires_literal_seed_and_group_identity(workspace):
    literal = SEED.replace("-", "")
    inputs = delivery.Inputs.from_dict({**workspace.raw, "resource_group": GROUP, "naming_seed": literal})
    saved = {"AZURE_SUBSCRIPTION_ID": SUBSCRIPTION, "AZURE_RESOURCE_GROUP": GROUP,
             "GOVERNEDGRAPH_NAMING_SEED": literal}
    assert target.same_scope(saved, inputs)
    assert not target.same_scope({**saved, "GOVERNEDGRAPH_NAMING_SEED": SEED}, inputs)
    assert not target.same_scope({**saved, "AZURE_RESOURCE_GROUP": GROUP.upper()}, inputs)


def test_group_being_deleted_cannot_be_reused(workspace):
    before = workspace.path.read_text()
    with pytest.raises(delivery.DeliveryError, match="not ready"):
        target.select_target(Runner(groups=[group(state="Deleting")]), workspace.path, workspace.raw,
                             target.new_target(workspace.raw), reuse=GROUP)
    assert workspace.path.read_text() == before


def test_archived_scope_can_recover_a_legacy_seed_missing_from_azure_history(workspace):
    old = save_environment(workspace)
    archive = workspace.root / ".azure" / "archived" / "synthetic-old" / "gg-dev"
    archive.parent.mkdir(parents=True)
    old.parent.rename(archive)
    selected = target.select_target(Runner(records=[]), workspace.path, workspace.raw,
                                    target.new_target(workspace.raw), reuse=GROUP)
    assert selected.naming_seed == SEED and (archive / ".env").is_file()


def test_persistence_failure_restores_the_original_environment(workspace, monkeypatch):
    env_file = save_environment(workspace)
    before_inputs, before_env = workspace.path.read_text(), env_file.read_text()

    def failed_replace(*_):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(target.os, "replace", failed_replace)
    with pytest.raises(OSError, match="synthetic write failure"):
        target.select_target(Runner(), workspace.path, workspace.raw,
                             target.new_target(workspace.raw), create_new=True)
    assert workspace.path.read_text() == before_inputs and env_file.read_text() == before_env
    assert not list(workspace.path.parent.glob(".delivery-*.json"))


@pytest.mark.parametrize("flags", [[], ["--new"], ["--reuse", GROUP]])
def test_plan_needs_no_seed_cloud_calls_prompt_or_persistence(workspace, monkeypatch, capsys, flags):
    before = workspace.path.read_text()
    monkeypatch.setattr(deploy.Composition, "load", lambda _: SimpleNamespace(name="pension_wtp"))
    monkeypatch.setattr(delivery.Runner, "run", lambda *_args, **_kwargs: pytest.fail("plan must not call Azure"))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("plan must not prompt"))
    assert deploy.main(["--inputs", str(workspace.path), "--plan", *flags]) == 0
    assert json.loads(capsys.readouterr().out)["sequence"] == list(deploy.SEQUENCE)
    assert workspace.path.read_text() == before


@pytest.mark.parametrize("flags,reuse,create_new", [(["--reuse", GROUP], GROUP, False), (["--new"], None, True)])
def test_main_routes_explicit_selection_flags_before_delivery(workspace, monkeypatch, capsys, flags, reuse, create_new):
    calls = []
    monkeypatch.setattr(deploy, "ROOT", workspace.root)
    selected = delivery.Inputs.from_dict({**workspace.raw, "resource_group": GROUP, "naming_seed": SEED})
    monkeypatch.setattr(deploy.Composition, "load", lambda _: SimpleNamespace(name="pension_wtp"))

    def select(_runner, path, raw, candidate, **kwargs):
        assert path == workspace.path and raw == workspace.raw
        assert candidate.naming_seed != SEED
        calls.append(("select", kwargs))
        return selected

    def deliver(_runner, inputs, _composition):
        assert inputs == selected
        calls.append(("deliver", {}))
        return {"readiness": "passed", "outputs": {
            "VOICE_DEMO_ORIGIN": "https://demo.example.invalid", "ADMIN_CONSOLE_URL": "https://admin.example.invalid"}}

    monkeypatch.setattr(deploy, "select_target", select)
    monkeypatch.setattr(deploy, "deploy", deliver)
    monkeypatch.setattr("scripts.apps_only.redeploy_images", lambda *_: None)
    assert deploy.main(["--inputs", str(workspace.path), *flags]) == 0
    assert calls == [("select", {"reuse": reuse, "create_new": create_new}), ("deliver", {})]
    assert "Demo URL: https://demo.example.invalid/demo/" in capsys.readouterr().out


def test_subscription_deployment_records_are_unique_per_target_but_stable_on_redeploy(workspace):
    names = []

    class DeploymentRunner:
        def run(self, args):
            names.append(args[args.index("--name") + 1])
            return '{"properties":{"provisioningState":"Succeeded","outputs":{}}}'

    for name in (GROUP, GROUP.upper(), "rg-gg-dev-other"):
        inputs = delivery.Inputs.from_dict({**workspace.raw, "resource_group": name, "naming_seed": SEED})
        delivery.provision(DeploymentRunner(), inputs, "infra/main.bicep", {})
    assert names[0] == names[1] and names[1] != names[2]
