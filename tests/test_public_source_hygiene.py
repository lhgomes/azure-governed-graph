"""Deployment-independent source hygiene checks."""
from scripts.check_public_artifacts import real_endpoint_found, public_hygiene_violations


def test_detects_concrete_resource_without_embedding_live_identifiers():
    host = "project42" + ".services.ai.azure.com"
    assert real_endpoint_found("https://" + host + "/providers/microsoft/v1/systemone")
    assert real_endpoint_found("https://" + "my-deployment.openai.azure.com" + "/openai/")
    assert not real_endpoint_found("https://" + "synthetic.services.ai.azure.com")
    assert not real_endpoint_found("https://" + "example-project.services.ai.azure.com")
    assert not real_endpoint_found("https://" + "$" + "{resource}" + ".services.ai.azure.com")


def test_public_repo_contains_no_probes_or_specific_azure_ai_resource_urls():
    assert public_hygiene_violations() == []
