"""Bounded read-only lookup of explicitly captured gg-dev synthetic conversation spans."""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

from governedgraph.admin.data import WATERMARK
from governedgraph.agents.foundry_tracing import CONVERSATION_SPAN, _message
from governedgraph.orchestrator.store import validate_id


class TraceUnavailable(RuntimeError):
    pass


Runner = Callable[[str], list[dict]]


def monitor_runner(workspace: str) -> Runner:
    """Query Log Analytics with the app's own managed identity; used where no Azure CLI exists."""
    client = None

    def run(query: str) -> list[dict]:
        nonlocal client
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus

        try:
            client = client or LogsQueryClient(DefaultAzureCredential())
            result = client.query_workspace(workspace, query, timespan=timedelta(days=7), server_timeout=30)
        except Exception as error:
            raise TraceUnavailable(
                "Azure Monitor rejected trace lookup; check the administration identity's log access.") from error
        if result.status != LogsQueryStatus.SUCCESS:
            raise TraceUnavailable("Azure Monitor returned partial trace results; retry shortly.")
        table = result.tables[0]
        return [{column: value.isoformat() if hasattr(value, "isoformat") else value
                 for column, value in zip(table.columns, row, strict=True)} for row in table.rows]

    return run


class SyntheticTraceReader:
    def __init__(self, workspace: str = "", project: str = "", environment: str = "", runner: Runner | None = None):
        self.workspace, self.project, self.environment, self.runner = workspace, project, environment, runner

    @property
    def enabled(self) -> bool:
        return bool(self.workspace and self.project and self.environment == "gg-dev")

    def _cli(self, query: str) -> list[dict]:
        try:
            result = subprocess.run(
                ["az", "monitor", "log-analytics", "query", "--workspace", self.workspace,
                 "--analytics-query", query, "--timespan", "P7D", "--output", "json", "--only-show-errors"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TraceUnavailable("Azure CLI trace lookup could not complete; check CLI availability and sign-in.") from error
        if result.returncode:
            raise TraceUnavailable("Azure rejected trace lookup; check existing operator log access and CLI sign-in.")
        return json.loads(result.stdout)

    def read(self, journey_id: str) -> dict:
        validate_id(journey_id)
        if not self.enabled:
            raise TraceUnavailable("Synthetic trace lookup is not configured. See the administration setup guide.")
        UUID(self.workspace)
        if not re.fullmatch(
            r"/subscriptions/[A-Za-z0-9-]+/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft\.CognitiveServices"
            r"/accounts/[A-Za-z0-9-]+/projects/[A-Za-z0-9_-]+", self.project,
        ):
            raise ValueError("invalid Foundry project resource ID")
        query = f"""AppDependencies
| where TimeGenerated > ago(7d)
| where Name == "{CONVERSATION_SPAN}"
| where tostring(Properties["microsoft.foundry.project.id"]) == "{self.project}"
| where tolower(tostring(Properties["governedgraph.synthetic.conversation"])) == "true"
| where tostring(Properties["governedgraph.synthetic.watermark"]) == "{WATERMARK}"
| where tostring(Properties["gen_ai.conversation.id"]) == "{journey_id}"
| project timestamp=TimeGenerated, journey_id=tostring(Properties["gen_ai.conversation.id"]),
    response_id=tostring(Properties["gen_ai.response.id"]),
    watermark=tostring(Properties["governedgraph.synthetic.watermark"]),
    capture_status=tostring(Properties["governedgraph.conversation.capture_status"]),
    input=tostring(Properties["gen_ai.input.messages"]), output=tostring(Properties["gen_ai.output.messages"])
| order by timestamp asc
| take 101"""
        try:
            rows = (self.runner or self._cli)(query)
            if not isinstance(rows, list) or len(rows) > 101:
                raise ValueError("invalid trace query response")
            turns = []
            for row in rows[:100]:
                if row["journey_id"] != journey_id or row["watermark"] != WATERMARK:
                    raise ValueError("trace scope mismatch")
                if not re.fullmatch(r"[a-f0-9]{32}|[a-f0-9]{64}", row["response_id"]):
                    raise ValueError("invalid trace response ID")
                messages = []
                for field, role in (("input", "user"), ("output", "assistant")):
                    text = row[field]
                    if not isinstance(text, str) or len(text.encode("utf-8")) > 7500:
                        raise ValueError("invalid trace message size")
                    parsed = json.loads(text)
                    if parsed == [] and role == "user":
                        continue
                    if (not isinstance(parsed, list) or len(parsed) != 1
                            or set(parsed[0]) != {"role", "parts"} or parsed[0]["role"] != role
                            or not isinstance(parsed[0]["parts"], list) or len(parsed[0]["parts"]) != 1
                            or set(parsed[0]["parts"][0]) != {"type", "content"}
                            or parsed[0]["parts"][0]["type"] != "text"
                            or not isinstance(parsed[0]["parts"][0]["content"], str)):
                        raise ValueError("invalid synthetic message envelope")
                    safe, status = _message(role, parsed[0]["parts"][0]["content"])
                    messages.append({"role": role, "text": json.loads(safe)[0]["parts"][0]["content"],
                                     "display_status": status})
                turns.append({"timestamp": row["timestamp"], "response_id": row["response_id"],
                              "capture_status": row["capture_status"], "messages": messages})
            return {"watermark": WATERMARK, "journey_id": journey_id, "turns": turns,
                    "truncated": len(rows) > 100, "window": "Last 7 days; up to 100 captured turns",
                    "notice": "Diagnostic captures only, not a complete or authoritative conversation archive."}
        except (ValueError, KeyError, TypeError) as error:
            raise TraceUnavailable("Azure returned invalid or out-of-scope synthetic trace data.") from error
