import json
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from governedgraph.dev import check_key_isolation, issue_member_token


ROOT = Path(__file__).resolve().parents[1]
PRINCIPAL = "00000000-0000-4000-8000-0000000000d4"
VAULT = "/subscriptions/synthetic/resourceGroups/dev/providers/Microsoft.KeyVault/vaults/synthetic"


def test_b1_local_member_issuer_uses_explicit_cli_credential(monkeypatch):
    credential = MagicMock()
    cli = MagicMock(return_value=credential)
    key = SimpleNamespace(id="https://synthetic.vault.azure.net/keys/dev-member-token-signing/version",
                          key=SimpleNamespace(n=b"\x01", e=b"\x03"))
    keys = MagicMock()
    keys.return_value.get_key.return_value = key
    crypto = MagicMock()
    for name, attributes in {
        "azure.identity": {"AzureCliCredential": cli},
        "azure.keyvault.keys": {"KeyClient": keys},
        "azure.keyvault.keys.crypto": {"CryptographyClient": crypto},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    issue_member_token.KeyVaultTokenSigner("https://synthetic.vault.azure.net", "dev-member-token-signing")
    cli.assert_called_once_with()
    keys.assert_called_once_with("https://synthetic.vault.azure.net", credential)
    crypto.assert_called_once_with(key.id, credential)
    assert "DefaultAzureCredential" not in (ROOT / "src/governedgraph/dev/issue_member_token.py").read_text()


def test_b1_hosted_member_issuer_cannot_generate_member_tokens(monkeypatch):
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER", "true")
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://synthetic-identity")
    monkeypatch.setattr(sys, "argv", ["issue_member_token", "--check-facade"])
    signer = MagicMock()
    monkeypatch.setattr(issue_member_token, "KeyVaultTokenSigner", signer)
    with pytest.raises(SystemExit) as error:
        issue_member_token.main()
    assert error.value.code == 2
    signer.assert_not_called()


def test_b1_hosted_public_key_uses_the_explicit_issuer_identity(monkeypatch, capsys):
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER", "true")
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://synthetic-identity")
    monkeypatch.setenv("AZURE_CLIENT_ID", "synthetic-dev-issuer-client")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://synthetic.vault.azure.net")
    monkeypatch.setattr(sys, "argv", ["issue_member_token", "--public-jwk"])
    credential = MagicMock()
    identity = ModuleType("azure.identity")
    identity.ManagedIdentityCredential = MagicMock(return_value=credential)
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    signer = MagicMock()
    signer.return_value.public_jwk = {"kty": "RSA", "use": "sig"}
    monkeypatch.setattr(issue_member_token, "KeyVaultTokenSigner", signer)
    issue_member_token.main()
    identity.ManagedIdentityCredential.assert_called_once_with(client_id="synthetic-dev-issuer-client")
    signer.assert_called_once_with("https://synthetic.vault.azure.net", "dev-member-token-signing",
                                   credential=credential)
    assert json.loads(capsys.readouterr().out) == {"kty": "RSA", "use": "sig"}


def block(source, name):
    return source.split(f"resource {name} ", 1)[1].split("\nresource ", 1)[0]


def test_b1_b3_dev_issuer_is_explicitly_opt_in_and_uses_only_its_own_identity():
    main = (ROOT / "infra/main.bicep").read_text()
    assert "param deployDevIssuer bool = false" in main
    bootstrap = (ROOT / "infra/bootstrap.bicep").read_text()
    assert "param deployDevIssuer bool = false" in bootstrap
    assert "module issuer 'modules/dev-member-issuer.bicep' = if (deployDevIssuer)" in bootstrap
    params = json.loads((ROOT / "infra/main.parameters.json").read_text())["parameters"]
    assert params["deployDevIssuer"]["value"] == "${GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER=false}"
    assert "enableDevMemberIssuer" not in params
    security = (ROOT / "infra/modules/security.bicep").read_text()
    assert "= if (deployDevIssuer)" in block(security, "devIssuerId")
    assert "= if (deployDevIssuer)" in block(security, "devMemberKey")
    assert "dev-only-member-issuer" in block(security, "devIssuerId")
    assert "dev-member-token-signing" in block(security, "devMemberKey")
    assert "tags:" not in block(security, "devMemberKey")
    issuer = (ROOT / "infra/modules/dev-member-issuer.bicep").read_text()
    assert "pipelineIdentity" not in issuer and "runtimeIdentity" not in issuer
    assert "identity: issuerIdentityId" in issuer
    assert "'${issuerIdentityId}': {}" in issuer
    assert "value: issuerClientId" in issuer
    assert "roleAssignments" not in issuer


def test_b3_all_key_vault_assignments_are_key_scoped_not_vault_scoped():
    for path in (ROOT / "infra/modules").glob("*.bicep"):
        source = path.read_text()
        for resource in source.split("\nresource "):
            if "'Microsoft.Authorization/roleAssignments@" in resource.split("\n", 1)[0]:
                assert not re.search(r"^\s*scope: (?:kv|vault)\s*$", resource, re.MULTILINE), path
    source = (ROOT / "infra/modules/rbac.bicep").read_text()
    for name in ("rtKvVerify", "strategyVerify", "p2Kv", "p2KvVerify"):
        assert "scope: snapshotKey" in block(source, name)
    assert "scope: devMemberKey" in block(source, "devIssuerSign")
    assert "= if (deployDevIssuer)" in block(source, "devIssuerSign")
    assert "principalId: devIssuerPrincipalId" in block(source, "devIssuerSign")
    assert "scope: registry" in block(source, "devIssuerPull")
    assert "principalId: devIssuerPrincipalId" in block(source, "devIssuerPull")
    assert "resource devKv " not in source


def test_developer_storage_access_is_content_container_scoped():
    source = (ROOT / "infra/modules/rbac.bicep").read_text()
    assert "resource devBlob " not in source
    assert "scope: contentContainer" in block(source, "devContentPublish")
    for resource in source.split("\nresource "):
        if "'Microsoft.Authorization/roleAssignments@" in resource.split("\n", 1)[0]:
            if "principalId: developerPrincipalId" in resource:
                assert not re.search(r"^\s*scope: st\s*$", resource, re.MULTILINE)


def test_b3_signer_has_no_encryption_key_management_or_other_key_permissions():
    source = (ROOT / "infra/modules/rbac.bicep").read_text()
    role = block(source, "keySignerRole")
    assert re.findall(r"'([^']*)'", re.search(r"dataActions:\s*\[([^]]*)\]", role).group(1)) == [
        "Microsoft.KeyVault/vaults/keys/read", "Microsoft.KeyVault/vaults/keys/sign/action",
    ]
    assert "actions: []" in role and "notActions: []" in role
    assert "keySignerRole.id" in block(source, "p2Kv")
    assert "verifyRole.id" in block(source, "p2KvVerify")
    assert "keySignerRole.id" in block(source, "devIssuerSign")
    assert "12338af0-0e69-4776-bea7-57ae8d297424" not in source


def test_b3_key_scope_changes_preserve_existing_key_notification_policy():
    source = (ROOT / "infra/modules/security.bicep").read_text()
    assert "action: { type: 'notify' }" in source
    assert "trigger: { timeBeforeExpiry: 'P30D' }" in source
    for name in ("signingKey", "devMemberKey"):
        assert "rotationPolicy: signingKeyRotationPolicy" in block(source, name)
        assert "keyOps: [ 'sign', 'verify' ]" in block(source, name)


@pytest.fixture
def probe_sdk(monkeypatch):
    class ResponseError(Exception):
        def __init__(self, message, status_code=403):
            super().__init__(message)
            self.status_code = status_code

    credential = MagicMock()
    credential.__enter__.return_value = credential
    allowed = MagicMock()
    allowed.__enter__.return_value = allowed
    denied = MagicMock()
    denied.__enter__.return_value = denied
    denied.sign.side_effect = ResponseError(f"ForbiddenByRbac Caller: oid={PRINCIPAL};")
    factory = MagicMock(side_effect=[allowed, denied])
    modules = {
        "azure.core.exceptions": {"HttpResponseError": ResponseError},
        "azure.identity": {"DefaultAzureCredential": MagicMock(return_value=credential)},
        "azure.keyvault.keys.crypto": {
            "CryptographyClient": factory, "SignatureAlgorithm": SimpleNamespace(rs256="RS256"),
        },
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    return SimpleNamespace(allowed=allowed, denied=denied, factory=factory, error=ResponseError)


@pytest.mark.parametrize("identity_role", ["pipeline", "issuer"])
def test_b3_separation_probe_checks_allowed_key_and_actual_identity_rbac_denial(probe_sdk, identity_role):
    result = check_key_isolation.check("https://synthetic.vault.azure.net/", identity_role, PRINCIPAL)
    allowed, denied = check_key_isolation.KEYS[identity_role]
    assert probe_sdk.factory.call_args_list[0].args[0].endswith("/keys/" + allowed)
    assert probe_sdk.factory.call_args_list[1].args[0].endswith("/keys/" + denied)
    assert probe_sdk.allowed.sign.call_count == probe_sdk.denied.sign.call_count == 1
    assert result["rbac_denial"] is True and result["principal_id"] == PRINCIPAL
    assert set(result) == {"identity_role", "principal_id", "allowed_key", "denied_key", "rbac_denial"}
    assert probe_sdk.allowed.sign.call_args.args[1] == probe_sdk.denied.sign.call_args.args[1]


@pytest.mark.parametrize("message,status", [
    (f"ForbiddenByConnection oid={PRINCIPAL};", 403),
    (f"Forbidden oid={PRINCIPAL};", 403),
    (f"ForbiddenByRbac oid={PRINCIPAL};", 500),
    ("ForbiddenByRbac oid=00000000-0000-4000-8000-0000000000a1;", 403),
])
def test_b3_separation_probe_rejects_firewall_outages_and_wrong_identity(probe_sdk, message, status):
    probe_sdk.denied.sign.side_effect = probe_sdk.error(message, status)
    with pytest.raises(RuntimeError, match="expected identity"):
        check_key_isolation.check("https://synthetic.vault.azure.net", "pipeline", PRINCIPAL)


def test_b3_separation_probe_fails_when_cross_key_signing_succeeds(probe_sdk):
    probe_sdk.denied.sign.side_effect = None
    with pytest.raises(RuntimeError, match="other signing key"):
        check_key_isolation.check("https://synthetic.vault.azure.net", "issuer", PRINCIPAL)


def test_b3_separation_probe_requires_positive_control(probe_sdk):
    probe_sdk.allowed.sign.side_effect = probe_sdk.error("signing unavailable", 503)
    with pytest.raises(probe_sdk.error, match="unavailable"):
        check_key_isolation.check("https://synthetic.vault.azure.net", "issuer", PRINCIPAL)
    probe_sdk.denied.sign.assert_not_called()


def test_b3_separation_command_does_not_sign_without_explicit_probe(monkeypatch, capsys):
    check = MagicMock()
    monkeypatch.setattr(check_key_isolation, "check", check)
    monkeypatch.setattr(sys, "argv", ["check_key_isolation", "--identity-role", "pipeline",
                                    "--expected-principal-id", PRINCIPAL])
    check_key_isolation.main()
    check.assert_not_called()
    assert json.loads(capsys.readouterr().out)["probe"] is False
