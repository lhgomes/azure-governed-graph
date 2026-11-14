"""Project audited, delivered outcomes into transcript-free ranking aggregates."""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re

from governedgraph.common.contracts import ExplainabilityRecord, RequestKey, _strict
from governedgraph.learning.ranking_pipeline import load_outcome_aggregates
from governedgraph.orchestrator.state_machine import Event, Journey
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
    reported_yes: int = 0
    reported_no: int = 0
    reported_skip: int = 0
    unknown_after_window: int = 0
    attributed_repeats: int = 0
    ambiguous_contacts: int = 0

    def __post_init__(self):
        if (any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                for value in (self.outcome_digest, self.source_digest, self.baseline_digest))
                or any(type(value) is not int or value < 0 for value in
                       (self.observed_decisions, self.eligible_decisions,
                        self.pending_decisions, self.observed_resolutions,
                        self.reported_yes, self.reported_no, self.reported_skip,
                        self.unknown_after_window, self.attributed_repeats, self.ambiguous_contacts))
                or self.observed_decisions != self.eligible_decisions + self.pending_decisions
                or self.observed_resolutions > self.observed_decisions
                or self.reported_yes + self.reported_no + self.reported_skip > self.observed_decisions
                or self.unknown_after_window > self.pending_decisions
                or self.attributed_repeats > self.observed_decisions
                or self.ambiguous_contacts > self.observed_decisions
                or self.watermark != WATERMARK):
            raise ValueError("invalid closed synthetic feedback manifest")

    @classmethod
    def from_dict(cls, payload: dict) -> FeedbackManifest:
        return _strict(cls, payload)

    def to_dict(self) -> dict:
        return asdict(self)


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
                     baseline: Path | None = None, now: datetime | None = None,
                     catalogue: Catalogue | None = None) -> FeedbackSnapshot:
    catalogue = catalogue or Catalogue.load()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("feedback projection requires a timezone-aware observation time")
    test_journeys = {journey.journey_id for journey in journeys if journey.test_strategy_id}
    journeys = [journey for journey in journeys if journey.journey_id not in test_journeys]
    records = [record for record in records if record.journey_id not in test_journeys]
    counts: dict[tuple[str, str, str], list[int]] = {}
    baseline_bytes = b""
    if baseline is not None:
        # Preserve the governed Azure baseline import; never fall back to bundled seed data.
        evidence = load_outcome_aggregates(baseline, catalogue=catalogue)
        baseline_bytes = baseline.read_bytes()
        for key, strategies in evidence.items():
            for sid, row in strategies.items():
                counts[(key, sid, catalogue.get(sid).version)] = [row.interactions, row.resolved, row.repeat_contacts]

    sources = {}
    for journey in journeys:
        if journey.journey_id in sources:
            raise ValueError("duplicate journey in feedback source")
        sources[journey.journey_id] = journey
    playbacks = {}
    for journey in journeys:
        for event in journey.events:
            if event.event != Event.PLAYBACK_CONFIRMED.value:
                continue
            if (journey.channel != "voice" or event.journey_id != journey.journey_id
                    or event.sequence <= 0 or event.playback_response_id is None):
                raise ValueError("invalid voice playback evidence")
            identity = (journey.journey_id, event.playback_response_id)
            if identity in playbacks:
                raise ValueError("duplicate voice playback evidence")
            playbacks[identity] = event
    decisions = {}
    delivered_responses = {}
    audits = {}
    validated_playbacks = set()
    for record in records:
        journey = sources.get(record.journey_id)
        if journey is None or journey.member_ref != record.member_ref:
            raise ValueError("feedback decision has no matching scoped journey")
        if record.response_kind != "explanation" or not record.numeric_fidelity_passed:
            continue
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
        started = _time(delivered[0].created_at)
        if started > now:
            raise ValueError("future delivered evidence cannot enter learning")
        if _time(record.created_at) > started:
            raise ValueError("feedback audit must precede delivery")
        sequence = delivered[0].sequence
        playback = playbacks.get(identity)
        if playback is not None:
            played = _time(playback.created_at)
            if (not delivered[0].outcome_offered or played < started or played > now
                    or playback.sequence <= sequence):
                raise ValueError("voice playback must follow its offered answer")
            validated_playbacks.add(identity)
        elif journey.channel == "voice" and delivered[0].outcome_offered:
            continue
        delivered_responses[identity] = delivered[0]
        key = RequestKey.from_dict(record.request_key)
        approved = catalogue.historical(record.strategy_id, record.strategy_version)
        if (record.arm not in {"control", "treatment"} or approved is None
                or not approved.ranked_for_resolution or not approved.eligible_for(key)):
            raise ValueError("feedback decision references an unapproved ranking context")
        if delivered[0].outcome_offered:
            decisions[identity] = (started, record, sequence)

    if set(playbacks) != validated_playbacks:
        raise ValueError("voice playback requires a checked, delivered explanation")

    outcomes = {}
    for journey in journeys:
        for event in journey.events:
            is_outcome = event.event == Event.OUTCOME_REPORTED.value
            is_link = event.related_response_id is not None
            if not is_outcome and not is_link:
                continue
            if event.journey_id != journey.journey_id or event.sequence <= 0:
                raise ValueError("feedback evidence has an invalid source journey or sequence")
            identity = ((journey.journey_id, event.outcome_response_id) if is_outcome
                        else (event.related_journey_id, event.related_response_id))
            target = sources.get(identity[0])
            delivered = delivered_responses.get(identity)
            audit = audits.get(identity)
            if (target is None or target.member_ref != journey.member_ref or delivered is None or audit is None
                    or audit.member_ref != journey.member_ref):
                raise ValueError("feedback evidence must reference a delivered, audited answer for the same member")
            when = _time(event.created_at)
            if (when > now or when < _time(delivered.created_at)
                    or (journey.journey_id == identity[0] and event.sequence <= delivered.sequence)):
                raise ValueError("feedback evidence must follow its delivered answer")
            playback = playbacks.get(identity)
            if playback is not None and (
                    when < _time(playback.created_at)
                    or (journey.journey_id == identity[0] and event.sequence <= playback.sequence)):
                raise ValueError("feedback evidence must follow voice playback")
            if is_outcome:
                if not delivered.outcome_offered:
                    raise ValueError("an outcome requires an offered answer")
                if identity in outcomes:
                    raise ValueError("an answer cannot have duplicate outcome reports")
                outcomes[identity] = event

    observed = eligible = resolutions = 0
    yes = no = skipped = unknown = linked_repeats = ambiguities = 0
    provenance = []

    for (journey_id, response_id), (started, record, sequence) in sorted(decisions.items()):
        if not catalogue.is_active(record.strategy_id, record.strategy_version):
            continue
        journey = sources[journey_id]
        key = RequestKey.from_dict(record.request_key).id
        sid, version = record.strategy_id, record.strategy_version
        deadline = started + timedelta(days=14)
        reported = outcomes.get((journey_id, response_id))
        choice = (reported.outcome_choice if reported is not None and _time(reported.created_at) <= deadline else None)
        resolved = choice == "yes"
        repeated = ambiguous = False
        for other in journeys:
            if other.member_ref != journey.member_ref:
                continue
            contacts = [
                event for event in other.events
                if event.event in (Event.INTENT_IN_SCOPE.value, Event.CONTACT_LINKED.value)
                and started <= _time(event.created_at) <= min(deadline, now)
                and (other.journey_id != journey_id or event.sequence > sequence)
            ]
            for event in contacts:
                linked = (event.related_journey_id, event.related_response_id)
                if linked == (journey_id, response_id):
                    repeated = True
                elif event.event == Event.INTENT_IN_SCOPE.value and event.related_response_id is None:
                    ambiguous = True
            if other.journey_id != journey_id and not contacts and any(
                event.event == Event.DISCLOSED_AND_AUTHENTICATED.value
                and started <= _time(event.created_at) <= min(deadline, now)
                for event in other.events
            ):
                ambiguous = True
        failed = choice == "no"
        observed += 1
        yes += int(choice == "yes")
        no += int(choice == "no")
        skipped += int(choice == "skip")
        linked_repeats += int(repeated)
        ambiguities += int(ambiguous)
        resolutions += int(resolved)
        mature = now >= deadline and (repeated or ((resolved or failed) and not ambiguous))
        unknown += int(now >= deadline and not mature)
        provenance.append((record.response_id, record.snapshot_id, record.arm, key, sid,
                           version, started.isoformat(), mature, resolved, repeated, failed, ambiguous, choice))
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
        reported_yes=yes, reported_no=no, reported_skip=skipped,
        unknown_after_window=unknown, attributed_repeats=linked_repeats,
        ambiguous_contacts=ambiguities,
    ))
