"""B4/B8: Azure ML platform identity is not the bounded-learning signer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_b8_workspace_system_identity_cannot_be_the_p2_signer():
    source = (ROOT / "infra/modules/learning.bicep").read_text()
    workspace = source.split("resource mlw ", 1)[1].split("module workspaceRoles", 1)[0]
    assert "identity: { type: 'SystemAssigned' }" in workspace
    assert "pipeline" not in workspace
    assert "primaryUserAssignedIdentity" not in source
    assert "pipelineIdentityId" not in source
    assert "keyVault: mlVault.id" in workspace
    assert "storageAccount: storage.id" in workspace
    assert "containerRegistry: registry.id" in workspace
    assert "applicationInsights: mlInsights.id" in workspace
    assert "systemDatastoresAuthMode: 'Identity'" in workspace
    assert "allowRoleAssignmentOnRG: false" in workspace
    assert "workspaces@2024-10-01-preview" in workspace
    main = (ROOT / "infra/main.bicep").read_text()
    learning = main.split("module learning ", 1)[1].split("module rbac ", 1)[0]
    assert "pipelinePrincipalId: security.outputs.pipelinePrincipalId" in learning


def test_b8_ml_platform_resources_are_dedicated_and_keyless():
    source = (ROOT / "infra/modules/learning.bicep").read_text()
    assert "name: 'kv-ml-gg-${token}'" in source
    assert "name: 'mlgg${token}'" in source
    assert "name: 'crmlgg${token}'" in source
    assert "name: 'appi-ml-gg-${token}'" in source
    assert "WorkspaceResourceId: logAnalyticsId" in source
    assert "DisableLocalAuth: true" in source
    assert "enableRbacAuthorization: true" in source
    assert "allowSharedKeyAccess: false" in source
    assert "adminUserEnabled: false" in source
    assert "anonymousPullEnabled: false" in source
    assert source.count("publicNetworkAccess: 'Disabled'") == 2
    assert "Microsoft.KeyVault/vaults/keys" not in source
    assert "ranking-snapshot-signing" not in source
    assert "dev-member-token-signing" not in source
    assert "keyVaultId" not in source
    assert "reuseExisting" not in source
    for target in ("ml-vault", "ml-blob", "ml-file"):
        assert f"name: '{target}'" in source


def test_b4_p2_only_reads_aggregates_and_explicit_artifact_containers():
    source = (ROOT / "infra/modules/learning.bicep").read_text()
    assert "scope: aggregates" in source
    assert "scope: artifactContainers[i]" in source
    assert "storageMetadataReader" not in source
    assert "insightsMetadataReader" not in source
    assert "p2RegistryPull" not in (ROOT / "infra/modules/rbac.bicep").read_text()
    roles = (ROOT / "infra/modules/ml-workspace-rbac.bicep").read_text()
    assert "workspacePrincipalId: mlw.identity.principalId" in source
    assert "vaultName: mlVault.name" in source
    assert "storageName: storage.name" in source
    assert "registryName: registry.name" in source
    assert "appInsightsName: mlInsights.name" in source
    scopes = {"workspaceSelf": "workspace", "workspaceStorage": "storage",
              "workspaceVaultMetadata": "mlVault", "workspaceRegistry": "registry",
              "workspaceInsights": "insights"}
    for name, scope in scopes.items():
        assignment = roles.split(f"resource {name} ", 1)[1].split("\n}", 1)[0]
        assert "principalId: workspacePrincipalId" in assignment
        assert f"scope: {scope}" in assignment
        assert "pipelinePrincipalId" not in assignment


def test_b8_ml_provider_data_grants_are_not_duplicated_by_iac():
    roles = (ROOT / "infra/modules/ml-workspace-rbac.bicep").read_text()
    assert "workspaceBlobs" not in roles
    assert "workspaceVaultData" not in roles
    assert "ba92f5b4-2d11-453d-a403-e96b0029c9fe" not in roles
    assert "00482a5a-887f-4fb3-b363-3b7fe8e74483" not in roles
    assert "69566ab7-960f-475b-8e7c-b3118f30c6bd" not in roles
    assert "Azure ML owns its automatic Blob/File data and operational-vault grants" in roles


def test_ml_compute_diagnostics_are_exported_at_workspace_scope():
    source = (ROOT / "infra/modules/learning.bicep").read_text()
    diagnostics = source.split("resource diagnostics ", 1)[1].split("\n}", 1)[0]
    assert "scope: mlw" in diagnostics
    assert "workspaceId: logAnalyticsId" in diagnostics
    for category in ("AmlComputeJobEvent", "AmlComputeClusterEvent", "AmlRunStatusChangedEvent"):
        assert f"category: '{category}', enabled: true" in diagnostics


def test_b7_temporary_p2_jobs_never_share_a_managed_identity():
    source = (ROOT / "infra/modules/p2-jobs.bicep").read_text()
    assert "{ name: 'pipeline', identityId: pipelineIdentityId, clientId: pipelineClientId }" in source
    assert "{ name: 'fairness', identityId: fairnessIdentityId, clientId: fairnessClientId }" in source
    assert "userAssignedIdentities: { '${role.identityId}': {} }" in source
    assert "identity: role.identityId" in source
    assert "triggerType: 'Manual'" in source
    assert "replicaRetryLimit: 0" in source
    assert "Microsoft.Authorization" not in source
    assert "Microsoft.Network" not in source
    assert "secret" not in source.lower()
