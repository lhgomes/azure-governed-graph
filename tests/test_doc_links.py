"""Static docs checker does not need deployed Azure resources or identity."""
from pathlib import Path
from scripts.check_doc_links import broken_links


def test_repo_markdown_links_exist():
    assert broken_links() == []


def test_checker_never_requests_deployment_configuration():
    text = (Path(__file__).resolve().parents[1] / "scripts" / "check_doc_links.py").read_text()
    assert "DefaultAzureCredential" not in text
    assert "AZURE_" not in text
