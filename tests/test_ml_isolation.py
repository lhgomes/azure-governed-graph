"""Greenfield P2 resource isolation and least privilege."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_greenfield_has_no_aml_or_unused_queue_resources():
    templates = "\n".join(p.read_text() for p in (ROOT / "infra").rglob("*.bicep"))
    for retired in ("Microsoft.MachineLearningServices", "ml-compute", "crmlgg", "life-events", "conversion-batches", "Microsoft.Fabric/capacities"):
        assert retired not in templates
    assert "name: 'journey-events'" in templates and "name: 'escalations'" in templates


def test_p2_grants_are_scoped_and_keep_signing_separate():
    access = (ROOT / "infra/modules/p2-access.bicep").read_text()
    assert "scope: artifacts" in access
    assert "scope: aggregates" in access
    assert "scope: registry" in access
    assert "principalId: pipelinePrincipalId" in access
    assert "scope: storage" not in access and "Microsoft.KeyVault" not in access
    data = (ROOT / "infra/modules/data.bicep").read_text()
    assert "name: 'p2-artifacts'" in data
    jobs = (ROOT / "infra/modules/p2-jobs.bicep").read_text()
    assert "{ name: 'pipeline', identityId: pipelineIdentityId, clientId: pipelineClientId }" in jobs
    assert "{ name: 'fairness', identityId: fairnessIdentityId, clientId: fairnessClientId }" in jobs
    assert "userAssignedIdentities: { '${role.identityId}': {} }" in jobs
    assert "identity: role.identityId" in jobs
    assert "triggerType: 'Schedule'" in jobs
    assert "replicaRetryLimit: 0" in jobs
    assert "crgg${take(token, 16)}.azurecr.io" in jobs
