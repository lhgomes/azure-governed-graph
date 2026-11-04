"""Export/check design-only APIM OpenAPI and Service Bus JSON Schemas; no Azure calls."""
from __future__ import annotations

import json
from dataclasses import asdict

from pydantic import TypeAdapter

from pension_wtp.agents.adoption_guide import APPROVED_NEXT_STEPS, NextStep
from pension_wtp.agents.design_contracts import (
    ConversionBatch, EventOutcome, LifeEvent, ReconciliationResult, domain_id,
)
from pension_wtp.agents.design_facade import OPERATIONS
from governedgraph.common.contracts import EscalationRequest

MESSAGE_TYPES = (ConversionBatch, ReconciliationResult, LifeEvent, EventOutcome, NextStep, EscalationRequest)
WATERMARK = "ILLUSTRATIVE - synthetic data"


def definitions() -> dict:
    schemas: dict = {}
    for _, request, response, _ in OPERATIONS.values():
        for model in (request, response):
            schema = TypeAdapter(model).json_schema(ref_template="#/components/schemas/{model}")
            schemas.update(schema.pop("$defs", {}))
            if isinstance(model, type):
                schemas[model.__name__] = schema
    for schema in schemas.values():
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
    schemas["NextStep"]["oneOf"] = [{
        "properties": {"step_id": {"const": sid}, "title": {"const": title},
                       "content_id": {"const": cid}, "content_version": {"const": "1.0"}},
    } for sid, (title, cid) in APPROVED_NEXT_STEPS.items()]
    for name in ("ConversionBatch", "ScheduleConversion"):
        schemas[name]["properties"]["member_refs"]["minItems"] = 1
    schemas["ScheduleConversion"]["properties"]["capacity"]["minimum"] = 1
    schemas["BuildSample"]["properties"]["size"]["minimum"] = 1
    schemas["ConversionBatch"]["allOf"] = [
        {"if": {"properties": {"status": {"enum": ["running", "awaiting_actuary", "exceptions", "released"]}},
                "required": ["status"]},
         "then": {"properties": {"engine_run_id": {"type": "string"}}, "required": ["engine_run_id"]}},
        {"if": {"properties": {"status": {"const": "released"}}, "required": ["status"]},
         "then": {"properties": {"actuary_approval_ref": {"type": "string"}, "exceptions": {"maxItems": 0}},
                  "required": ["actuary_approval_ref"]}},
    ]
    schemas["EventOutcome"]["oneOf"] = [
        {"properties": {"route": {"const": "submitted"}, "reason_code": {"const": "routine_rule_covered"},
                        "administrator_ref": {"type": "string"}}, "required": ["administrator_ref"]},
        {"properties": {"route": {"const": "exception_queue"},
                        "reason_code": {"not": {"const": "routine_rule_covered"}},
                        "administrator_ref": {"type": "null"}}},
    ]
    _nonempty_strings(schemas)
    return schemas


def _nonempty_strings(node: object):
    if isinstance(node, dict):
        if node.get("type") == "string":
            node["minLength"] = 1
            node["pattern"] = r"\S"
        for value in node.values():
            _nonempty_strings(value)
    elif isinstance(node, list):
        for value in node:
            _nonempty_strings(value)


def openapi_document() -> dict:
    paths = {}
    for operation, (agent, request, response, writes) in OPERATIONS.items():
        response_schema = (
            {"type": "array", "items": {"$ref": "#/components/schemas/NextStep"}}
            if response == list[NextStep] else {"$ref": f"#/components/schemas/{response.__name__}"}
        )
        paths[f"/design/v1/{agent}/{operation}"] = {"post": {
            "operationId": operation,
            "summary": operation.replace("_", " "),
            "x-design-depth-only": True,
            "x-writes": writes,
            "parameters": ([{
                "name": "Idempotency-Key", "in": "header", "required": True,
                "schema": {"type": "string", "minLength": 1, "maxLength": 128},
            }] if writes else []),
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{request.__name__}"},
            }}},
            "responses": {
                "200": {"description": "Offline stub result only; no deployed administrator effect",
                        "content": {"application/json": {"schema": response_schema}}},
                "400": {"description": "Closed contract, version or idempotency-key violation"},
                "401": {"description": "Production adapter must authenticate callers"},
                "403": {"description": "Unapproved operation, run plan or scope"},
                "404": {"description": "Unknown batch or life event"},
                "409": {"description": "Conflicting idempotent replay (also a ContractError in the offline stub)"},
                "501": {"description": "Required production adapter is not implemented; never claim success"},
                "503": {"description": "Dependency failure; use the always-available advisor handover"},
            },
        }}
    return {
        "openapi": "3.1.0",
        "info": {"title": "GovernedGraph design-depth agent APIM facade", "version": "1.0.0",
                 "description": f"{WATERMARK}. Contract design only, not an imported/deployed API. "
                 "Writes use idempotency keys; calculation scheduling is queue-only. "
                 "The production APIM adapter must enforce approved tool permissions and bind journey/member "
                 "scope from authenticated context. The shared escalation operation is available to all three agents. "
                 "No release, rules-edit, entitlement, eligibility or complex-case-close operation exists."},
        "servers": [{"url": "https://{gateway}.azure-api.net",
                     "variables": {"gateway": {"default": "apim-gg-dev"}}}],
        "security": [{"entraToken": []}],
        "paths": paths,
        "components": {"securitySchemes": {"entraToken": {"type": "http", "scheme": "bearer",
                                                         "bearerFormat": "JWT"}},
                       "schemas": definitions()},
    }


def message_document() -> dict:
    schemas = json.loads(json.dumps(definitions()).replace("#/components/schemas/", "#/$defs/"))
    variants = []
    for model in MESSAGE_TYPES:
        variants.append({
            "type": "object", "additionalProperties": False,
            "required": ["message_type", "contract_version", "message_id", "session_id", "body"],
            "properties": {
                "message_type": {"const": model.__name__},
                "contract_version": {"const": "1.0"},
                "message_id": {"type": "string", "pattern": "^MSG-[0-9a-f]{32}$"},
                "session_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "body": {"$ref": f"#/$defs/{model.__name__}"},
            },
        })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://governedgraph.example/schemas/design-depth-messages/1.0",
        "title": "GovernedGraph design-depth Service Bus contracts",
        "description": f"{WATERMARK}. At-least-once. Map message_id to MessageId and session_id "
        "to SessionId. Consumers deduplicate before writes; same-key/different-body replay fails. "
        "Use batch ID for conversion/reconciliation, event ID for life events/outcomes and journey ID "
        "for next steps/escalations. Queued batches have no engine run ID yet; every engine result must have one. "
        "EscalationRequest retains the deployed body shape; its version is in this envelope.",
        "oneOf": variants,
        "$defs": {model.__name__: schemas[model.__name__] for model in MESSAGE_TYPES},
    }


def service_bus_message(body, session_id: str) -> dict:
    if type(body) not in MESSAGE_TYPES:
        raise ValueError("unsupported design-depth message type")
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
        raise ValueError("session ID must be a nonempty domain ID of at most 128 characters")
    expected_session = getattr(body, "batch_id", getattr(body, "event_id", getattr(body, "journey_id", session_id)))
    if session_id != expected_session:
        raise ValueError("session ID must match the batch, event or journey")
    payload = {"message_type": type(body).__name__, "contract_version": "1.0",
               "session_id": session_id, "body": asdict(body)}
    return {**payload, "message_id": domain_id("MSG", payload)}

