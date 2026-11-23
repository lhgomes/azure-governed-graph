"""Private, per-attempt delivery evidence; never a resume or acceptance mechanism."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from scripts.delivery import DeliveryError, save_json


class Evidence:
    def __init__(self, root: Path):
        self.run_id = uuid4().hex[:12]
        self._lock = threading.RLock()  # image builds record commands from several threads
        self.directory = root / ".run" / "deliveries" / self.run_id
        self.directory.mkdir(mode=0o700, parents=True)
        self.path = self.directory / "receipt.json"
        files = sorted({*root.glob("scripts/*.py"), *root.glob("infra/**/*.bicep"),
                        *root.glob("examples/*/infra/*.bicep")})
        source = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        self.data = {"label": "ILLUSTRATIVE - synthetic data", "run_id": self.run_id,
                     "status": "running", "started_at": self.now(), "stage": "target selection",
                     "source_sha256": source, "stages": [], "commands": [], "deployments": [], "jobs": []}
        self.write()
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                    text=True, timeout=10, check=False)
            self.data["git"] = ({"status": "available", "head": result.stdout.strip()} if result.returncode == 0
                                else {"status": "unavailable", "exit_code": result.returncode})
        except (OSError, subprocess.TimeoutExpired) as error:
            self.data["git"] = {"status": "unavailable", "error_type": type(error).__name__}
        self.stage("target selection")

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def write(self) -> None:
        with self._lock:
            save_json(self.path, self.data)

    def begin(self, section: str, **facts) -> dict:
        with self._lock:
            record = {"started_at": self.now(), "status": "running", **facts}
            self.data.setdefault(section, []).append(record)
            self.write()
            return record

    def begin_numbered(self, section: str, facts) -> tuple[int, dict]:
        """Start a record whose name and files embed its number; concurrent callers never share a number."""
        with self._lock:
            number = len(self.data.setdefault(section, [])) + 1
            return number, self.begin(section, **facts(number))

    def end(self, record: dict, status: str, **facts) -> None:
        with self._lock:
            record.update(status=status, ended_at=self.now(), **facts)
            record["elapsed_seconds"] = (datetime.fromisoformat(record["ended_at"])
                                         - datetime.fromisoformat(record["started_at"])).total_seconds()
            self.write()

    def stage(self, name: str, **facts) -> None:
        with self._lock:
            if self.data["stages"]:
                self.end(self.data["stages"][-1], "succeeded")
            self.data.update(stage=name, **facts)
            self.begin("stages", name=name)

    def finish(self, status: str, error: BaseException | None = None) -> None:
        if self.data["stages"]:
            self.end(self.data["stages"][-1], status)
        for section in ("commands", "deployments", "jobs", "http"):
            for record in self.data.get(section, []):
                if record["status"] == "running":
                    self.end(record, "outcome_unconfirmed")
        self.data.update(status=status, ended_at=self.now())
        if error is not None:
            self.data["error_type"] = type(error).__name__
        self.write()

    def failure(self, status: str, error: BaseException | None = None) -> None:
        try:
            self.finish(status, error)
        except OSError as write_error:
            print(f"Delivery evidence update failed ({type(write_error).__name__}): {self.path}",
                  file=sys.stderr)
        print(f"Delivery stage: {self.data['stage']}; evidence: {self.path}", file=sys.stderr)

    def observe(self, runner, name: str, command: list[str], *, expected: type[dict] | type[list] = dict):
        try:
            result = json.loads(runner.run(command))
            if not isinstance(result, expected):
                raise ValueError("unexpected observation shape")
        except (DeliveryError, OSError, ValueError) as error:
            self.data.setdefault("observations", {})[name] = {
                "status": "unavailable", "error_type": type(error).__name__}
            self.write()
            print(f"Delivery observation {name} unavailable ({type(error).__name__}); "
                  f"see {self.path}.", file=sys.stderr, flush=True)
            return None
        self.data.setdefault("observations", {})[name] = {"status": "observed", "result": result}
        self.write()
        return result

    def deployment_state(self, runner, inputs, record: dict, *, group: bool) -> None:
        scope = ["--subscription", inputs.subscription, "--name", record["name"]]
        if group:
            scope += ["--resource-group", inputs.resource_group]
        level = "group" if group else "sub"
        self.observe(runner, record["name"] + "/state", [
            "az", "deployment", level, "show", *scope, "--query",
            "{id:id,state:properties.provisioningState,correlation_id:properties.correlationId,"
            "template_hash:properties.templateHash,timestamp:properties.timestamp,error_code:properties.error.code}",
            "--only-show-errors", "-o", "json"])
        self.observe(runner, record["name"] + "/operations", [
            "az", "deployment", "operation", level, "list", *scope, "--query",
            "[].{id:operationId,state:properties.provisioningState,operation:properties.provisioningOperation,"
            "resource:properties.targetResource.id,status_code:properties.statusCode,"
            "timestamp:properties.timestamp}", "--only-show-errors", "-o", "json"], expected=list)


def stage(runner, name: str, **facts) -> None:
    evidence = getattr(runner, "evidence", None)
    if evidence is not None:
        evidence.stage(name, **facts)


def network_snapshot(runner, inputs, foundation: dict, when: str) -> None:
    evidence = getattr(runner, "evidence", None)
    if evidence is None:
        return
    scope = ["--subscription", inputs.subscription, "--resource-group", inputs.resource_group,
             "--only-show-errors", "-o", "json"]
    environment = evidence.observe(runner, when + "/environment", [
        "az", "containerapp", "env", "show", *scope,
        "--name", foundation["compute"]["environmentId"].rsplit("/", 1)[-1], "--query",
        "{id:id,state:properties.provisioningState,domain:properties.defaultDomain,static_ip:properties.staticIp,"
        "vnet:properties.vnetConfiguration,public_access:properties.publicNetworkAccess}"])
    if environment and environment.get("domain"):
        evidence.observe(runner, when + "/dns", [
            "az", "network", "private-dns", "record-set", "a", "list", *scope,
            "--zone-name", environment["domain"], "--query",
            "[].{name:name,ttl:ttl,addresses:aRecords[].ipv4Address}"], expected=list)
        evidence.observe(runner, when + "/dns-links", [
            "az", "network", "private-dns", "link", "vnet", "list", *scope,
            "--zone-name", environment["domain"], "--query",
            "[].{name:name,state:virtualNetworkLinkState,vnet:virtualNetwork.id}"], expected=list)
    evidence.observe(runner, when + "/gateway", [
        "az", "apim", "list", *scope, "--query",
        "[].{id:id,state:provisioningState,sku:sku.name,vnet_type:virtualNetworkType,"
        "vnet:virtualNetworkConfiguration}"], expected=list)
    endpoints = evidence.observe(runner, when + "/private-endpoints", [
        "az", "network", "private-endpoint", "list", *scope, "--query",
        "[].{id:id,state:provisioningState,connections:privateLinkServiceConnections[]."
        "{resource:privateLinkServiceId,groups:groupIds,status:privateLinkServiceConnectionState.status},"
        "nics:networkInterfaces[].id}"], expected=list)
    if isinstance(endpoints, list):
        for endpoint in endpoints:
            if any(link["resource"] == foundation["compute"]["environmentId"] for link in endpoint["connections"]):
                for nic in endpoint["nics"]:
                    evidence.observe(runner, when + "/nic/" + nic.rsplit("/", 1)[-1], [
                        "az", "network", "nic", "show", "--subscription", inputs.subscription,
                        "--ids", nic, "--query",
                        "{id:id,state:provisioningState,addresses:ipConfigurations[].privateIPAddress}",
                        "--only-show-errors", "-o", "json"])


def require_images(apps: list[dict], expected: dict[str, str]) -> None:
    """Each listed service's latest template must run exactly the image just deployed."""
    stale = sorted(service for app in apps
                   if (service := app.get("tags", {}).get("azd-service-name")) in expected
                   and expected[service] not in {item.get("image") for item in app["properties"]["template"]["containers"]})
    if stale:
        raise DeliveryError("workloads are not running the new image: " + ", ".join(stale))


def readiness_observation(runner, kind: str, rows: list[dict]) -> None:
    evidence = getattr(runner, "evidence", None)
    if evidence is None:
        return
    result = []
    for row in rows:
        properties = row["properties"]
        result.append({
            "name": row["name"], "service": row.get("tags", {}).get("azd-service-name"),
            "provisioning_state": properties.get("provisioningState"),
            "running_status": properties.get("runningStatus"), "ready_revision": properties.get("latestReadyRevisionName"),
            "fqdn": (properties.get("configuration", {}).get("ingress") or {}).get("fqdn"),
            "trigger": properties.get("configuration", {}).get("triggerType"),
            "images": [item.get("image") for item in properties.get("template", {}).get("containers", [])],
        })
    evidence.data.setdefault("readiness", {})[kind] = result
    evidence.write()


def admin_readiness(runner, url: str, *, opener) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
        raise DeliveryError("admin readiness requires an HTTPS URL without credentials or query parameters")
    safe_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    evidence = getattr(runner, "evidence", None)
    record = evidence.begin("http", url=safe_url) if evidence is not None else None
    try:
        with opener(url, timeout=30) as response:
            status = response.status
    except HTTPError as error:
        ids = {}
        activity_id_status = "not_json"
        with error:
            headers = error.headers or {}
            candidates = {header: headers.get(header) for header in (
                "apim-request-id", "apim-trace-id", "x-ms-request-id", "x-ms-correlation-request-id")}
            if headers.get("Content-Type", "").split(";", 1)[0].strip().lower() == "application/json":
                try:
                    body = json.loads(error.read(4096))
                    activity_id_status = "absent"
                    if isinstance(body, dict) and isinstance(body.get("activityId"), str):
                        candidates["activityId"] = body["activityId"]
                        activity_id_status = "observed"
                except (OSError, ValueError, UnicodeError) as body_error:
                    activity_id_status = type(body_error).__name__
            for name, value in candidates.items():
                if value:
                    try:
                        UUID(value)
                        ids[name] = value
                    except ValueError:
                        ids[name] = "not-a-uuid"
        if evidence is not None and record is not None:
            evidence.end(record, "failed", http_status=error.code, request_ids=ids,
                         activity_id_status=activity_id_status)
        raise DeliveryError(f"admin readiness HTTP {error.code} at {safe_url}; "
                            f"request IDs: {json.dumps(ids, sort_keys=True)}. "
                            "Inspect gateway/backend connectivity; no application or platform cause is inferred.") from error
    except (URLError, OSError) as error:
        if evidence is not None and record is not None:
            evidence.end(record, "failed", error_type=type(error).__name__)
        raise DeliveryError(f"admin readiness connection failed ({type(error).__name__}) at {safe_url}") from error
    if evidence is not None and record is not None:
        evidence.end(record, "succeeded" if status == 200 else "failed", http_status=status)
    if status != 200:
        raise DeliveryError(f"admin readiness HTTP {status} at {safe_url}")
