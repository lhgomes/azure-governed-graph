"""Customer-bound Foundry dialogue, with reference-only Blob commit recovery."""
from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
import re
from typing import Callable, Protocol

from governedgraph.agents.conversation_context import ConversationMessage, MAX_CONVERSATION_TURNS
from governedgraph.common.contracts import ContractError, _strict
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.orchestrator.store import BlobRecordStore, JourneyStore, Transaction, validate_id

log = logging.getLogger("governedgraph.continuity")
MAX_ITEMS = MAX_CONVERSATION_TURNS * 2


class ContinuityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingConversation:
    journey_id: str
    request_id: str
    after_item: str | None

    @classmethod
    def from_dict(cls, payload: dict) -> "PendingConversation":
        result = _strict(cls, payload)
        validate_id(result.journey_id)
        validate_id(result.request_id)
        if result.after_item is not None and not isinstance(result.after_item, str):
            raise ContractError("invalid conversation cursor")
        return result


@dataclass
class CustomerConversation:
    owner_key: str
    conversation_id: str
    pending: PendingConversation | None = None

    def encode(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def decode(cls, text: str) -> "CustomerConversation":
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get("pending") is not None:
            payload["pending"] = PendingConversation.from_dict(payload["pending"])
        result = _strict(cls, payload)
        if (not isinstance(result.owner_key, str) or not re.fullmatch(r"[0-9a-f]{64}", result.owner_key)
                or not isinstance(result.conversation_id, str) or not result.conversation_id):
            raise ContractError("invalid customer conversation reference")
        return result


def _message(item) -> ConversationMessage:
    if item.type != "message" or item.role not in {"user", "assistant"}:
        raise ContinuityUnavailable("unexpected customer conversation item")
    if not item.content or any(part.type not in {"input_text", "output_text"} for part in item.content):
        raise ContinuityUnavailable("unexpected customer conversation content")
    return ConversationMessage(item.role, "".join(part.text for part in item.content))


class ConversationSession:
    def __init__(self, client, transaction: Transaction[CustomerConversation] | None,
                 record: CustomerConversation | None, journey_id: str, request_id: str, member_ref: str):
        self.client, self.transaction, self.record = client, transaction, record
        self.journey_id, self.request_id = journey_id, request_id
        self.member_ref = member_ref
        self.messages: tuple[ConversationMessage, ...] = ()

    def save(self) -> None:
        if self.transaction is None or self.record is None:
            raise ContinuityUnavailable("a verified conversation transaction is required")
        self.transaction.save(self.record)

    def items(self, *, after: str | None = None):
        if self.record is None:
            raise ContinuityUnavailable("a verified conversation reference is required")
        page = self.client.items.list(
            self.record.conversation_id, order="asc", limit=MAX_ITEMS + 3,
            **({"after": after} if after is not None else {}),
        )
        if page.has_more or len(page.data) > MAX_ITEMS + 2:
            raise ContinuityUnavailable("customer conversation exceeded its bounded journal")
        return page.data

    def recover(self, journeys, flush) -> None:
        if self.record is None:
            return
        pending = self.record.pending
        if pending is not None:
            with journeys.transaction(pending.journey_id) as target:
                record = target.record
                committed = record is not None and pending.request_id in record.processed_requests
                if committed and record is not None:
                    if record.journey.member_ref != self.member_ref:
                        raise ContinuityUnavailable("conversation commit owner mismatch")
                    flush(target, record)
            items = self.items(after=pending.after_item)
            if committed:
                if len(items) != 2 or [_message(item).role for item in items] != ["user", "assistant"]:
                    raise ContinuityUnavailable("incomplete committed customer conversation")
            else:
                for item in items:
                    self.client.items.delete(item.id, conversation_id=self.record.conversation_id)
                log.warning("unfinished_conversation_discarded", extra={"journey_id": pending.journey_id})
            self._trim()
            self.record.pending = None
            self.save()
        items = self.items()
        messages = tuple(_message(item) for item in items)
        if len(messages) % 2 or any(item.role != ("user" if i % 2 == 0 else "assistant")
                                    for i, item in enumerate(messages)):
            raise ContinuityUnavailable("customer conversation has an invalid turn sequence")
        self.messages = messages

    def _trim(self) -> None:
        if self.record is None:
            raise ContinuityUnavailable("a verified conversation reference is required")
        items = self.items()
        for item in items[:max(0, len(items) - MAX_ITEMS)]:
            self.client.items.delete(item.id, conversation_id=self.record.conversation_id)

    def prepare(self, utterance: str, answer: str) -> None:
        if self.record is None:
            return  # The session explicitly excludes non-step-up identities.
        if not utterance.strip():
            log.info("empty_turn_not_retained", extra={"journey_id": self.journey_id})
            return
        pair = (ConversationMessage("user", utterance), ConversationMessage("assistant", answer))
        items = self.items()
        self.record.pending = PendingConversation(
            self.journey_id, self.request_id, items[-1].id if items else None,
        )
        self.save()
        # Journal first: an ambiguous create is recovered under the same customer lease.
        created = self.client.items.create(self.record.conversation_id, items=[
            {"type": "message", "role": item.role, "content": item.text} for item in pair
        ])
        if tuple(_message(item) for item in created.data) != pair:
            raise ContinuityUnavailable("Foundry did not confirm the complete prepared turn")


class ConversationContinuity(Protocol):
    def session(self, identity: MemberIdentity, journey_id: str, request_id: str, *, test: bool,
                journeys: JourneyStore, flush: Callable) -> AbstractContextManager[ConversationSession]: ...


class CustomerConversations:
    def __init__(self, store, client, *, identity_namespace: str):
        if not isinstance(identity_namespace, str) or not identity_namespace:
            raise ContinuityUnavailable("a trusted customer identity namespace is required")
        self.store, self.client = store, client
        self.identity_namespace = identity_namespace

    @classmethod
    def azure(cls, storage_url: str, project_endpoint: str) -> "CustomerConversations":
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from governedgraph.common.config import settings

        if not storage_url or not project_endpoint or not settings.member_token_issuer:
            raise ContinuityUnavailable("Azure customer continuity endpoints and member issuer are required")
        project = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential(),
                                  connection_timeout=5, read_timeout=10, retry_total=0)
        # Conversation CRUD uses the private project endpoint; inference still uses APIM.
        client = project.get_openai_client(max_retries=0, timeout=20)
        return cls(BlobRecordStore(storage_url, "journey-state", CustomerConversation.decode,
                                   prefix="customer-context/"), client.conversations,
                   identity_namespace=json.dumps([project_endpoint, settings.member_token_issuer]))

    @contextmanager
    def session(self, identity: MemberIdentity, journey_id: str, request_id: str, *, test: bool, journeys, flush):
        from azure.core.exceptions import AzureError
        from openai import OpenAIError

        validate_id(journey_id)
        validate_id(request_id)
        if not identity.step_up_verified:
            log.info("customer_continuity_requires_step_up", extra={"journey_id": journey_id})
            yield ConversationSession(None, None, None, journey_id, request_id, identity.member_ref)
            return
        scope = ["admin-test", journey_id] if test else ["customer"]
        owner_key = hashlib.sha256(
            json.dumps([self.identity_namespace, *scope, identity.member_ref]).encode(),
        ).hexdigest()
        try:
            with self.store.transaction(owner_key) as transaction:
                record = transaction.record
                metadata = {"customer_key": owner_key, "context_schema": "1"}
                if record is None:
                    conversation = self.client.create(metadata=metadata)
                    record = CustomerConversation(owner_key, conversation.id)
                    transaction.save(record)
                elif record.owner_key != owner_key:
                    raise ContinuityUnavailable("customer conversation ownership mismatch")
                conversation = self.client.retrieve(record.conversation_id)
                if conversation.id != record.conversation_id or conversation.metadata != metadata:
                    raise ContinuityUnavailable("Foundry conversation ownership mismatch")
                session = ConversationSession(self.client, transaction, record, journey_id, request_id,
                                              identity.member_ref)
                session.recover(journeys, flush)
                yield session
                session.recover(journeys, flush)
        except (AzureError, OpenAIError, ContinuityUnavailable, ContractError, json.JSONDecodeError) as error:
            log.error("customer_continuity_unavailable", extra={
                "journey_id": journey_id, "error_type": type(error).__name__,
            })
            raise ContinuityUnavailable("customer conversation unavailable; retry the same request") from error
