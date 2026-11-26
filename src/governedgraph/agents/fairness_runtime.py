"""Versioned Foundry fairness agent with host-bound, protective-only tools."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from uuid import uuid4

from governedgraph.agents import llm_log
from governedgraph.agents.fairness_explainability import assemble_explainability_report
from governedgraph.agents.foundry_runtime import MAX_CALLS_PER_ROUND, MAX_TOOL_ROUNDS, FoundryUnavailable
from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError, _strict, utcnow

log = logging.getLogger("governedgraph.fairness")
WATERMARK = "ILLUSTRATIVE - synthetic data"
INSTRUCTIONS = """You are the protective Fairness & Explainability Agent.
Use only the four declared host-bound tools. The deterministic audit always
applies its required global vetoes, regardless of model choices. Never promote,
reinstate, edit content, change thresholds or release a freeze. A reinstatement
request is refused and recorded as a Tier 2 governance case by the host.
Never request or expose cohort attributes, cohort metrics, member references,
member records or official figures. Tools return receipts only. Model text is
not an authorized decision or a member data-access report. For operation audit,
use run_cohort_audit or suspend_strategy only. For reinstate, use
open_governance_case only. For report, use assemble_explainability_report only."""
TOOL_DESCRIPTIONS = {
    "run_cohort_audit": "Run the approved isolated audit and enforce all protective vetoes; return a receipt only.",
    "suspend_strategy": "Enforce the bound audit's required suspensions, never a model-selected strategy.",
    "open_governance_case": "Record the bound request for Tier 2 governance; cannot reinstate or promote.",
    "assemble_explainability_report": "Prepare the host-authorized member report without sending its contents to the model.",
}


@dataclass(frozen=True)
class FairnessRequest:
    action: str
    strategy_id: str = ""
    member_ref: str = ""

    def __post_init__(self):
        if not all(isinstance(value, str) for value in (self.action, self.strategy_id, self.member_ref)):
            raise ContractError("fairness request fields must be strings")
        if self.action not in {"audit", "reinstate", "report"}:
            raise ContractError("unknown fairness operation")
        if self.action == "reinstate":
            if not isinstance(self.strategy_id, str) or not re.fullmatch(r"S[1-9][0-9]*", self.strategy_id):
                raise ContractError("a strategy identifier is required for governance routing")
        elif self.strategy_id:
            raise ContractError("strategy scope is only accepted for governance routing")
        if self.action == "report":
            if not isinstance(self.member_ref, str) or not re.fullmatch(r"MBR-[A-Z0-9-]{4,64}", self.member_ref):
                raise ContractError("an operator-authorized pseudonymous member reference is required")
        elif self.member_ref:
            raise ContractError("member scope is only accepted for a data-access report")

    @classmethod
    def from_dict(cls, payload: dict) -> "FairnessRequest":
        return _strict(cls, payload)


def agent_definition():
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition

    return PromptAgentDefinition(
        model=settings.compact_deployment, instructions=INSTRUCTIONS, temperature=0.0,
        tools=[FunctionTool(name=name, description=description, strict=True,
                            parameters={"type": "object", "properties": {}, "required": [],
                                        "additionalProperties": False})
               for name, description in TOOL_DESCRIPTIONS.items()],
    )


class FairnessTools:
    def __init__(self, request: FairnessRequest, *, audit_runner, case_writer, records_reader):
        self.request = request
        self.audit_runner, self.case_writer, self.records_reader = audit_runner, case_writer, records_reader
        self.audit_id = None
        self.case_id = None
        self.member_report = None

    def run_cohort_audit(self) -> dict:
        if self.request.action != "audit":
            raise PermissionError("audit is not bound to this request")
        if self.audit_id is None:
            self.audit_id = self.audit_runner()
        return {"audit_id": self.audit_id, "protective_vetoes_enforced": True}

    def suspend_strategy(self) -> dict:
        # The existing audit runner enforces every veto before acknowledging the audit.
        return self.run_cohort_audit()

    def open_governance_case(self) -> dict:
        if self.request.action != "reinstate":
            raise PermissionError("governance routing is not bound to this request")
        if self.case_id is None:
            case_id = "GC-" + uuid4().hex[:12].upper()
            self.case_writer({
                "case_id": case_id, "type": "reinstatement_requested", "tier": "Tier 2",
                "strategy_id": self.request.strategy_id, "status": "refused_pending_human_decision",
                "created_at": utcnow(), "watermark": WATERMARK,
            })
            self.case_id = case_id
        return {"case_id": self.case_id, "tier": "Tier 2", "refused": True}

    def assemble_explainability_report(self) -> dict:
        if self.request.action != "report":
            raise PermissionError("member report is not bound to this request")
        if self.member_report is None:
            self.member_report = assemble_explainability_report(
                self.request.member_ref, self.records_reader(self.request.member_ref),
            )
        return {"report_prepared": True, "contents_withheld_from_model": True}

    def preflight(self) -> dict:
        return {"audit": self.run_cohort_audit, "reinstate": self.open_governance_case,
                "report": self.assemble_explainability_report}[self.request.action]()

    def dispatch(self, name: str, arguments: str) -> str:
        if name not in TOOL_DESCRIPTIONS or json.loads(arguments) != {}:
            log.error("fairness_unapproved_tool_refused")
            raise PermissionError("unapproved fairness tool or arguments")
        return json.dumps(getattr(self, name)(), sort_keys=True)


class FoundryFairnessAgent:
    def __init__(self, *, client=None, credential=None):
        if client is None:
            from azure.ai.projects import AIProjectClient

            if credential is None:
                raise FoundryUnavailable("the fairness managed identity is required")
            project = AIProjectClient(endpoint=settings.project_endpoint, credential=credential,
                                      allow_preview=True, connection_timeout=5, read_timeout=10, retry_total=0)
            project.agents.get(settings.fairness_agent_name)
            client = project.get_openai_client(agent_name=settings.fairness_agent_name, max_retries=0, timeout=20)
        self.client = client

    def run(self, tools: FairnessTools) -> dict:
        receipt = tools.preflight()
        inputs = [{"type": "message", "role": "user", "content": json.dumps({
            "operation": tools.request.action, "host_receipt": receipt,
        })}]
        previous = None
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            kwargs = {"input": inputs, "timeout": 20, "max_output_tokens": 600,
                      "extra_body": {"agent_reference": {"type": "agent_reference",
                                     "name": settings.fairness_agent_name}}}
            if previous is not None:
                kwargs["previous_response_id"] = previous
            response = self.client.responses.create(**kwargs)
            llm_log.record(None, "fairness", settings.fairness_agent_name,
                           {"agent": settings.fairness_agent_name,
                            "input": inputs, "previous_response_id": previous}, response)
            if response.status != "completed":
                raise FoundryUnavailable("fairness response did not complete")
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                return {"agent_name": settings.fairness_agent_name, **receipt}
            if round_number == MAX_TOOL_ROUNDS or len(calls) > MAX_CALLS_PER_ROUND:
                raise FoundryUnavailable("fairness tool-call limit reached")
            inputs = [{"type": "function_call_output", "call_id": call.call_id,
                       "output": tools.dispatch(call.name, call.arguments)} for call in calls]
            previous = response.id
        raise FoundryUnavailable("fairness tool-call limit reached")


def azure_runtime(request: FairnessRequest) -> tuple[FoundryFairnessAgent, FairnessTools]:
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient

    from governedgraph.explainability.store import BlobExplainabilityStore
    from governedgraph.learning.azure_inputs import BlobLearningInputs
    from governedgraph.strategy_service.client import StrategyAdminClient

    if (os.environ.get("GOVERNEDGRAPH_P2_ROLE") != "fairness"
            or not settings.fairness_identity_client_id
            or os.environ.get("AZURE_CLIENT_ID") != settings.fairness_identity_client_id):
        raise PermissionError("fairness runtime requires its isolated managed-identity host")
    credential = ManagedIdentityCredential(client_id=settings.fairness_identity_client_id)
    inputs = BlobLearningInputs(credential=credential)
    control = StrategyAdminClient(protective_only=True, credential=credential)
    records = BlobExplainabilityStore(
        settings.explainability_account_url, settings.explainability_container,
        client=BlobServiceClient(settings.explainability_account_url, credential=credential),
    )

    def write_case(record: dict) -> None:
        blob = inputs.private.get_blob_client("cohort-zone", "governance/" + record["case_id"] + ".json")
        encoded = json.dumps(record, sort_keys=True).encode()
        blob.upload_blob(encoded, overwrite=False, metadata={"watermark": WATERMARK})
        if blob.download_blob().readall() != encoded:
            raise ValueError("governance case read-back conflict")

    return FoundryFairnessAgent(credential=credential), FairnessTools(
        request, audit_runner=lambda: inputs.run_fairness(control),
        case_writer=write_case, records_reader=records.read_member,
    )


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the protective agent on its isolated fairness host.")
    parser.add_argument("--action", required=True, choices=("audit", "reinstate", "report"))
    parser.add_argument("--strategy-id", default="")
    parser.add_argument("--member-ref", default="")
    args = parser.parse_args(argv)
    request = FairnessRequest(args.action, strategy_id=args.strategy_id, member_ref=args.member_ref)
    agent, tools = azure_runtime(request)
    print(json.dumps(agent.run(tools), sort_keys=True))


if __name__ == "__main__":
    main()
