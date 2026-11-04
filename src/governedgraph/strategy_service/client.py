"""Managed-identity offline client; never writes ranking blobs directly."""
from __future__ import annotations

from dataclasses import asdict

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.contracts import SuspensionEvent
from governedgraph.common.io import load_json
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.snapshot import RankingSnapshot, default_signer, verify_snapshot


class StrategyAdminClient:
    def __init__(self, *, protective_only: bool = False, credential=None):
        from azure.identity import DefaultAzureCredential
        import httpx

        if not settings.strategy_admin_url or not settings.strategy_api_client_id:
            raise ValueError("strategy administration URL and Entra application ID are required")
        self.credential = credential if credential is not None else DefaultAzureCredential()
        self.http = httpx.Client(base_url=settings.strategy_admin_url.rstrip("/") + "/", timeout=30)
        self.catalogue = Catalogue.load()
        self.priors = load_json(DATA_DIR / "catalogue" / "expert_priors_v1.json")
        self.current = None
        self.protective_only = protective_only
        if not protective_only:
            self.refresh()

    def refresh(self) -> dict:
        if self.protective_only:
            raise PermissionError("protective-only client cannot read or verify ranking snapshots")
        response = self.http.get("snapshots")
        response.raise_for_status()
        state = response.json()
        current = RankingSnapshot.from_dict(state["current"]) if state["current"] else None
        if current is not None:
            if (not verify_snapshot(current, default_signer()) or current.catalogue_version != self.catalogue.version
                    or any(not self.catalogue.is_active(sid, version)
                           for ranking in current.rankings.values() for sid, version in ranking)):
                raise ValueError("remote snapshot failed signature or active-catalogue verification")
        self.current = current
        return state

    def _post(self, path: str, payload: dict | None = None) -> None:
        if self.protective_only and path not in {"freeze", "suspensions", "revert-to-static"}:
            raise PermissionError("protective-only client cannot publish ranking snapshots")
        token = self.credential.get_token(f"api://{settings.strategy_api_client_id}/.default")
        response = self.http.post(path, json=payload, headers={"Authorization": f"Bearer {token.token}"})
        response.raise_for_status()

    def load_snapshot(self, snapshot: RankingSnapshot, *, expected_snapshot_id: str | None = None) -> None:
        payload = snapshot.to_dict()
        if expected_snapshot_id is not None:
            payload = {"snapshot": payload, "expected_snapshot_id": expected_snapshot_id}
        self._post("snapshots", payload)
        self.refresh()

    def suspend(self, event: SuspensionEvent) -> None:
        self._post("suspensions", asdict(event))

    def freeze(self) -> None:
        self._post("freeze")

    def revert_to_static(self) -> None:
        self._post("revert-to-static")

    def suspended_for(self, journey_state: str) -> list[str]:
        return sorted({e["strategy_id"] for e in self.refresh()["suspensions"]
                       if e["journey_state"] in (journey_state, "*")})
