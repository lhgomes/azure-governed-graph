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


class DecisionModelUnavailable(RuntimeError):
    pass


class DecisionModelAbstained(DecisionModelUnavailable):
    """The model answered, but with no choice above the minimum confidence."""


# Routing reasons that hand the turn to a person or refuse advice; an unclear reading between these and anything
# else is never resolved by margin, so it still fails closed.
PROTECTIVE_REASONS = frozenset({"explicit_human_request", "personal_choice_or_comparison"})
MARGIN_FLOOR, MARGIN_RATIO, PROTECTIVE_CEILING = 0.35, 1.25, 0.10


def _clear_margin(label: str, probabilities: dict[str, float]) -> bool:
    """A routing reading at or below 0.5 is still usable when it clearly leads and no protective route is near."""
    ranked = sorted(probabilities.items(), key=lambda item: -item[1])
    if len(ranked) < 2 or ranked[0][0] != label:
        return False
    (top, top_probability), (_, runner_up) = ranked[0], ranked[1]
    protective = sum(probabilities.get(reason, 0) for reason in PROTECTIVE_REASONS)
    return (top_probability >= MARGIN_FLOOR and top_probability >= MARGIN_RATIO * runner_up
            and top not in PROTECTIVE_REASONS and protective <= PROTECTIVE_CEILING)


def choose(tools, state: dict | str, questions: dict, optional: frozenset[str] = frozenset()) -> dict[str, str | None]:
    """One Decision request. An `optional` question that is answered without enough confidence returns None;
    any other unclear or invalid answer raises, so the caller fails closed."""
    project = re.fullmatch(
        r"(https://[a-z0-9-]+\.services\.ai\.azure\.com)/api/projects/[A-Za-z0-9_-]+/?",
        settings.project_endpoint,
    )
    deployment = settings.decision_deployment
    if not project or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", deployment):
        raise DecisionModelUnavailable("invalid Decision model configuration")
    endpoint = project[1] + "/providers/microsoft/v1/systemone"
    event_id = uuid4().hex
    usage_recorded = False
    failure = "credential"
    question_id = ""
    http_status = 0
    margin: dict = {}
    request_body = {"model": deployment,
                    "state": state if isinstance(state, str) else json.dumps(state, ensure_ascii=False),
                    "questions": questions}
    raw_response = ""
    try:
        token = connections.credential().get_token("https://cognitiveservices.azure.com/.default")
        def request():
            nonlocal failure, http_status, raw_response
            failure = "transport"
            http_status = 0
            response = connections.http_client().post(
                endpoint,
                headers={**headers(tools.journey.journey_id, tools.trace_parent),
                         "Authorization": f"Bearer {token.token}"},
                json=request_body,
                timeout=10,
            )
            http_status = response.status_code
            raw_response = response.text
            failure = "http_status"
            response.raise_for_status()
            return response

        with tools.trace.measure("foundry_requests"):
            response = create_with_retry(request, tools)
        failure = "response_json"
        body = response.json()
        failure = "usage"
        usage = body["usage"]
        counts = (usage["input_tokens"], usage["output_tokens"])
        if not all(type(count) is int and count >= 0 for count in counts):
            raise ValueError("invalid Decision usage")
        emit("model_usage", event_id=f"decision:{event_id}", journey_id=tools.journey.journey_id,
             agent_name="microsoft-decision-1.1", model_deployment=deployment, usage_complete=True,
             input_tokens=counts[0], cached_input_tokens=0, output_tokens=counts[1])
        usage_recorded = True
        failure = "response_model"
        if body["model"].lower() != "microsoft-decision-1.1":
            raise ValueError("unexpected Decision model")
        failure = "answer_keys"
        answers = body["answers"]
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("unexpected Decision answer keys")
        selected = {}
        for key, question in questions.items():
            question_id = key
            failure = "answer_choice"
            answer = answers[key]
            label = answer["choice"]
            if answer["type"] != "choice" or label not in question["criteria"]:
                raise ValueError("invalid Decision choice")
            failure = "answer_probability"
            probability = answer["probabilities"][label]
            if type(probability) not in (int, float) or not 0 <= probability <= 1:
                raise ValueError("invalid Decision probability")
            failure = "low_confidence"
            if probability <= 0.5:
                ranked = sorted(answer["probabilities"].items(), key=lambda item: -item[1])[:2]
                unclear = {"top_choice": ranked[0][0], "top_probability": ranked[0][1]}
                if len(ranked) == 2:
                    unclear.update(runner_up=ranked[1][0], runner_up_probability=ranked[1][1])
                if key == "reason" and _clear_margin(label, answer["probabilities"]):
                    log.info("decision_choice_accepted_on_margin", extra={
                        "journey_id": tools.journey.journey_id, "agent_name": "microsoft-decision-1.1",
                        "decision_question": key, **unclear,
                    })
                    selected[key] = label
                    continue
                if key in optional:
                    # The caller can proceed without this answer; record that it was unclear, not a failure.
                    log.info("decision_choice_unclear", extra={
                        "journey_id": tools.journey.journey_id, "agent_name": "microsoft-decision-1.1",
                        "decision_question": key, **unclear,
                    })
                    selected[key] = None
                    continue
                margin = unclear
                raise ValueError("Decision choice below minimum confidence")
            selected[key] = label
    except (AzureError, httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as error:
        log.error("decision_model_failed", extra={
            "event_id": f"decision:{event_id}", "journey_id": tools.journey.journey_id,
            "agent_name": "microsoft-decision-1.1", "model_deployment": deployment,
            "failure_domain": failure, "error_type": type(error).__name__,
            "http_status": http_status, "decision_question": question_id, **margin,
        })
        if not usage_recorded:
            emit("model_usage_missing", event_id=f"decision:{event_id}", journey_id=tools.journey.journey_id,
                 agent_name="microsoft-decision-1.1", model_deployment=deployment, usage_complete=False)
        llm_log.record(tools.journey.journey_id, "decision", deployment, request_body, raw_response, outcome=failure)
        raise (DecisionModelAbstained if failure == "low_confidence" else DecisionModelUnavailable)(
            "Decision model unavailable or abstained") from error
    llm_log.record(tools.journey.journey_id, "decision", deployment, request_body, raw_response)
    return selected
