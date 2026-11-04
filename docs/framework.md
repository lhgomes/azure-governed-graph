# GovernedGraph framework concepts

GovernedGraph separates **what may be said** (deterministic and approved) from **how it is explained**
(generative, learned within bounds). This page describes the model; the boundaries it enforces are in
[boundaries-traceability](boundaries-traceability.md), the Azure realisation in [architecture](architecture.md).

## The idea in one paragraph

A member asks a question during a journey. Official values come only from a deterministic system of record.
Knowledge comes only from approved, versioned content. A generative agent drafts an explanation
around a **placeholder** (`{{value:<id>}}`) and never a figure. The host binds the official value, checks
numeric fidelity and advice-steering, records an explainability record and only then releases the answer.
Which *explanation strategy* (style, structure, teach-back) the agent uses is chosen from a small **approved
catalogue** by a **signed ranking snapshot** that offline learning may reorder within hard limits and a
fairness audit may veto for everyone.

## Two planes

| Plane | Purpose | Learns? |
|---|---|---|
| **P1 runtime** | Serve a member journey: orchestrator state machine, counsellor agent, values facade, approved retrieval, strategy service, explainability store | No |
| **P2 offline** | Re-rank approved strategies from immutable aggregate counts, audit cohorts for fairness, apply tiered approval, publish a signed snapshot | Yes, bounded |

There is no path from raw conversations or telemetry to prompts, templates, models or the catalogue (B4). P2 reads aggregates only.

## Roles

- **Orchestrator**: owns journey state, authorization, escalation and audit-before-release. Human escalation is reachable from every state (B9).
- **Counsellor** (generative): drafts a question-specific explanation using placeholders and approved passages. Cannot compute, round or select outside the served ranking.
- **Intent routing, understanding assessment, answer-quality judge** (generative, tool-free): closed semantic decisions and one bounded repair.
- **Strategy Service**: verifies and serves the signed Ranking Snapshot; applies suspension overlays at read time for all members (B3, B6).
- **Fairness & Explainability Agent** (protective only): audits segregated cohort aggregates and can suspend a strategy for every member (B6).
- **Assurance committee and tiers**: bounded ranking moves are autonomous (Tier 0/1); anything that widens what the system may say, use or decide is a human Tier 2 decision (B8).
- **Control arm and kill switch**: static expert ranking is retained and the graph must keep beating it, otherwise treatment reverts (B10).

## Protective versus widening actions

Protective actions (suspend, freeze, escalate) are always autonomous. Anything that widens the approved catalogue,
the content, the data used or the decisions made requires human approval and, for reinstatement, a governance board reference.

## What a domain supplies

See [adopting](adopting.md). In short: an approved strategy catalogue, templates with placeholders, approved
content, expert priors, fairness thresholds and cohort dimensions, a deterministic values source, and the counsellor prompt.
