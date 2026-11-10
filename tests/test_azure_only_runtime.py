"""Azure-only boundary checks; run by the approved Azure-hosted CI runner."""
from dataclasses import fields
from types import SimpleNamespace

import pytest

from governedgraph.common.config import Settings
from governedgraph.search import approved_content
from governedgraph.agents import clients
from governedgraph.strategy_service import snapshot


def test_no_selectable_local_mode():
    assert "mode" not in {field.name for field in fields(Settings)}
    assert Settings().is_azure


def test_azure_search_fails_closed_without_endpoint(monkeypatch):
    monkeypatch.setattr(approved_content, "settings", SimpleNamespace(search_endpoint=""))
    with pytest.raises(ValueError, match="AZURE_SEARCH_ENDPOINT"):
        approved_content.default_search()
    assert not hasattr(approved_content, "LocalContentSearch")


def test_member_values_and_strategy_require_azure_endpoints(monkeypatch):
    monkeypatch.setattr(clients, "settings", SimpleNamespace(member_values_url="", strategy_service_url=""))
    with pytest.raises(ValueError, match="MEMBER_VALUES_API_URL"):
        clients.default_values_client()
    with pytest.raises(ValueError, match="STRATEGY_SERVICE_URL"):
        clients.default_strategy_client()
    assert not hasattr(clients, "LocalValuesClient")
    assert not hasattr(clients, "LocalStrategyClient")


def test_snapshots_cannot_use_local_signing_by_default(monkeypatch):
    monkeypatch.setattr("governedgraph.common.config.settings", SimpleNamespace(key_vault_url=""))
    with pytest.raises(ValueError, match="AZURE_KEY_VAULT_URL"):
        snapshot.default_signer(signing=True)


def test_admin_never_opens_without_entra_groups():
    from fastapi import HTTPException
    from governedgraph.admin.access import AccessPolicy

    for policy in (AccessPolicy(), AccessPolicy(allow_open_actions=True)):
        with pytest.raises(HTTPException) as failed:
            policy.identify("")
        assert failed.value.status_code == 503
