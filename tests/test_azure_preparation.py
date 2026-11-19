import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree

from governedgraph.strategy_service.snapshot import KeyVaultSigner

ROOT = Path(__file__).resolve().parents[1]


def test_b3_key_vault_verification_does_not_require_key_read(monkeypatch):
    credential = object()
    crypto = Mock()
    crypto.verify.return_value = SimpleNamespace(is_valid=True)
    identity = ModuleType("azure.identity")
    identity.DefaultAzureCredential = Mock(return_value=credential)
    crypto_module = ModuleType("azure.keyvault.keys.crypto")
    crypto_module.CryptographyClient = Mock(return_value=crypto)
    for name in ("azure", "azure.keyvault", "azure.keyvault.keys"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.keyvault.keys.crypto", crypto_module)
    crypto_module.SignatureAlgorithm = SimpleNamespace(rs256="RS256")
    core_exceptions = ModuleType("azure.core.exceptions")
    core_exceptions.AzureError = type("AzureError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", core_exceptions)

    verifier = KeyVaultSigner("https://synthetic.vault.azure.net/", "snapshot-key")

    crypto_module.CryptographyClient.assert_called_once_with(
        "https://synthetic.vault.azure.net/keys/snapshot-key", credential=credential
    )
    assert verifier.verify(b"snapshot", base64.b64encode(b"signature").decode())
    crypto.verify.assert_called_once_with("RS256", hashlib.sha256(b"snapshot").digest(), b"signature")
    crypto.sign.assert_not_called()
    import pytest

    with pytest.raises(PermissionError, match="P2 pipeline"):
        verifier.sign(b"snapshot")
    crypto.sign.assert_not_called()


def test_b1_apim_member_identity_comes_only_from_validated_claim():
    policy = ElementTree.parse(ROOT / "infra/policies/member-values.xml")
    inbound = policy.getroot().find("inbound")
    header = inbound.find("set-header[@name='X-Authenticated-Member']/value")
    assert 'context.Variables["jwt"]' in header.text
    assert '"member_ref"' in header.text
    assert "context.Request.Headers" not in header.text
    children = list(inbound)
    method_gate = inbound.find("choose")
    token_gate = inbound.find("validate-jwt")
    assert children.index(method_gate) < children.index(token_gate)
    assert method_gate.find("when/return-response/set-status").attrib["code"] == "405"
    assert token_gate.attrib["failed-validation-httpcode"] == "401"


def test_b7_apim_strategy_policy_is_valid_xml_and_has_closed_fields():
    policy = ElementTree.parse(ROOT / "infra/policies/strategy-request.xml")
    condition = policy.getroot().find("inbound/choose/when")
    assert 'var allowed = new [] { "journey_id", "journey_state", "intent", "channel", "accessibility_mode" }' in condition.attrib["condition"]
    assert condition.find("return-response/set-status").attrib["code"] == "400"


def test_b3_runtime_key_vault_role_allows_only_verification():
    source = (ROOT / "infra/modules/rbac.bicep").read_text()
    role = source.split("resource verifyRole ", 1)[1].split("\nresource ", 1)[0]
    actions = re.search(r"\bactions:\s*\[([^]]*)\]", role)
    data_actions = re.search(r"\bdataActions:\s*\[([^]]*)\]", role)
    assert actions is not None and not actions.group(1).strip()
    assert data_actions is not None
    assert re.findall(r"'([^']*)'", data_actions.group(1)) == [
        "Microsoft.KeyVault/vaults/keys/verify/action"
    ]


def test_remote_application_images_survive_reprovisioning():
    driver = (ROOT / "scripts/deploy.py").read_text()
    helpers = (ROOT / "scripts/delivery.py").read_text()
    for name in ("orchestratorImage", "strategyServiceImage", "memberValuesImage"):
        assert f'"{name}": images["runtime"]["image"]' in driver
    assert 'if not re.fullmatch(r"sha256:[a-f0-9]{64}", existing):' in helpers
    assert "@{existing}" in helpers


def test_persisted_seed_drives_new_names_but_empty_seed_keeps_legacy_names():
    parameters = json.loads((ROOT / "infra/main.parameters.json").read_text())["parameters"]
    assert parameters["namingSeed"]["value"] == "${GOVERNEDGRAPH_NAMING_SEED}"
    source = (ROOT / "infra/main.bicep").read_text()
    assert "param namingSeed string" in source
    token = source.split("var token = ", 1)[1].split("\nvar tags", 1)[0]
    assert "empty(namingSeed)" in token
    assert "? toLower(uniqueString(subscription().id, rg.id, environmentName))" in token
    assert ": toLower(uniqueString(subscription().id, rg.id, environmentName, namingSeed))" in token
    assert "newGuid(" not in source


def test_runtime_registry_access_precedes_container_app_creation():
    source = (ROOT / "infra/modules/compute.bicep").read_text()
    apps = source.split("resource ca ", 1)[1].split("\noutput ", 1)[0]
    assert "dependsOn: [ runtimeAcrPull, strategyAcrPull, memberValuesAcrPull ]" in apps
    assert "scope: acr" in source.split("resource runtimeAcrPull ", 1)[1].split("\nresource ", 1)[0]
    assert "scope: acr" in source.split("resource memberValuesAcrPull ", 1)[1].split("\nresource ", 1)[0]


def test_b1_apim_uses_explicit_methods_for_read_only_rejections():
    source = (ROOT / "infra/modules/gateway.bicep").read_text()
    methods = re.search(r"var blockedValuesMethods = \[([^]]*)\]", source)
    assert methods is not None
    assert set(re.findall(r"'([^']*)'", methods.group(1))) == {
        "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"
    }
    assert "method: '*'" not in source
    post = source.split("resource rejectValuesPost ", 1)[1].split("\nvar ", 1)[0]
    assert "method: 'POST'" in post
    operations = source.split("resource rejectValuesWrites ", 1)[1].split("\nresource ", 1)[0]
    assert "for method in blockedValuesMethods" in operations
    assert "method: method" in operations
    assert "dependsOn: [ rejectValuesPost ]" in operations

def test_azure_client_factories_fail_closed_without_endpoints(monkeypatch):
    from dataclasses import replace
    import pytest
    from governedgraph.agents import clients

    monkeypatch.setattr(clients, "settings", replace(
        clients.settings, member_values_url="", strategy_service_url="",
    ))
    with pytest.raises(ValueError, match="MEMBER_VALUES_API_URL"):
        clients.default_values_client("synthetic-token")
    with pytest.raises(ValueError, match="STRATEGY_SERVICE_URL"):
        clients.default_strategy_client()


def test_azure_search_does_not_fall_back_to_local_when_unconfigured(monkeypatch):
    from dataclasses import replace
    import pytest
    from governedgraph.search import approved_content

    monkeypatch.setattr(approved_content, "settings", replace(
        approved_content.settings, search_endpoint="",
    ))
    with pytest.raises(ValueError, match="AZURE_SEARCH_ENDPOINT"):
        approved_content.default_search()
