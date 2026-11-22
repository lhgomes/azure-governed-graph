# Gap review: declined interactions to owner-reviewed proposals

Part of the [architecture](architecture.md). Some answers are declined although they were legitimate, and some
questions have no approved content or style yet. A scheduled review finds these gaps and proposes fixes. People
decide; nothing it proposes goes live by itself. The owner-facing screen is described in the
[administration guide](administration.md#improvement-ideas-declined-answers-and-proposed-fixes).

## What runs

The daily `job-gg-gap-review` (`governedgraph.learning.gap_review_job`) reviews every declined interaction of the
last 30 days and writes **proposals** for the administration console. It reads the immutable response records, the
member questions kept for declined answers and, to leave selected-style test journeys out of its counts, the journey
state. It runs under its own identity. That identity can read response records, kept questions, journey state and
content approvals, write proposals, and call its own drafting agent. It cannot write an approval, sign, publish
content, change the catalogue or start a job.

A declined interaction is an explanation question that ended in an advisor offer, or whose delivered answer the final
answer review flagged. Findings on a draft that was repaired and then passed review do not count. Interactions are grouped by closed situation (journey stage, topic, channel, accessibility mode, language).
A proposal needs at least five declined answers from three journeys.

## Inputs

**Closed decline reason.** Each acknowledgment or escalation record carries an optional closed `decline_reason`
(`output_gate`, `answer_review`, `no_safe_answer`, `no_eligible_style`, `teach_back_failed`, `content_safety` and so
on), set where the runtime declines. Records without one are stored byte-identically. "No eligible style" escalations
count as declined answers and can propose a new style. Protective and member-requested exits are context only.

**Kept member questions.** When an answer ends as `output_gate`, `answer_review`, `no_safe_answer` or
`no_eligible_style`, or is delivered after the answer review raised a finding, the runtime writes the member's
question as written to the quarantined `declined-questions` container. This is best effort, happens after the audit
record, is never logged and is skipped for selected-style test journeys. The container is not immutable, so an
erasure request can be honoured, and a lifecycle rule expires blobs, including noncurrent versions, after 30 days.
Up to five distinct questions per proposal appear to owners as examples, and the same questions go to the drafting
agent. Proposal blobs, which hold those examples, expire after 30 days too.

The kept text never feeds learning, ranking, prompts, templates, models or the catalogue. It reaches behaviour only
when an owner approves a passage or style. This is the single governed exception to B4, which otherwise forbids any
path from conversations to behaviour.

## Proposals

| Proposal | Trigger | Draft |
|---|---|---|
| Missing approved content | Most declined answers retrieved no approved passage | Title, stage, language, questions answered, related passages and an AI-drafted passage |
| Approved content to revise | Most declined answers were flagged as not grounded in the retrieved passage | The retrieved passage as the base for a new version, with an AI revision |
| Missing explanation style | At most one approved style can explain, or most declined answers had no eligible style | An AI recommendation: the themes, why current styles do not fit, and either a style on an approved pattern scoped to the situation, or a described new style that can only be acknowledged. Not offered without the AI answer |
| Answers refused that may have been fine | Content and styles exist, and the refusals came from quality-only review findings or output gates | None. An owner looks at examples; no check is changed |

Proposals are immutable, content-addressed blobs (`proposals/<id>/<evidence-digest>.json`). An unchanged situation
adds nothing on the next run. Each batch summary names the digest it currently observes for every proposal, and the
console shows that revision, so evidence that returns to an earlier state shows that state again. A proposal to revise a
newer base passage has a different identity from one already decided for an older base. Each proposal also carries up to ten **cases**, one per journey: journey id, outcome,
closed decline reason, review findings, approved passages found and the kept question. Cases sit outside the evidence
digest and readers never see their question text. The record does not say which style was tried before a draft was
refused, and the console says so.

## Drafting agent

`gg-gap-review` is a pinned, tool-free Foundry agent (`agents/gap_drafter.py`), called once for each new proposal
that has kept questions. Its output is untrusted until the code checks below pass, and an owner reviews it before
anything changes.

**Content.** The agent receives the situation, the example questions and the related approved passages. It
**analyses** the themes in the questions and what the passages do not cover, and **drafts** a passage from its own
knowledge, consistent with the existing passages. The draft may contain general facts, figures and dates because the
approved passages cannot supply content that is missing. It returns a closed JSON suggestion with the title,
questions answered, passage text, the passages it stayed consistent with, every claim it makes, and what it was
unsure of.

`gap_review.accept_suggestion` admits a suggestion only if it has the closed shape, cites only passage ids it was
given, holds no placeholder or steering, carries no figure typed by a member and no copied part of a member's
question, lists claims whenever there is text, and lists every figure of the text in a claim. A failed suggestion is
dropped and the idea stays without a draft.

The job does not vouch for the facts. An owner must verify each claim against an authoritative source and record
that source before the console accepts an approval. The source goes into the immutable journal, and the publisher
refuses an approval whose journal entry has none.

**Styles.** Counting finds the gap: a situation where no style, or only one, can explain. The agent then reads the
kept questions, why answers were declined, the styles that can already explain there and every approved pattern. It
returns the themes, why the current styles do not suit these questions, and a decision: `existing` (add a style on
this approved pattern, with a name) or `new` (no approved pattern fits, with a described idea for people to build).
`gap_review.accept_style_suggestion` admits it only if it names an approved pattern and holds no placeholder,
steering, figure or copied question from a member. The scope (stage, channel, mode) stays the gap situation and the
owner can edit it. The agent is offered only patterns that do not already explain in that situation, because a second
style on the same pattern adds no alternative. Without an agent answer a style idea is not offered, and the nearest-pattern rule is only the
scope scaffold that the agent's choice is applied to.

## Decisions and implementation

The console lists proposals under **Improvement ideas**. An owner approves, rejects, defers or acknowledges each one.
The decision is written to the immutable action journal before it takes effect and is implemented only through an
existing governed path:

- **Style.** The owner catalogue path (`/api/catalogue`) queues an exact catalogue release that the isolated P2 job
  signs and activates. Nothing changes until then. A `new` style idea can only be acknowledged, deferred or rejected,
  because the runtime can only use approved patterns.
- **Content.** The owner writes the passage; a revision starts from the approved text. The console validates it as
  an approved `ContentDocument` carrying the owner's `GB-` decision reference and writes it to `approved/` in the
  separate `content-approvals` container, after journalling the decision. The content publisher
  (`--from-approvals`, started through `run_publisher_job --approved-proposals`) first checks each approval against
  the immutable journal, so an approval without a matching journalled owner decision publishes nothing. It then
  publishes under its normal lease, retirement and verification rules and writes a `published/` marker. Each approval
  records the approved passage it was based on, and the publisher applies it only if that passage is still the live
  version. Otherwise it writes a `refused/` marker and applies nothing over the newer content, and the other approvals
  still publish. An interrupted batch resumes without reinstating anything: a version already in the feed with identical
  content counts as applied. The console never writes to Search or the approved-content container, and the gap review
  cannot write an approval.

Every listing the console, the job and the publisher rely on fails closed when it hits its cap (5,000 blobs), instead
of acting on part of the history.

Both improvement containers carry a 30-day immutability policy, so blobs are create-only by enforcement.

## Boundaries

- **B1/B2:** a drafted suggestion cannot carry a figure that a member typed, and every figure it contains is listed as
  a claim for the owner to verify against a recorded source.
- **B3/B8:** drafts reuse approved patterns, are inert, and activation is an owner (Tier 2) decision through the
  existing signed release or publisher.
- **B4:** member words are an input only to owner-reviewed proposals. They are kept briefly and never feed learning.
- **B7:** the situation has no demographic field.
