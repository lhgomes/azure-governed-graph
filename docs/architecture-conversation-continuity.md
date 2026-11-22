# Customer-scoped conversation continuity

Part of the [architecture](architecture.md). This page describes how an authenticated customer's
dialogue carries across turns, contacts and channels without becoming a source of facts,
authorization or learning.

The authenticated, step-up-verified
`MemberIdentity.member_ref`, namespaced by the configured trusted issuer and
Foundry project, owns a durable Foundry conversation across turns,
new contacts, channels and orchestrator restarts. A closed contact selector is
not a memory reset. A new customer or absent earlier exchange gets no invented
recollection. Each administrator test journey has a separate conversation;
it neither reads nor updates ordinary customer history.

Session start recovers the verified customer's conversation before releasing
the audited greeting, using the same customer-before-journey lock order as a
turn. Retained dialogue selects an approved returning greeting regardless of
the contact selector. `second_contact` without retained dialogue selects an
approved follow-up greeting that explicitly states the earlier conversation
is unavailable; it does not create fictional history. New customers retain
the existing first-contact greeting. Every variant includes the AI disclosure
and human-help offer, and the disclosure version is recorded in the audit.
The [closed channel start contract](../src/governedgraph/voice/voice_live_bridge.py)
accepts only these approved greetings, not arbitrary text containing an AI label.
Greetings neither replay historical text or figures nor append a customer turn.
Recovery failures prevent greeting release; a busy customer produces the
retryable conflict rather than an uncontextualized first-contact greeting.

The [continuity adapter](../src/governedgraph/orchestrator/continuity.py) keeps
only an opaque owner key, Foundry conversation ID and pending commit references
under `customer-context/` in the existing `journey-state` container. Ordinary
journey enumeration excludes that prefix. Customer leases serialize concurrent
contacts; customer locks precede journey locks. A busy customer returns the
existing retryable conflict, not a second concurrent model turn. Channel bodies
and model tools cannot select customer or Foundry conversation identifiers.
Non-step-up identities receive no persisted personal context.

Only screened customer text paired with the final checked, audited answer
committed for release is appended, never rejected drafts, internal decisions,
tool outputs or withheld/escalated turns. A reference-only prepare journal is
saved before the Foundry append. Recovery checks the durable journey request
commit and flushes its existing audit/delivery outbox before accepting the pair;
uncommitted appended items are removed before any later model invocation.
Ambiguous appends are not blindly retried. Retention is the most recent **20
completed turns**, not a time-based retention promise. Older Foundry context
items are pruned on successful commit/recovery; cleanup failure prevents reuse
and surfaces an error for retry. Immutable audit/event history is not deleted.
A released reply is not proof of receipt, voice playback or comprehension.

Prior dialogue is re-screened through Prompt Shields in provider-bounded batches.
Historical numbers are masked before role-labelled dialogue is supplied as untrusted
reference data to the Decision model (intent, understanding and answer review)
and the writer. Current authorization, authoritative value reads, approved retrieval,
signed ranking and all output gates still run. History never authorizes tools,
restores an old journey state or becomes approved factual evidence. Demographic
self-descriptions are not profile features and must not affect treatment.
The [context contract](../src/governedgraph/agents/conversation_context.py) is
closed; no demographic attribute or caller-selected identity is added.

Inference remains behind APIM, with `store=false`; intra-answer tool rounds
carry their inputs/outputs only in process memory. Accepted dialogue is retained
through Foundry Conversations CRUD at the existing private project endpoint,
using the existing runtime identity and permissions. This avoids automatically
appending an unchecked model response to durable customer history.

Operational memory is not model training or modification of prompts, templates,
models or catalogue: B4 prohibits those transcript-driven changes. The only governed exception is the
[gap review](architecture-gap-review.md), which proposes to people only.
P2 still reads closed aggregates only, never Foundry conversation content.
See [focused regression evidence and limits](evaluation.md#customer-scoped-conversation-continuity).
