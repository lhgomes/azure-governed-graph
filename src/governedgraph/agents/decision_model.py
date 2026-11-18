"""Bounded Microsoft Decision API calls on the runtime's existing Foundry account."""
from __future__ import annotations

import json
import re
from uuid import uuid4

import httpx
from azure.core.exceptions import AzureError

from governedgraph.agents import connections
from governedgraph.common.config import settings
from governedgraph.observability import emit, headers


class DecisionModelUnavailable(RuntimeError):
    pass


def choose(tools, state: dict, questions: dict) -> dict[str, str]:
    project = re.fullmatch(
        r"(https://[a-z0-9-]+\.services\.ai\.azure\.com)/api/projects/[A-Za-z0-9_-]+/?",
        settings.project_endpoint,
    )
    deployment = settings.decision_deployment
    if not project or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", deployment):
        raise DecisionModelUnavailable("invalid Decision model configuration")
    endpoint = project[1] + "/providers/microsoft/v1/systemone"
    event_id = uuid4().hex
    try:
        token = connections.credential().get_token("https://cognitiveservices.azure.com/.default")
        with tools.trace.measure("foundry_requests"):
            response = connections.http_client().post(
                endpoint,
                headers={**headers(tools.journey.journey_id, tools.trace_parent),
                         "Authorization": f"Bearer {token.token}"},
                json={"model": deployment, "state": json.dumps(state, ensure_ascii=False), "questions": questions},
                timeout=10,
            )
        response.raise_for_status()
        body = response.json()
        if body["model"].lower() != "microsoft-decision-1.1":
            raise ValueError("unexpected Decision model")
        usage = body["usage"]
        counts = (usage["input_tokens"], usage["output_tokens"])
        if not all(type(count) is int and count >= 0 for count in counts):
            raise ValueError("invalid Decision usage")
        answers = body["answers"]
        if set(answers) != set(questions):
            raise ValueError("unexpected Decision answer keys")
        selected = {}
        for key, question in questions.items():
            answer = answers[key]
            label = answer["choice"]
            if answer["type"] != "choice" or label not in question["criteria"]:
                raise ValueError("invalid Decision choice")
            probability = answer["probabilities"][label]
            if type(probability) not in (int, float) or not 0.5 < probability <= 1:
                raise ValueError("Decision choice below minimum confidence")
            selected[key] = label
    except (AzureError, httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as error:
        emit("model_usage_missing", event_id=f"decision:{event_id}", journey_id=tools.journey.journey_id,
             agent_name="microsoft-decision-1.1", model_deployment=deployment, usage_complete=False)
        raise DecisionModelUnavailable("Decision model unavailable or abstained") from error
    emit("model_usage", event_id=f"decision:{event_id}", journey_id=tools.journey.journey_id,
         agent_name="microsoft-decision-1.1", model_deployment=deployment, usage_complete=True,
         input_tokens=counts[0], cached_input_tokens=0, output_tokens=counts[1])
    return selected
