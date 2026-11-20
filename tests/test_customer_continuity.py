"""ILLUSTRATIVE - synthetic data: customer continuity without network dependencies."""
from contextlib import contextmanager, nullcontext
import json
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from governedgraph.agents.conversation_context import ConversationMessage, reference_messages, screening_batches
from governedgraph.agents.counsellor import TurnResult
from governedgraph.agents.tools import Trace
from governedgraph.common.contracts import ContractError
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.explainability.store import AuditUnavailable
from governedgraph.orchestrator.continuity import ContinuityUnavailable, CustomerConversation, CustomerConversations
from governedgraph.orchestrator.service import DeliveryPending, MemberScopeViolation, OrchestratorService
from governedgraph.orchestrator.state_machine import Event
from governedgraph.orchestrator.store import BlobJourneyStore, JourneyBusy, JourneyRecord, Transaction

MEMBER = MemberIdentity("SYNTHETIC-CUSTOMER", True)
OTHER = MemberIdentity("SYNTHETIC-OTHER", True)


@pytest.fixture(autouse=True)
def isolated_observation(monkeypatch):
    for module in ("orchestrator.service", "agents.tools", "agents.decision_agents"):
        monkeypatch.setattr(f"governedgraph.{module}.operation", lambda *args, **kwargs: nullcontext(None))
    monkeypatch.setattr("governedgraph.orchestrator.service.emit", Mock())
    monkeypatch.setattr("governedgraph.agents.foundry_tracing.record_synthetic_conversation", Mock())
    monkeypatch.setattr("governedgraph.observability.model_usage", Mock())


class MemoryRecords:
    def __init__(self, decoder):
        self.decoder, self.rows, self.locks = decoder, {}, {}
        self.fail = lambda key, text: None

    @contextmanager
    def transaction(self, journey_id: str):
        lock = self.locks.setdefault(journey_id, Lock())
        if not lock.acquire(blocking=False):
            raise JourneyBusy(journey_id)
        try:
            def write(text):
                self.fail(journey_id, text)
                self.rows[journey_id] = text
            yield Transaction(self.decoder(self.rows[journey_id]) if journey_id in self.rows else None, write)
        finally:
            lock.release()

    def ids(self):
        return iter(self.rows)


class FoundryConversationsDouble:
    def __init__(self):
        self.rows, self.content = {}, {}
        self.sequence = 0
        self.fail_append = False
        self.partial_append = False
        self.items = SimpleNamespace(create=self.append, list=self.list_items, delete=self.delete)

    def create(self, *, metadata):
        conversation_id = f"conv-{len(self.rows)}"
        self.rows[conversation_id] = SimpleNamespace(id=conversation_id, metadata=metadata.copy())
        self.content[conversation_id] = []
        return self.rows[conversation_id]

    def retrieve(self, conversation_id):
        return self.rows[conversation_id]

    def append(self, conversation_id, *, items):
        created = []
        for item in items:
            self.sequence += 1
            message = SimpleNamespace(
                id=f"item-{self.sequence}", type="message", role=item["role"],
                content=[SimpleNamespace(type="input_text", text=item["content"])],
            )
            self.content[conversation_id].append(message)
            created.append(message)
            if self.partial_append:
                self.partial_append = False
                break
        if self.fail_append:
            self.fail_append = False
            raise OSError("synthetic crash after server-side append")
        return SimpleNamespace(data=created, has_more=False)

    def list_items(self, conversation_id, *, order, limit, after=None):
        assert order == "asc" and 1 <= limit <= 100
        rows = self.content[conversation_id]
        if after is not None:
            rows = rows[next(i for i, item in enumerate(rows) if item.id == after) + 1:]
        return SimpleNamespace(data=rows[:limit], has_more=len(rows) > limit)

    def delete(self, item_id, *, conversation_id):
        rows = self.content[conversation_id]
        rows.remove(next(item for item in rows if item.id == item_id))

    def messages(self):
        return [[part.text for item in items for part in item.content] for items in self.content.values()]


@pytest.fixture
def continuity_runtime(monkeypatch):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    journeys = MemoryRecords(JourneyRecord.decode)
    references = MemoryRecords(CustomerConversation.decode)
    foundry = FoundryConversationsDouble()
    observed = []
    publisher = Mock()
    audit = Mock()
    mode = {"audit": "committed", "escalated": False}

    def factory(journey, auth_ref, **kwargs):
        tools = SimpleNamespace(trace=Trace(), conversation_history=(), response_id="")
        def handle(utterance):
            observed.append((journey.journey_id, tools.conversation_history))
            journey.fire(Event.INTENT_IN_SCOPE)
            journey.fire(Event.NO_TEACH_BACK)
            journey.record_response(tools.response_id, len(journey.events) - 2)
            if mode["audit"] == "unavailable":
                journey.withhold_response()
            return TurnResult("The current approved explanation.", journey.state.value,
                              audit_status=mode["audit"], escalated=mode["escalated"],
                              numeric_fidelity_passed=True, no_option_recommendation=True)
        return SimpleNamespace(tools=tools, handle=handle)

    def runtime():
        return OrchestratorService(
            journeys, publisher, Mock(side_effect=factory), audit_store=audit,
            continuity=CustomerConversations(references, foundry, identity_namespace="synthetic-authority"),
        )
    return SimpleNamespace(runtime=runtime, journeys=journeys, references=references,
                           foundry=foundry, observed=observed, publisher=publisher, mode=mode)


def begin(case, identity=MEMBER, *, channel="chat", contact_type="first_contact", test_strategy_id=""):
    return case.runtime().start(identity, channel, "standard", contact_type=contact_type,
                                test_strategy_id=test_strategy_id)["journey_id"]


def say(case, journey, request, identity=MEMBER, text="Please explain this remaining concern.", **kwargs):
    return case.runtime().turn(journey, request, identity, "synthetic-token-never-retained", text, **kwargs)


@pytest.mark.parametrize("contact_type,channel", [("second_contact", "voice"), ("first_contact", "chat")])
def test_b9_returning_greeting_recovers_customer_context_before_any_new_turn(continuity_runtime, contact_type, channel):
    from governedgraph.guardrails.numeric_fidelity import check

    case = continuity_runtime
    first = begin(case)
    say(case, first, "first")
    earlier = case.foundry.messages()
    result = case.runtime().start(MEMBER, channel, "standard", contact_type=contact_type)
    assert "Hello again!" in result["text"] and "earlier conversation available" in result["text"]
    assert "AI pension guide" in result["text"] and "person at any time" in result["text"]
    assert "Where would you like to start?" not in result["text"]
    assert check(result["text"], None, []).passed
    assert len(case.observed) == 1 and len(case.foundry.rows) == 1 and case.foundry.messages() == earlier
    audit = case.runtime().audit_store.append.call_args.args[0]
    assert audit.journey_id == result["journey_id"] and audit.disclosure_given and audit.escalation_offered
    assert audit.template_version == "disclosure-1.1" and audit.request_key["journey_state"] == contact_type


@pytest.mark.parametrize("contact_type", ["first_contact", "second_contact"])
def test_contact_type_selects_opening_without_inventing_customer_history(continuity_runtime, contact_type):
    case = continuity_runtime
    result = case.runtime().start(MEMBER, "chat", "standard", contact_type=contact_type)
    if contact_type == "second_contact":
        assert "Hello again!" in result["text"] and "remaining question" in result["text"]
        assert "don't have an earlier conversation available" in result["text"]
    else:
        assert "Hello, welcome!" in result["text"] and "Where would you like to start?" in result["text"]
        assert "earlier conversation" not in result["text"]
    assert "AI pension guide" in result["text"] and "person at any time" in result["text"]
    assert not case.observed and case.foundry.messages() == [[]]


@pytest.mark.parametrize("identity,test_strategy_id", [
    (OTHER, ""), (MemberIdentity(MEMBER.member_ref, False), ""), (MEMBER, "S1"),
])
def test_b7_startup_greeting_cannot_claim_another_customer_or_unverified_or_admin_history(
        continuity_runtime, identity, test_strategy_id):
    case = continuity_runtime
    first = begin(case)
    say(case, first, "first", text="The synthetic customer's earlier private question.")
    result = case.runtime().start(identity, "chat", "standard", contact_type="second_contact",
                                  test_strategy_id=test_strategy_id)
    assert "don't have an earlier conversation available" in result["text"]
    assert "private question" not in result["text"] and len(case.observed) == 1


def test_startup_discards_uncommitted_dialogue_before_choosing_a_returning_greeting(continuity_runtime):
    case = continuity_runtime
    first = begin(case)
    case.foundry.fail_append = True
    with pytest.raises(OSError):
        say(case, first, "ambiguous")
    assert len(case.foundry.messages()[0]) == 2
    result = case.runtime().start(MEMBER, "chat", "standard", contact_type="second_contact")
    assert "don't have an earlier conversation available" in result["text"]
    assert case.foundry.messages() == [[]] and len(case.observed) == 1


def test_startup_recovers_a_committed_turn_before_returning_a_greeting(continuity_runtime):
    case = continuity_runtime
    first = begin(case)
    case.publisher.send.side_effect = TimeoutError("synthetic delivery outage")
    with pytest.raises(DeliveryPending):
        say(case, first, "pending")
    before = len(case.journeys.rows)
    with pytest.raises(DeliveryPending):
        case.runtime().start(MEMBER, "chat", "standard", contact_type="second_contact")
    assert len(case.journeys.rows) == before
    case.publisher.send.side_effect = None
    result = case.runtime().start(MEMBER, "chat", "standard", contact_type="second_contact")
    assert "I have our earlier conversation available" in result["text"]
    assert len(case.observed) == 1 and len(case.foundry.messages()[0]) == 2
    with case.journeys.transaction(first) as transaction:
        assert not transaction.record.pending


def test_startup_history_failure_never_releases_a_fresh_greeting(continuity_runtime, caplog):
    case = continuity_runtime
    begin(case)
    case.foundry.retrieve = Mock(side_effect=ContinuityUnavailable("synthetic private history detail"))
    before = len(case.journeys.rows)
    case.runtime().audit_store.reset_mock()
    with pytest.raises(ContinuityUnavailable):
        case.runtime().start(MEMBER, "chat", "standard", contact_type="second_contact")
    assert len(case.journeys.rows) == before and not case.runtime().audit_store.append.called
    assert "customer_continuity_unavailable" in caplog.text and "private history detail" not in caplog.text


@pytest.mark.parametrize("failure,status", [(JourneyBusy("synthetic"), 409), (ContinuityUnavailable("synthetic"), 503)])
def test_startup_api_surfaces_busy_and_unavailable_customer_context(monkeypatch, failure, status):
    from fastapi import HTTPException
    from governedgraph.orchestrator import api

    runtime = Mock()
    runtime.start.side_effect = failure
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=runtime)))
    monkeypatch.setattr(api, "authenticate", lambda _: ("synthetic", MEMBER))
    with pytest.raises(HTTPException) as error:
        api.start(api.StartJourney(contact_type="second_contact"), request, authorization="Bearer synthetic")
    assert error.value.status_code == status


def test_customer_history_survives_runtime_reconstruction_and_a_new_contact_and_channel(continuity_runtime):
    case = continuity_runtime
    first = begin(case)
    say(case, first, "first", text="Please explain the synthetic transition letter.")
    say(case, first, "follow-up")
    second = begin(case, channel="voice", contact_type="second_contact")
    say(case, second, "return")
    history = case.observed[-1][1]
    assert [item.role for item in history] == ["user", "assistant"] * 2
    assert history[0].text == "Please explain the synthetic transition letter."
    assert len(case.foundry.rows) == 1
    encoded = "".join(case.references.rows.values()) + "".join(case.journeys.rows.values())
    assert "transition letter" not in encoded and "current approved explanation" not in encoded
    assert "synthetic-token" not in encoded


def test_customer_and_administrator_test_boundaries_cannot_share_history(continuity_runtime):
    case = continuity_runtime
    first = begin(case)
    say(case, first, "first", text="The synthetic customer's private earlier question.")
    other = begin(case, OTHER)
    say(case, other, "other", OTHER)
    assert case.observed[-1][1] == ()
    admin = begin(case, test_strategy_id="S1")
    say(case, admin, "admin", allow_admin_test=True)
    assert case.observed[-1][1] == ()
    say(case, admin, "admin-follow-up", allow_admin_test=True)
    assert len(case.observed[-1][1]) == 2
    second_admin = begin(case, test_strategy_id="S1")
    say(case, second_admin, "new-admin", allow_admin_test=True)
    assert case.observed[-1][1] == ()
    returned = begin(case, contact_type="second_contact")
    say(case, returned, "return")
    assert len(case.observed[-1][1]) == 2
    assert len(case.foundry.rows) == 4
    before = len(case.observed)
    with pytest.raises(MemberScopeViolation):
        say(case, first, "forged", OTHER)
    with pytest.raises(MemberScopeViolation):
        say(case, admin, "forged-admin")
    assert len(case.observed) == before


def test_forged_foundry_mapping_fails_before_customer_history_reaches_a_model(continuity_runtime):
    case = continuity_runtime
    journey = begin(case)
    say(case, journey, "first")
    next(iter(case.foundry.rows.values())).metadata["customer_key"] = "0" * 64
    result = say(case, journey, "forged")
    assert result["escalated"] and result["strategy_id"] == "S6"
    assert len(case.observed) == 1


def test_unverified_identity_never_reads_or_writes_customer_history(continuity_runtime):
    case = continuity_runtime
    journey = begin(case)
    say(case, journey, "verified")
    before = case.foundry.messages()
    say(case, journey, "not-verified", MemberIdentity(MEMBER.member_ref, False))
    assert case.observed[-1][1] == ()
    assert case.foundry.messages() == before


def test_identical_customer_references_from_another_identity_authority_do_not_share_context(continuity_runtime):
    case = continuity_runtime
    journey = begin(case)
    say(case, journey, "original")
    runtime = case.runtime()
    runtime.continuity = CustomerConversations(case.references, case.foundry,
                                               identity_namespace="different-synthetic-authority")
    runtime.turn(journey, "other-authority", MEMBER, "", "A synthetic question.")
    assert case.observed[-1][1] == () and len(case.foundry.rows) == 2


def test_overlapping_contacts_for_one_customer_are_busy_not_interleaved(continuity_runtime):
    case = continuity_runtime
    first, second = begin(case), begin(case)
    runtime = case.runtime()
    with runtime.continuity.session(MEMBER, first, "held", test=False, journeys=case.journeys, flush=runtime._flush):
        with pytest.raises(JourneyBusy):
            say(case, second, "overlap")
    say(case, second, "overlap")
    assert len(case.observed) == 1


def test_ambiguous_foundry_append_is_discarded_before_retry_not_replayed_as_delivered(continuity_runtime):
    case = continuity_runtime
    journey = begin(case)
    case.foundry.fail_append = True
    with pytest.raises(OSError):
        say(case, journey, "ambiguous", text="Uncommitted synthetic question.")
    assert len(case.foundry.messages()[0]) == 2
    say(case, journey, "ambiguous", text="Accepted retry question.")
    assert case.observed[-1][1] == ()
    assert case.foundry.messages()[0] == ["Accepted retry question.", "The current approved explanation."]
    assert say(case, journey, "ambiguous")["duplicate"]
    assert len(case.observed) == 2


def test_journey_commit_failure_cannot_publish_prepared_dialogue(continuity_runtime):
    case = continuity_runtime
    journey = begin(case)
    def fail(key, text):
        raise OSError("synthetic journal commit unavailable")
    case.journeys.fail = fail
    with pytest.raises(OSError):
        say(case, journey, "failed")
    case.journeys.fail = lambda *_: None
    say(case, journey, "retry")
    assert case.observed[-1][1] == ()
    assert len(case.foundry.messages()[0]) == 2


def test_incomplete_foundry_append_cannot_commit_a_customer_turn(continuity_runtime):
    from governedgraph.orchestrator.continuity import ContinuityUnavailable

    case = continuity_runtime
    journey = begin(case)
    case.foundry.partial_append = True
    with pytest.raises(ContinuityUnavailable):
        say(case, journey, "partial")
    with case.journeys.transaction(journey) as transaction:
        assert "partial" not in transaction.record.processed_requests
    say(case, journey, "partial")
    assert case.observed[-1][1] == () and len(case.foundry.messages()[0]) == 2


def test_customer_commit_failure_recovers_committed_turn_without_reinvoking_the_model(continuity_runtime):
    case = continuity_runtime
    journey = begin(case)
    def fail_clear(key, text):
        if json.loads(text)["pending"] is None and key in case.references.rows:
            raise OSError("synthetic customer commit interrupted")
    case.references.fail = fail_clear
    with pytest.raises(OSError):
        say(case, journey, "committed")
    case.references.fail = lambda *_: None
    assert say(case, journey, "committed")["duplicate"]
    assert len(case.observed) == 1 and len(case.foundry.messages()[0]) == 2
    say(case, begin(case, contact_type="second_contact"), "next")
    assert len(case.observed[-1][1]) == 2


def test_pending_outbox_prevents_history_reuse_until_the_audit_delivery_barrier_recovers(continuity_runtime):
    case = continuity_runtime
    journey = begin(case)
    case.publisher.send.side_effect = TimeoutError("synthetic delivery outage")
    with pytest.raises(DeliveryPending):
        say(case, journey, "pending")
    with pytest.raises(DeliveryPending):
        say(case, journey, "new")
    assert len(case.observed) == 1
    case.publisher.send.side_effect = None
    say(case, journey, "new")
    assert len(case.observed[-1][1]) == 2
    assert len(case.foundry.messages()[0]) == 4


@pytest.mark.parametrize("audit,escalated", [("unavailable", False), ("committed", True)])
def test_withheld_or_escalated_turns_never_enter_reusable_history(continuity_runtime, audit, escalated):
    case = continuity_runtime
    journey = begin(case)
    case.mode.update(audit=audit, escalated=escalated)
    if audit == "unavailable":
        with pytest.raises(AuditUnavailable):
            say(case, journey, "withheld")
    else:
        say(case, journey, "escalated")
    assert case.foundry.messages() == [[]]


def test_retention_removes_old_foundry_content_but_not_immutable_journey_evidence(continuity_runtime):
    case = continuity_runtime
    journey = begin(case)
    for i in range(23):
        say(case, journey, f"turn-{i}", text=f"Synthetic question {i}.")
    messages = case.foundry.messages()[0]
    assert len(messages) == 40 and messages[0] == "Synthetic question 3."
    assert "Synthetic question 0." not in messages and len(case.observed[-1][1]) == 40
    with case.journeys.transaction(journey) as transaction:
        assert len(transaction.record.processed_requests) == 23


def test_historical_numbers_are_not_model_authority_and_screening_respects_provider_limits():
    history = tuple(ConversationMessage("user" if i % 2 == 0 else "assistant",
                                      "Synthetic explanation " + "x" * 3000 + " 1,412.50 on 2027-01-01.")
                    for i in range(40))
    documents = list(screening_batches(history))
    assert sum(len(batch) for batch in documents) == 40
    assert all(len(batch) <= 5 and sum(map(len, batch)) <= 10_000 for batch in documents)
    payload = json.dumps(reference_messages(history))
    assert "1,412.50" not in payload and "2027-01-01" not in payload
    assert "Synthetic explanation" in payload and "current official reference" in payload
    with pytest.raises(ContractError):
        ConversationMessage.from_dict({"role": "user", "text": "synthetic", "age": 80})


def test_native_conversation_identifiers_are_not_channel_inputs():
    from pydantic import ValidationError
    from governedgraph.orchestrator.api import StartJourney, Turn
    for contract, payload in [(StartJourney, {}), (Turn, {"utterance": "Synthetic question."})]:
        with pytest.raises(ValidationError):
            contract(**payload, conversation_id="conv-forged")


def test_journey_enumeration_excludes_customer_reference_blobs():
    store = object.__new__(BlobJourneyStore)
    store.container = Mock()
    store.container.list_blobs.return_value = [
        SimpleNamespace(name="J-synthetic.json"),
        SimpleNamespace(name="customer-context/opaque.json"),
    ]
    assert list(store.ids()) == ["J-synthetic"]


@pytest.fixture
def model_tools():
    from governedgraph.agents.tools import CounsellorTools
    from governedgraph.common.contracts import MemberValue, MemberValues
    from governedgraph.orchestrator.state_machine import Journey, State

    tools = CounsellorTools(
        Journey("J-synthetic-context", MEMBER.member_ref, state=State.RESOLVED),
        MEMBER.member_ref, Mock(), Mock(), Mock(), Mock(), "gpt-counsellor", "1.5",
    )
    tools.conversation_history = (
        ConversationMessage("user", "Please explain my synthetic letter."),
        ConversationMessage("assistant", "Your earlier official amount was EUR 1,111.00."),
    )
    tools.conversation_screened = True
    tools.values_client.get.side_effect = [
        MemberValues(MEMBER.member_ref, [
            MemberValue("current_monthly_pension", "Official monthly pension", 1111.0, "EUR/month",
                        "synthetic-administrator", "synthetic-run-before", "2026-11-16", "EUR 1,111.00")]),
        MemberValues(MEMBER.member_ref, [
            MemberValue("current_monthly_pension", "Official monthly pension", 2222.0, "EUR/month",
                        "synthetic-administrator", "synthetic-run-now", "2026-11-17", "EUR 2,222.00")]),
    ]
    return tools


def test_semantic_routing_receives_customer_history_but_no_historical_figures(model_tools, monkeypatch):
    from governedgraph.agents import decision_agents, decision_model
    from governedgraph.agents.decision_agents import DecisionContext

    requests = []

    def choose(_tools, state, questions):
        requests.append(state)
        return {"reason": "payment_continuity"} if "reason" in questions else {"response_language": "en"}

    monkeypatch.setattr(decision_model, "choose", choose)
    decision = decision_agents.run_decision(
        "intent", model_tools, "What about that amount now?", DecisionContext("resolved", "unclassified", None))
    state = next(item for item in requests if "Customer: Please explain my synthetic letter." in item)
    assert state.endswith("Customer message: What about that amount now?")
    assert len(requests) == 2 and "Earlier" not in "".join(item for item in requests if item is not state)
    assert "1,111.00" not in "".join(requests)
    assert decision.reason == "payment_continuity"


def test_writer_uses_customer_history_and_new_authoritative_values_not_a_cached_figure(model_tools):
    from governedgraph.agents.foundry_runtime import FoundryComposer
    from governedgraph.guardrails.binding import bind
    from governedgraph.guardrails.numeric_fidelity import check

    model_tools.get_member_values()
    model_tools.get_member_values()
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(
        id="resp-writer-synthetic", status="completed", output=[],
        output_text=json.dumps({"explanation_parts": ["The official amount is {{value:current_monthly_pension}}."],
                                "content_ids": [], "meaning_check_question": ""}),
    )
    strategy = SimpleNamespace(strategy_id="S1", version="1.1", name="Summary", teach_back=False,
                               template_id="T-S1", template_version="1.5")
    raw = FoundryComposer("Synthetic pinned instructions", client=client).draft(
        model_tools, "Can you explain that amount again?", strategy)
    payload = client.responses.create.call_args.kwargs
    assert "synthetic letter" in json.dumps(payload["input"]) and "1,111.00" not in json.dumps(payload)
    assert "2,222.00" not in json.dumps(payload) and payload["store"] is False
    text = bind(json.loads(raw)["text"], model_tools.trace.values)
    assert text == "The official amount is EUR 2,222.00."
    assert check(text, model_tools.trace.values, []).passed
    assert not check("The official amount is EUR 1,111.00.", model_tools.trace.values, []).passed
    assert model_tools.values_client.get.call_count == 2
    assert all(call.args == (MEMBER.member_ref, MEMBER.member_ref)
               for call in model_tools.values_client.get.call_args_list)


def test_stateless_writer_preserves_tool_round_context_without_storing_rejected_drafts(model_tools):
    from governedgraph.agents.foundry_runtime import FoundryComposer

    model_tools.get_member_values()
    function = {"type": "function_call", "name": "get_member_values", "arguments": "{}", "call_id": "call-synthetic"}
    model_tools.as_functions = lambda: [model_tools.get_member_values]
    client = Mock()
    client.responses.create.side_effect = [
        SimpleNamespace(id="resp-tool", status="completed", output_text="",
                        output=[SimpleNamespace(**function, model_dump=lambda **_: function)]),
        SimpleNamespace(id="resp-draft", status="completed", output=[], output_text="A synthetic unbound draft."),
    ]
    inputs = [{"type": "message", "role": "user", "content": "Synthetic current question."}]
    result = FoundryComposer("Synthetic pinned instructions", client=client)._run(model_tools, inputs)
    calls = [call.kwargs for call in client.responses.create.call_args_list]
    assert result == "A synthetic unbound draft."
    assert calls[1]["input"][0] == inputs[0] and calls[1]["input"][1] == function
    assert calls[1]["input"][-1]["type"] == "function_call_output"
    assert all(call["store"] is False and "conversation" not in call and "previous_response_id" not in call
               for call in calls)
    assert "EUR 2,222.00" not in calls[1]["input"][-1]["output"]
