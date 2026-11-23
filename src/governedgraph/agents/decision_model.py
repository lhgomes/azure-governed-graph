"""Bounded Microsoft Decision API calls on the runtime's existing Foundry account."""
from __future__ import annotations

import json
import logging
import re
from uuid import uuid4

import httpx
from azure.core.exceptions import AzureError

from governedgraph.agents import connections, llm_log
from governedgraph.agents.rate_limit import create_with_retry
from governedgraph.common.config import settings
from governedgraph.observability import emit, headers

log = logging.getLogger(__name__)

# A required answer is accepted above MIN_CONFIDENCE. An optional answer (the concept readings, which can veto a
# draft) needs a clearer reading: a weak one is treated as unclear, not as evidence.
MIN_CONFIDENCE = 0.3
OPTIONAL_MIN_CONFIDENCE = 0.6


class DecisionModelUnavailable(RuntimeError):
    pass


class DecisionModelAbstained(DecisionModelUnavailable):
    """The model answered, but with no choice above the minimum confidence."""


def _role(questions: dict) -> str:
    """The model-call log name of a request: `decision_<question>`, so each call is readable on its own."""
    first = next(iter(questions))
    return "decision_" + ("concepts" if first.startswith("concept_") else first)


class _Call:
    """What a failure log needs to know about one request: the step reached and what was seen there."""

    def __init__(self):
        self.event_id = uuid4().hex
        self.stage = "credential"
        self.http_status = 0
        self.raw_response = ""
        self.question = ""
        self.margin: dict = {}
        self.usage_recorded = False


def _decision_endpoint() -> tuple[str, str]:
    project = re.fullmatch(
        r"(https://[a-z0-9-]+\.services\.ai\.azure\.com)/api/projects/[A-Za-z0-9_-]+/?", settings.project_endpoint)
    deployment = settings.decision_deployment
    if not project or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", deployment):
        raise DecisionModelUnavailable("invalid Decision model configuration")
    return project[1] + "/providers/microsoft/v1/systemone", deployment


def _send(tools, endpoint: str, request_body: dict, call: _Call) -> dict:
    """POST the request (retrying throttling) and return the JSON body."""
    token = connections.credential().get_token("https://cognitiveservices.azure.com/.default")

    def request():
        call.stage, call.http_status = "transport", 0
        response = connections.http_client().post(
            endpoint,
            headers={**headers(tools.journey.journey_id, tools.trace_parent), "Authorization": f"Bearer {token.token}"},
            json=request_body, timeout=10)
        call.http_status, call.raw_response = response.status_code, response.text
        call.stage = "http_status"
        response.raise_for_status()
        return response

    with tools.trace.measure("foundry_requests"):
        response = create_with_retry(request, tools)
    call.stage = "response_json"
    return response.json()


def _record_usage(tools, body: dict, deployment: str, call: _Call) -> None:
    call.stage = "usage"
    usage = body["usage"]
    counts = (usage["input_tokens"], usage["output_tokens"])
    if not all(type(count) is int and count >= 0 for count in counts):
        raise ValueError("invalid Decision usage")
    emit("model_usage", event_id=f"decision:{call.event_id}", journey_id=tools.journey.journey_id,
         agent_name="microsoft-decision-1.1", model_deployment=deployment, usage_complete=True,
         input_tokens=counts[0], cached_input_tokens=0, output_tokens=counts[1])
    call.usage_recorded = True
    call.stage = "response_model"
    if body["model"].lower() != "microsoft-decision-1.1":
        raise ValueError("unexpected Decision model")


def _read_choices(tools, body: dict, questions: dict, optional: frozenset[str], call: _Call) -> dict[str, str | None]:
    """The label chosen for every question; a label at or below the confidence gate is refused (None if optional)."""
    call.stage = "answer_keys"
    answers = body["answers"]
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("unexpected Decision answer keys")
    selected = {}
    for key, question in questions.items():
        call.question = key
        call.stage = "answer_choice"
        answer = answers[key]
        label = answer["choice"]
        if answer["type"] != "choice" or label not in question["criteria"]:
            raise ValueError("invalid Decision choice")
        call.stage = "answer_probability"
        probability = answer["probabilities"][label]
        if type(probability) not in (int, float) or not 0 <= probability <= 1:
            raise ValueError("invalid Decision probability")
        call.stage = "low_confidence"
        if probability > (OPTIONAL_MIN_CONFIDENCE if key in optional else MIN_CONFIDENCE):
            selected[key] = label
            continue
        ranked = sorted(answer["probabilities"].items(), key=lambda item: -item[1])[:2]
        unclear = {"top_choice": ranked[0][0], "top_probability": ranked[0][1]}
        if len(ranked) == 2:
            unclear.update(runner_up=ranked[1][0], runner_up_probability=ranked[1][1])
        if key not in optional:
            call.margin = unclear
            raise ValueError("Decision choice below minimum confidence")
        # The caller can proceed without this answer; record that it was unclear, not a failure.
        log.info("decision_choice_unclear", extra={
            "journey_id": tools.journey.journey_id, "agent_name": "microsoft-decision-1.1",
            "decision_question": key, **unclear})
        selected[key] = None
    return selected


def choose(tools, state: dict | str, questions: dict, optional: frozenset[str] = frozenset()) -> dict[str, str | None]:
    """One Decision request. An `optional` question that is answered without enough confidence returns None;
    any other unclear or invalid answer raises, so the caller fails closed."""
    endpoint, deployment = _decision_endpoint()
    call = _Call()
    request_body = {"model": deployment,
                    "state": state if isinstance(state, str) else json.dumps(state, ensure_ascii=False),
                    "questions": questions}
    journey_id = tools.journey.journey_id
    try:
        body = _send(tools, endpoint, request_body, call)
        _record_usage(tools, body, deployment, call)
        selected = _read_choices(tools, body, questions, optional, call)
    except (AzureError, httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as error:
        log.error("decision_model_failed", extra={
            "event_id": f"decision:{call.event_id}", "journey_id": journey_id,
            "agent_name": "microsoft-decision-1.1", "model_deployment": deployment,
            "failure_domain": call.stage, "error_type": type(error).__name__,
            "http_status": call.http_status, "decision_question": call.question, **call.margin})
        if not call.usage_recorded:
            emit("model_usage_missing", event_id=f"decision:{call.event_id}", journey_id=journey_id,
                 agent_name="microsoft-decision-1.1", model_deployment=deployment, usage_complete=False)
        llm_log.record(journey_id, _role(questions), deployment, request_body, call.raw_response, outcome=call.stage)
        raise (DecisionModelAbstained if call.stage == "low_confidence" else DecisionModelUnavailable)(
            "Decision model unavailable or abstained") from error
    llm_log.record(journey_id, _role(questions), deployment, request_body, call.raw_response)
    return selected
