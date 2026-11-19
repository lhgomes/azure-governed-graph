"""ILLUSTRATIVE - synthetic data: administration console doubles; no Azure, Entra or Strategy API is contacted."""
import fcntl
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from governedgraph.admin import access as access_module
from governedgraph.admin import api
from governedgraph.admin.access import AccessPolicy
from governedgraph.admin.data import Context, build_overview, pick, project_learning, tier1_decision
from governedgraph.common.contracts import SuspensionEvent
from governedgraph.explainability.store import LocalExplainabilityStore
from governedgraph.strategy_service.service import StrategyService

ORIGIN = "https://admin.example.test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        rows = [json.loads(line) for line in source if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("governance records must be objects")
    return rows


def append_action(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.write(json.dumps(record) + "\n")
        output.flush()
        os.fsync(output.fileno())


def governance_cases(directory: Path) -> list[dict]:
    result = []
    for row in read_lines(directory / "governance" / "cases.jsonl"):
        result.append({
            **pick(row, "case_id type tier audit_id created_at options_for_board"),
            "finding": pick(row["finding"], "request_key strategy_id strategy_version gap_pts breach"),
        })
    return result


class LocalSources:
    """In-process stand-in for AzureSources: the same interface over a test Strategy Service and local files."""

    mode = "local"
    profile = "test-double"

    def __init__(self, service: StrategyService, directory: Path):
        self.service, self.directory = service, Path(directory)

    def context(self, state: dict | None = None) -> Context:
        return Context(self.service.catalogue, self.service.priors, self.service.thresholds, self.service.control_share)

    def state(self) -> dict:
        return self.service.read_state()

    def actions(self) -> list[dict]:
        return read_lines(self.directory / "admin" / "actions.jsonl")

    def overview(self) -> dict:
        state = self.state()
        packs = [tier1_decision(row) for row in read_lines(self.directory / "governance" / "evidence_packs.jsonl")]
        return build_overview(self.context(state), state, mode="local", cases=governance_cases(self.directory),
                              decisions=packs, learning=project_learning(None, {}), actions=self.actions())

    def audit_records(self) -> tuple[list[dict], bool]:
        return LocalExplainabilityStore(self.directory / "explainability" / "records.jsonl").read_all(), False

    def journal(self, record: dict) -> None:
        append_action(self.directory / "admin" / "actions.jsonl", record)

    def apply(self, action: str, event: SuspensionEvent | None) -> None:
        if action == "freeze":
            self.service.freeze()
        elif action == "revert_to_static":
            self.service.revert_to_static()
        elif event is not None:
            self.service.suspend(event)


class SignedIn:
    """Entra sign-in double: a throwaway signing key and reader and operator groups; no key set is fetched."""

    def __init__(self, monkeypatch):
        self.tenant, self.client = str(uuid4()), str(uuid4())
        self.readers, self.operators, self.outsiders = str(uuid4()), str(uuid4()), str(uuid4())
        fetcher = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=KEY.public_key()))
        monkeypatch.setattr(access_module, "signing_keys", lambda tenant: fetcher)

    def policy(self, **override) -> AccessPolicy:
        values = {"reader_group": self.readers, "operator_group": self.operators,
                  "client_id": self.client, "tenant_id": self.tenant, **override}
        return AccessPolicy(**values)

    def token(self, groups=(), *, key=KEY, **override) -> str:
        now = int(time.time())
        claims = {"iss": f"https://login.microsoftonline.com/{self.tenant}/v2.0", "aud": self.client,
                  "tid": self.tenant, "oid": str(uuid4()), "iat": now, "exp": now + 600,
                  "preferred_username": "synthetic.operator@example.test", "scp": "Admin.Access", "groups": groups}
        claims.update(override)
        return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")

    def bearer(self, groups=(), **override) -> dict:
        return {"authorization": f"Bearer {self.token(groups, **override)}"}


def open_console(monkeypatch, source, access: AccessPolicy, *, base_url="http://admin.internal", **options) -> TestClient:
    """Hosted console over ``source``; the page's session capability is attached to every request."""
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_ENABLED", "true")
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_PUBLIC_ORIGIN", ORIGIN)
    client = TestClient(api.create_app(access=access, sources=source, **options), base_url=base_url)
    page = client.get("/")
    client.headers["x-admin-session"] = re.search(r'name="admin-session" content="([^"]+)"', page.text)[1]
    return client
