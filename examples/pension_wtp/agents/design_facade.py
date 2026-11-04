"""Offline APIM contract stub, not a deployed or authenticated HTTP service."""
from __future__ import annotations

from pension_wtp.agents.adoption_guide import AdoptionGuide, NextStep
from pension_wtp.agents.design_contracts import (
    ActuarySample, BatchReference, BuildSample, ConversionBatch, DocumentCheck,
    EmptyRequest, Escalate, EventOutcome, EventReference, IdempotentWrites, JourneyState, LifeEvent,
    ReconciliationResult, RouteEventException, RouteException, ScheduleConversion,
    SearchNextSteps, SelectNextStep,
)
from pension_wtp.agents.event_processing import EventProcessingAgent
from pension_wtp.agents.transition_calculation import TransitionCalculationAgent
from governedgraph.common.contracts import ContractError, EscalationRequest, StrategyResponse

# operation -> (agent path, request, response, writes)
OPERATIONS = {
    "schedule_conversion_batch": ("calculation", ScheduleConversion, ConversionBatch, True),
    "get_batch_status": ("calculation", BatchReference, ConversionBatch, False),
    "run_reconciliation_check": ("calculation", BatchReference, ReconciliationResult, True),
    "route_exception": ("calculation", RouteException, RouteException, True),
    "build_actuary_sample": ("calculation", BuildSample, ActuarySample, True),
    "ingest_life_event": ("events", LifeEvent, LifeEvent, True),
    "check_documents": ("events", EventReference, DocumentCheck, False),
    "submit_event_to_administration": ("events", EventReference, EventOutcome, True),
    "route_to_exception_queue": ("events", RouteEventException, EventOutcome, True),
    "get_journey_state": ("adoption", EmptyRequest, JourneyState, False),
    "get_ranked_strategies": ("adoption", EmptyRequest, StrategyResponse, False),
    "search_approved_content": ("adoption", SearchNextSteps, list[NextStep], False),
    "escalate_to_advisor": ("adoption", Escalate, EscalationRequest, True),
    "next_step": ("adoption", SelectNextStep, NextStep, True),
}


class DesignDepthFacade:
    def __init__(self, calculation: TransitionCalculationAgent, events: EventProcessingAgent,
                 adoption: AdoptionGuide):
        self.calculation = calculation
        self.events = events
        self.adoption = adoption
        self._writes = IdempotentWrites()

    def invoke(self, operation: str, payload: dict, *, idempotency_key: str = "") -> object:
        if operation not in OPERATIONS:
            raise PermissionError("operation is not in the approved design-depth tool catalogue")
        if OPERATIONS[operation][3] and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128
        ):
            raise ContractError("every APIM write requires an Idempotency-Key header (max 128 characters)")
        if OPERATIONS[operation][3]:
            return self._writes.apply(operation, idempotency_key, payload,
                                      lambda: self._dispatch(operation, payload, idempotency_key))
        return self._dispatch(operation, payload, idempotency_key)

    def _dispatch(self, operation: str, payload: dict, idempotency_key: str) -> object:
        match operation:
            case "schedule_conversion_batch":
                req = ScheduleConversion.from_dict(payload)
                return self.calculation.schedule_conversion_batch(
                    req.run_plan_id, req.rule_set_version, req.member_refs, req.capacity,
                    idempotency_key=idempotency_key,
                )
            case "get_batch_status":
                ref = BatchReference.from_dict(payload)
                return self.calculation.get_batch_status(ref.batch_id)
            case "run_reconciliation_check":
                ref = BatchReference.from_dict(payload)
                return self.calculation.run_reconciliation_check(
                    self.calculation.get_batch_status(ref.batch_id), idempotency_key=idempotency_key,
                )
            case "route_exception":
                exception = RouteException.from_dict(payload)
                return self.calculation.route_exception(exception.batch_id, exception.exception_id,
                                                        idempotency_key=idempotency_key)
            case "build_actuary_sample":
                sample = BuildSample.from_dict(payload)
                return self.calculation.build_actuary_sample(
                    self.calculation.get_batch_status(sample.batch_id), sample.size, sample.seed,
                    idempotency_key=idempotency_key,
                )
            case "ingest_life_event":
                event = LifeEvent.from_dict(payload)
                return self.events.ingest_life_event(event, idempotency_key=idempotency_key)
            case "check_documents":
                event_ref = EventReference.from_dict(payload)
                return self.events.check_documents(event_ref.event_id)
            case "submit_event_to_administration":
                event_ref = EventReference.from_dict(payload)
                return self.events.submit_event_to_administration(event_ref.event_id, idempotency_key=idempotency_key)
            case "route_to_exception_queue":
                route = RouteEventException.from_dict(payload)
                return self.events.route_to_exception_queue(route.event_id, route.reason_code,
                                                            idempotency_key=idempotency_key)
            case "get_journey_state":
                EmptyRequest.from_dict(payload)
                return self.adoption.get_journey_state()
            case "get_ranked_strategies":
                EmptyRequest.from_dict(payload)
                return self.adoption.get_ranked_strategies()
            case "search_approved_content":
                search = SearchNextSteps.from_dict(payload)
                return self.adoption.search_approved_content(search.query)
            case "escalate_to_advisor":
                escalation = Escalate.from_dict(payload)
                return self.adoption.escalate_to_advisor(escalation.reason_code, idempotency_key=idempotency_key)
            case "next_step":
                selection = SelectNextStep.from_dict(payload)
                state = self.adoption.get_journey_state()
                return self.adoption.next_step(state.journey_stage, state.open_items,
                                               selection.advice_seeking, option_level=selection.option_level,
                                               idempotency_key=idempotency_key)
        raise AssertionError("operation catalogue and dispatcher differ")
