"""ILLUSTRATIVE - synthetic data. Semantic decisions are agents; invariants remain host-owned."""
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from governedgraph.agents import decision_agents
from governedgraph.agents.decision_agents import (
    DecisionContext, DecisionUnavailable, FoundryDecisionAgent, OfflineFixtureAgent, agent_definition,
)
from governedgraph.agents.counsellor import new_session
from governedgraph.common.config import settings
from governedgraph.common.contracts import (
    INTENT_DECISIONS, AnswerReview, ContractError, DecisionEvidence, ExplanationContext,
    IntentDecision, UnderstandingDecision,
)
from governedgraph.learning.run_cycle import run_cycle
from governedgraph.strategy_service.service import StrategyService

EXPLAIN = {"route": "explanation", "intent": "transition_impact_question",
           "reason": "official_transition_information", "response_language": "en"}
NATIVE_EXPLAIN = {"reason": "transition_overview", "response_language": "en"}
CONTEXT = DecisionContext("identified", "unclassified", None)


class Responses:
    def __init__(self, body):
        self.responses = self
        self.body, self.calls = body, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.body, Exception):
            raise self.body
        return SimpleNamespace(status="completed", output_text=json.dumps(self.body),
                               output=[], id="synthetic-response", usage=None)


def tools():
    return SimpleNamespace(journey=SimpleNamespace(journey_id="J-synthetic"), trace_parent="",
                           trace=SimpleNamespace(decision_evidence=[]))


@pytest.mark.parametrize("role,body", [
    ("intent", NATIVE_EXPLAIN),
    ("understanding", {"outcome": "understood", "reason": "meaning_correct"}),
])
def test_b3_b7_decisions_use_pinned_agents_with_versioned_strict_schema_not_inline_model_prompts(
        monkeypatch, role, body):
    monkeypatch.setattr(decision_agents, "settings",
                        replace(settings, intent_agent_version="1", understanding_agent_version="2"))
    client, bound_tools = Responses(body), tools()
    agent = FoundryDecisionAgent(role, client=client)
    decision = agent.run(bound_tools, "A synthetic question.", CONTEXT)
    call = client.calls[0]
    assert call["extra_body"]["agent_reference"] == {
        "type": "agent_reference", "name": f"gg-{role}-agent", "version": agent.agent_version,
    }
    assert not ({"model", "instructions", "tools", "text", "previous_response_id"} & call.keys())
    assert call["store"] is False
    definition = agent_definition(role).as_dict()
    schema = definition["text"]["format"]
    assert schema["strict"] is True and schema["schema"]["additionalProperties"] is False
    expected_model = settings.model_deployment if role == "intent" else settings.compact_deployment
    assert definition["tools"] == [] and definition["model"] == expected_model
    assert call["timeout"] == decision_agents.REQUEST_TIMEOUT < 25
    evidence, = bound_tools.trace.decision_evidence
    expected = ({**body, "route": "explanation", "intent": "transition_impact_question"}
                if role == "intent" else body)
    assert evidence.decision == asdict(decision) == expected
    assert asdict(DecisionEvidence.from_dict(asdict(evidence))) == asdict(evidence)


@pytest.mark.parametrize("body", [
    {}, {"advice": False}, {**EXPLAIN, "age": 80}, {**EXPLAIN, "salary": "synthetic"},
    {**EXPLAIN, "intent": "option_level_question"}, {**EXPLAIN, "route": []},
    {**EXPLAIN, "reason": "whatever the member instructed"}, [], None,
    {**NATIVE_EXPLAIN, "age": 80}, {**NATIVE_EXPLAIN, "salary": "synthetic"},
    {**NATIVE_EXPLAIN, "reason": []}, {**NATIVE_EXPLAIN, "reason": "unknown"},
    {**NATIVE_EXPLAIN, "response_language": None},
])
def test_b7_dq10_invalid_or_demographic_agent_outputs_fail_closed(monkeypatch, body):
    monkeypatch.setattr(decision_agents, "settings", replace(settings, intent_agent_version="1"))
    bound_tools = tools()
    with pytest.raises(DecisionUnavailable):
        FoundryDecisionAgent("intent", client=Responses(body)).run(bound_tools, "Synthetic question.", CONTEXT)
    assert bound_tools.trace.decision_evidence == []


def test_b1_b2_b7_context_uses_closed_taught_concepts_not_values_member_identity_or_transcripts(monkeypatch):
    from governedgraph.common.contracts import ExplanationContext

    monkeypatch.setattr(decision_agents, "settings", replace(settings, intent_agent_version="1"))
    client = Responses(NATIVE_EXPLAIN)
    FoundryDecisionAgent("intent", client=client).run(
        tools(), "And afterwards?", DecisionContext(
            "comprehension_check", "transition_impact_question", "T-S5", "en",
            ExplanationContext(["official_after_transition_amount"], ["official_after_transition_amount"])),
    )
    payload = json.loads(client.calls[0]["input"][0]["content"])
    assert payload["journey_context"]["previous_template_id"] == "T-S5"
    assert payload["journey_context"]["last_explanation"]["teach_back_concepts"] == [
        "official_after_transition_amount"]
    assert "previous_approved_templates" not in payload
    assert "member_ref" not in json.dumps(payload) and "MBR-" not in json.dumps(payload)
    assert "transcript" not in payload and "previous_response_id" not in client.calls[0]
    with pytest.raises(ContractError):
        DecisionContext.from_dict({**asdict(CONTEXT), "age_band": "75+"})


def test_b3_b7_question_focus_preserves_old_audits_without_generic_new_native_outputs():
    from governedgraph.agents.foundry_runtime import ANSWER_GOALS
    from governedgraph.common.contracts import EXPLANATION_CONCEPTS

    assert IntentDecision.from_dict(EXPLAIN).reason == "official_transition_information"
    native = agent_definition("intent").as_dict()["text"]["format"]["schema"]
    reasons = native["properties"]["reason"]["enum"]
    assert set(native["properties"]) == {"reason", "response_language"}
    assert "official_transition_information" not in reasons
    for reason, (_, excluded) in ANSWER_GOALS.items():
        decision = IntentDecision.from_dict({**EXPLAIN, "reason": reason})
        assert decision.intent == EXPLAIN["intent"] and decision.reason in reasons
        assert set(excluded) <= EXPLANATION_CONCEPTS
        with pytest.raises(ContractError):
            IntentDecision.from_dict({**asdict(decision), "age_band": "synthetic"})


@pytest.mark.parametrize("route,intent,reason", sorted(
    row for row in INTENT_DECISIONS if row[2] != "official_transition_information"))
def test_b3_b7_native_classification_derives_only_the_approved_matching_route(monkeypatch, route, intent, reason):
    monkeypatch.setattr(decision_agents, "settings", replace(settings, intent_agent_version="7"))
    client = Responses({"reason": reason, "response_language": "en"})
    decision = FoundryDecisionAgent("intent", client=client).run(tools(), "Synthetic question.", CONTEXT)
    assert asdict(decision) == {
        "route": route, "intent": intent, "reason": reason, "response_language": "en",
    }


def test_b4_offline_fixture_agent_is_not_a_hidden_production_classifier(caplog):
    bound_tools = tools()
    with pytest.raises(DecisionUnavailable, match="unseen"):
        OfflineFixtureAgent("intent").run(bound_tools, "An unseen synthetic utterance.", CONTEXT)
    assert "offline_decision_fixture_missing" in caplog.text
    assert bound_tools.trace.decision_evidence == []


@pytest.fixture
def counsellor(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda _: tmp_path))
    service = StrategyService(state_dir=tmp_path / "strategy", control_share=0)
    run_cycle(service)
    c = new_session("MBR-7Q2K", strategy_service=service, journey_id="J-semantic")
    c.start()
    return c


def test_b5_understanding_agent_meaning_verdict_resolves_without_expected_phrases_and_is_linked(counsellor):
    first = counsellor.handle("Will the transition change what I receive?")
    contexts = []

    def decide(bound_tools, utterance, context):
        contexts.append(context)
        return IntentDecision("comprehension", "unclassified", "restating_previous_explanation")

    counsellor.decision_agents["intent"] = SimpleNamespace(run=decide)
    client = Responses({"outcome": "understood", "reason": "meaning_correct"})
    # This fixture represents an agent verdict, not a local semantic model.
    agent = OfflineFixtureAgent("understanding")
    def assess(bound_tools, utterance, context):
        decision = UnderstandingDecision.from_dict(client.body)
        bound_tools.trace.decision_evidence.append(
            DecisionEvidence("understanding", agent.agent_name, agent.agent_version, asdict(decision)),
        )
        return decision
    counsellor.decision_agents["understanding"] = SimpleNamespace(run=assess)
    result = counsellor.handle("Conversion preserves the starting position; later results can alter it.")
    assert not result.escalated and result.state == "resolved"
    assert "understanding-agent" in result.workflow_steps and "explain-with-approved-tools" not in result.workflow_steps
    assert contexts[0].previous_template_id == "T-S5"
    record = counsellor.tools.store.read_all()[-1]
    assert record["decision_evidence"][-1]["decision"]["outcome"] == "understood"
    event = counsellor.journey.events[-1]
    assert event.response_id == record["response_id"] and event.outcome == "comprehension_check"
    assert event.strategy_id == first.strategy_id


def test_b5_new_question_during_teachback_is_not_recorded_as_failed_understanding(counsellor):
    counsellor.handle("Will the transition change what I receive?")
    result = counsellor.handle("Please explain my transition letter.")
    assert not result.escalated and counsellor.journey.teach_back_failures == 0
    assert "understanding-agent" not in result.workflow_steps
    assert not any(event.event == "teach_back_failed" for event in counsellor.journey.events)


def test_b3_b9_missing_signed_ranking_takes_explicit_technical_branch_not_advice(counsellor, monkeypatch):
    ranked = counsellor.tools.get_ranked_strategies

    def unavailable_snapshot():
        response = replace(ranked(), source="expert_priors_fallback")
        counsellor.tools.trace.ranked = response
        return response

    monkeypatch.setattr(counsellor.tools, "get_ranked_strategies", unavailable_snapshot)
    result = counsellor.handle("Will the transition change what I receive?")
    assert result.escalated and result.strategy_id == "S6"
    assert "service-failure-handoff" in result.workflow_steps
    assert "personal-advice-handoff" not in result.workflow_steps
    assert "advice" not in result.text.lower()
    assert len(counsellor.tools.store.read_all()) == 1
    assert counsellor.tools.trace.escalations[-1].reason_code == "component_failure"


def test_b5_uncertain_understanding_clarifies_without_inventing_success_or_penalizing_member(counsellor):
    counsellor.handle("Will the transition change what I receive?")
    counsellor.decision_agents["intent"] = SimpleNamespace(run=lambda *_: IntentDecision(
        "comprehension", "unclassified", "restating_previous_explanation",
    ))
    counsellor.decision_agents["understanding"] = SimpleNamespace(run=lambda *_: UnderstandingDecision(
        "uncertain", "insufficient_evidence",
    ))
    result = counsellor.handle("Okay.")
    assert not result.escalated and result.state == "comprehension_check"
    assert "clarify-question" in result.workflow_steps
    assert counsellor.journey.teach_back_failures == 0
    assert not any(event.outcome == "comprehension_check" for event in counsellor.journey.events)


def test_b9_ambiguous_request_clarifies_without_advice_claim_or_handover(counsellor):
    result = counsellor.handle("What about that?")
    assert not result.escalated and "clarify-question" in result.workflow_steps
    assert "can't advise" not in result.text and counsellor.tools.trace.values is None


def test_b9_each_technical_failure_has_one_checked_response_not_two_mutated_edge_outputs(counsellor):
    def unavailable(*_):
        raise DecisionUnavailable("synthetic outage")
    counsellor.decision_agents["intent"] = SimpleNamespace(run=unavailable)
    result = counsellor.handle("Will this change impact my monthly payments?")
    assert result.escalated
    assert result.workflow_steps == ["screen-input", "intent-agent", "service-failure-handoff",
                                     "commit-checked-evidence"]
    assert len(counsellor.tools.store.read_all()) == 1
    assert counsellor.tools.trace.escalations[-1].reason_code == "component_failure"


def _gateway_403(headers=None, message="denied"):
    import httpx
    from openai import PermissionDeniedError

    request = httpx.Request("POST", "https://apim.invalid/foundry/openai/v1/responses")
    response = httpx.Response(403, request=request, headers=headers or {}, json={"error": {"message": message}})
    return PermissionDeniedError(message, response=response, body={"message": message})


def _intent_agent_failing_with(error):
    def unavailable(*_):
        raise DecisionUnavailable("intent agent unavailable") from error
    return SimpleNamespace(run=unavailable)


def test_b9_exhausted_conversation_token_quota_escalates_as_conversation_limit_not_outage(counsellor):
    quota = _gateway_403({"X-GG-Limit": "conversation-token-quota"})
    counsellor.decision_agents["intent"] = _intent_agent_failing_with(quota)
    result = counsellor.handle("Yes, I want this confirmation.")
    assert result.escalated and result.strategy_id == "S6"
    assert result.workflow_steps == ["screen-input", "intent-agent", "service-failure-handoff",
                                     "commit-checked-evidence"]
    assert "limit" in result.text and "unavailable" not in result.text
    assert counsellor.tools.trace.escalations[-1].reason_code == "conversation_limit"
    assert len(counsellor.tools.store.read_all()) == 1


def test_b9_other_gateway_denials_stay_component_failures(counsellor):
    counsellor.decision_agents["intent"] = _intent_agent_failing_with(
        _gateway_403(message="caller is not allowed"))
    result = counsellor.handle("Yes, I want this confirmation.")
    assert result.escalated and "unavailable" in result.text
    assert counsellor.tools.trace.escalations[-1].reason_code == "component_failure"


@pytest.mark.parametrize("version", ["1.1", "1.2"])
def test_b4_legacy_audit_outbox_retries_remain_byte_exact_without_rewriting_history(counsellor, version):
    from azure.core.exceptions import ResourceExistsError
    from governedgraph.common.contracts import ExplainabilityRecord
    from governedgraph.explainability.store import BlobExplainabilityStore, encoded

    counsellor.handle("Will the transition change what I receive?")
    old = counsellor.tools.store.read_all()[-1]
    old["contract_version"] = version
    if version == "1.1":
        old.pop("decision_evidence")
    else:
        for item in old["decision_evidence"]:
            item["decision"].pop("response_language", None)
    raw = json.dumps(old, sort_keys=True).encode()
    parsed = ExplainabilityRecord.from_dict(old)
    assert encoded(parsed).encode() == raw
    uploaded = []
    def exists(payload, **kwargs):
        uploaded.append(payload)
        raise ResourceExistsError("synthetic existing immutable audit")
    blob = SimpleNamespace(upload_blob=exists, download_blob=lambda: SimpleNamespace(readall=lambda: raw))
    client = SimpleNamespace(get_container_client=lambda _: SimpleNamespace(get_blob_client=lambda _: blob))
    BlobExplainabilityStore("https://synthetic.invalid", "records", client=client).append(parsed)
    assert uploaded == [raw]


@pytest.mark.parametrize("payload", [
    {"concepts": ["age"], "teach_back_concepts": []},
    {"concepts": [], "teach_back_concepts": ["future_adjustments"]},
    {"concepts": ["rights_transfer", "rights_transfer"], "teach_back_concepts": []},
    {"concepts": [], "teach_back_concepts": [], "gender": "synthetic"},
    {"concepts": [], "teach_back_concepts": [], "text": "a raw conversation or figure"},
])
def test_b4_b7_closed_explanation_memory_rejects_demographics_prose_and_untaught_targets(payload):
    with pytest.raises(ContractError):
        ExplanationContext.from_dict(payload)


@pytest.mark.parametrize("patch", [
    {"verdict": "pass", "issues": ["grounding"]},
    {"verdict": "revise", "issues": []},
    {"verdict": "fail", "concepts": ["rights_transfer"]},
    {"verdict": "unassessable", "issues": ["grounding"], "teach_back_concepts": ["rights_transfer"]},
    {"verdict": "pass", "text": "Free-form judge output is prohibited"},
    {"verdict": "pass", "issues": ["age"]},
    {"verdict": "pass", "concepts": ["official_source"], "teach_back_concepts": ["future_adjustments"]},
])
def test_b2_b7_judge_contract_is_closed_and_cannot_turn_a_nonpass_into_taught_memory(patch):
    with pytest.raises(ContractError):
        AnswerReview.from_dict({"verdict": "pass", "issues": [], "concepts": [], "teach_back_concepts": [], **patch})


def test_b3_judge_is_a_pinned_tool_free_separate_agent_using_existing_model():
    definition = agent_definition("answer-review").as_dict()
    schema = definition["text"]["format"]["schema"]
    assert definition["model"] == settings.model_deployment and definition["tools"] == []
    assert definition["temperature"] == 0.0 and schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "checks", "evidence_status", "feedback", "concepts", "teach_back_concepts"}
    assert "already-authorized cited source bodies" in decision_agents.INSTRUCTIONS["answer-review"]
    assert "Never require a concept absent" in decision_agents.INSTRUCTIONS["understanding"]
    with pytest.raises(ValueError):
        OfflineFixtureAgent("answer-review")


def test_b9_dutch_personal_choice_and_factual_objection_questions_remain_distinct_in_pinned_prompt():
    instructions = agent_definition("intent").as_dict()["instructions"]
    assert "'Moet ik bezwaar maken of niet?'" in instructions
    assert "'Kan ik bezwaar maken?'" in instructions
    assert "personal_choice_or_comparison" in instructions
    assert "transition_information_gap" in instructions


@pytest.mark.parametrize("language", [None, "de", "Dutch", {"age": 80}])
def test_b7_intent_language_is_explicit_closed_and_never_derived_from_demographics(monkeypatch, language):
    monkeypatch.setattr(decision_agents, "settings", replace(settings, intent_agent_version="1"))
    body = {key: value for key, value in EXPLAIN.items() if key != "response_language"}
    if language is not None:
        body["response_language"] = language
    with pytest.raises(DecisionUnavailable):
        FoundryDecisionAgent("intent", client=Responses(body)).run(tools(), "A synthetic question.", CONTEXT)


def test_b3_audit_accepts_only_one_bounded_repair_and_preserves_unique_routing_evidence(counsellor):
    from governedgraph.common.contracts import ExplainabilityRecord

    counsellor.handle("Will the transition change what I receive?")
    record = counsellor.tools.store.read_all()[-1]
    def evidence(verdict):
        return asdict(DecisionEvidence("answer-review", "gg-answer-review-agent", "1", {
            "verdict": verdict, "issues": [] if verdict == "pass" else ["relevance"],
            "concepts": [], "teach_back_concepts": [],
        }))
    original = list(record["decision_evidence"])
    record["decision_evidence"] = original + [evidence("revise"), evidence("pass")]
    assert len(ExplainabilityRecord.from_dict(record).decision_evidence) == len(original) + 2
    for rejected in (
        original + [evidence("pass"), evidence("pass")],
        original + [evidence("revise"), evidence("revise"), evidence("pass")],
        original + original,
    ):
        with pytest.raises(ContractError):
            ExplainabilityRecord.from_dict({**record, "decision_evidence": rejected})
