"""Project audited, delivered outcomes into transcript-free ranking aggregates."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path

from governedgraph.common.contracts import ExplainabilityRecord, RequestKey, _strict
from governedgraph.common.io import load_csv
from governedgraph.learning.ranking_pipeline import load_outcome_aggregates
from governedgraph.orchestrator.state_machine import Event, Journey, State
from governedgraph.strategy_service.catalogue import Catalogue

WATERMARK = "ILLUSTRATIVE - synthetic data"
COLUMNS = ("request_key", "strategy_id", "strategy_version", "interactions",
           "resolved_first_contact", "repeat_contact_14d")


@dataclass(frozen=True)
class FeedbackManifest:
    outcome_digest: str
    source_digest: str
    baseline_digest: str
    observed_decisions: int
    eligible_decisions: int
    pending_decisions: int
    observed_resolutions: int
    watermark: str = WATERMARK

    def __post_init__(self):
        import re

        if (any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                for value in (self.outcome_digest, self.source_digest, self.baseline_digest))
                or any(type(value) is not int or value < 0 for value in
                       (self.observed_decisions, self.eligible_decisions,
                        self.pending_decisions, self.observed_resolutions))
                or self.observed_decisions != self.eligible_decisions + self.pending_decisions
                or self.observed_resolutions > self.observed_decisions
                or self.watermark != WATERMARK):
            raise ValueError("invalid closed synthetic feedback manifest")

    @classmethod
    def from_dict(cls, payload: dict) -> FeedbackManifest:
        return _strict(cls, payload)


@dataclass(frozen=True)
class FeedbackSnapshot:
    outcomes: bytes
    manifest: FeedbackManifest


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("feedback evidence requires timezone-aware timestamps")
    return parsed


def project_feedback(journeys: list[Journey], records: list[ExplainabilityRecord], *,
                     baseline: Path | None = None, now: datetime | None = None) -> FeedbackSnapshot:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("feedback projection requires a timezone-aware observation time")
    counts: dict[tuple[str, str, str], list[int]] = {}
    baseline_bytes = b""
    if baseline is not None:
        load_outcome_aggregates(baseline)
        baseline_bytes = baseline.read_bytes()
        for row in load_csv(baseline):
            counts[(row["request_key"], row["strategy_id"], row["strategy_version"])] = [
                int(row["interactions"]), int(row["resolved_first_contact"]), int(row["repeat_contact_14d"]),
            ]

    sources = {}
    for journey in journeys:
        if journey.journey_id in sources:
            raise ValueError("duplicate journey in feedback source")
        sources[journey.journey_id] = journey
    catalogue = Catalogue.load()
    decisions = {}
    audits = {}
    checked_responses = set()
    for record in records:
        journey = sources.get(record.journey_id)
        if journey is None or journey.member_ref != record.member_ref:
            raise ValueError("feedback decision has no matching scoped journey")
        if record.numeric_fidelity_passed and record.response_id:
            checked_responses.add((record.journey_id, record.response_id))
    for record in records:
        if record.response_kind != "explanation" or not record.numeric_fidelity_passed:
            continue
        journey = sources.get(record.journey_id)
        if journey is None or journey.member_ref != record.member_ref:
            raise ValueError("feedback decision has no matching scoped journey")
        if not record.response_id:
            raise ValueError("feedback requires response-linked audit evidence")
        identity = (record.journey_id, record.response_id)
        if identity in audits and audits[identity] != record:
            raise ValueError("conflicting feedback audit identity")
        audits[identity] = record
        delivered = [event for event in journey.events
                     if event.response_id == record.response_id and not event.response_withheld]
        if not delivered:
            continue
        if len(delivered) != 1:
            raise ValueError("feedback requires exactly one delivered event per decision")
        key = RequestKey.from_dict(record.request_key)
        if (record.arm not in {"control", "treatment"}
                or not any(strategy.strategy_id == record.strategy_id
                           and strategy.version == record.strategy_version
                           for strategy in catalogue.rankable_for(key))):
            raise ValueError("feedback decision references an unapproved ranking context")
        started = _time(delivered[0].created_at)
        if started > now:
            raise ValueError("future delivered evidence cannot enter learning")
        exposure = (journey.journey_id, key.id, record.strategy_id, record.strategy_version)
        previous = decisions.get(exposure)
        sequence = delivered[0].sequence
        if previous is None or (started, sequence) < (previous[0], previous[2]):
            decisions[exposure] = (started, record, sequence)

    observed = eligible = resolutions = 0
    explanation_responses = set(audits)
    provenance = []
    for (journey_id, key, sid, version), (started, record, sequence) in sorted(decisions.items()):
        journey = sources[journey_id]
        deadline = started + timedelta(days=14)
        resolved = any(
            event.event == Event.TEACH_BACK_PASSED.value
            and event.to_state == State.RESOLVED.value
            and event.strategy_id == sid and event.strategy_version == version
            and event.response_id is not None and not event.response_withheld
            and started <= _time(event.created_at) <= min(deadline, now)
            and (journey_id, event.response_id) in checked_responses
            for event in journey.events
        )
        repeated = any(
            event.from_state == State.RESOLVED.value
            and event.event == Event.INTENT_IN_SCOPE.value
            and event.sequence > sequence
            and started <= _time(event.created_at) <= min(deadline, now)
            for event in journey.events
        ) or any(
            other.journey_id != journey_id and other.member_ref == journey.member_ref
            and any(event.event == Event.DISCLOSED_AND_AUTHENTICATED.value
                    and started <= _time(event.created_at) <= min(deadline, now)
                    for event in other.events)
            for other in journeys
        )
        next_explanation = min(
            (event.sequence for event in journey.events
             if event.sequence > sequence
             and (journey_id, event.response_id) in explanation_responses),
            default=len(journey.events) + 1,
        )
        failed = any(
            event.event == Event.TEACH_BACK_FAILED.value
            and sequence < event.sequence < next_explanation
            and started <= _time(event.created_at) <= min(deadline, now)
            for event in journey.events
        )
        observed += 1
        resolutions += int(resolved)
        mature = now >= deadline and (resolved or repeated or failed)
        provenance.append((record.response_id, record.snapshot_id, record.arm, key, sid,
                           version, started.isoformat(), mature, resolved, repeated, failed))
        if mature:
            eligible += 1
            row = counts.setdefault((key, sid, version), [0, 0, 0])
            row[0] += 1
            row[1] += int(resolved and not repeated)
            row[2] += int(repeated)

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(COLUMNS)
    for key, row in sorted(counts.items()):
        writer.writerow((*key, *row))
    payload = output.getvalue().encode()
    return FeedbackSnapshot(payload, FeedbackManifest(
        hashlib.sha256(payload).hexdigest(),
        hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest(),
        hashlib.sha256(baseline_bytes).hexdigest(),
        observed, eligible, observed - eligible, resolutions,
    ))
