# GovernedGraph
### A framework for governed journey graphs. AI that improves with use and stays safe: bounded, auditable, fair.

> **Efficiency is learned. Fairness is enforced.**

Organisations want AI to help people understand official information (a balance, a status, a rule), but cannot let a model
*decide*, *calculate* or *advise*. GovernedGraph is a reference framework for exactly that: a generative model **explains**,
your deterministic systems **decide**, and a learning loop improves **how** things are explained, inside hard, auditable limits.

> **Terminology.** "Member" is the framework's word for the person being guided; read it as customer, participant, patient,
> citizen or whatever your domain calls them. "Journey" is a multi-step interaction with your organisation, and "official values"
> are any authoritative figure, status or outcome held in a system of record.

## The three ideas

1. **Facts never come from the model.** Official values come from your system of record. The model writes a placeholder such as
   `{{value:<id>}}`; the host fills in the real value and checks it.
2. **Only approved strategies run, and learning only reorders them.** Offline learning ranks a catalogue of approved
   explanation styles within hard limits. It never writes content and never reads raw conversations.
3. **Fairness can veto, and humans approve anything that widens.** A cohort audit can remove a strategy for every member.
   Protective actions (suspend, freeze, escalate) are autonomous; anything that widens what the system may say, use or decide
   needs a human.

## How it works

[![How GovernedGraph works: a member's question passes three gates in the framework, using a domain pack and values source that you supply; a separate bounded learning loop and an administration console keep people in charge](docs/diagrams/overview.svg)](docs/diagrams/overview.svg)

*Select the diagram for the full-size view. For a step-by-step walkthrough of one answer and a glossary, see
[getting started](docs/getting-started.md); for every agent and gate, see the
[detailed agent workflow](docs/diagrams/agent-workflow.md).*

## Framework, your domain and the sample

| Part | What it is | Where |
|---|---|---|
| **GovernedGraph** (the framework) | Journey state machine, guardrails, strategy service, learning, fairness, administration console and Azure adapters. Enforces the ten boundaries | [src/governedgraph](src/governedgraph) |
| **Your domain** | A **domain pack** (strategies, templates, prompts, vocabulary, approved content, fairness thresholds) and a **values source** (read-only access to your system of record) | `examples/<your_package>` |
| **The sample** | A complete worked domain (pension-transition guidance for a synthetic Dutch sector fund), bundled so the repository runs out of the box. Synthetic data only | [examples/pension_wtp](examples/pension_wtp) |

The framework never imports a sample ([tested](tests/test_layering.py)). One setting, `GOVERNEDGRAPH_SAMPLE`, chooses which
domain the repository runs; it defaults to the bundled sample.

> **ILLUSTRATIVE - synthetic data.** All sample data is synthetic; no real organisation, member or personal data is used.
> The sample gives guidance, not financial or legal advice.

## Try it (offline, 5 minutes)

Python 3.14. No Azure account needed.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export GOVERNEDGRAPH_MODE=local PYTHONPATH=src:examples
pytest                                                  # boundary and integration tests
python evals/run_evals.py                               # evaluation hard gates
python -m pension_wtp.demo.run_demo                     # a scripted journey, end to end
```

Then look at the evidence in the [administration console](docs/administration.md): strategies and rankings, vetoes,
suspensions, governance decisions, learning evidence and journeys, with audited protective actions (freeze and suspend only).

## Use it for your own solution

1. Copy `examples/pension_wtp` to `examples/my_domain` and rename the package.
2. `export GOVERNEDGRAPH_SAMPLE=my_domain`.
3. Replace the domain pack and the values source with yours.
4. Run `python evals/run_evals.py` and `pytest`; the hard gates must pass.

Nothing under `src/governedgraph` changes. See [adopting](docs/adopting.md) for the pack layout, what you change and never
change, and the current limits.

## Run it on Azure

```bash
python -m scripts.deploy        # guided, resumable; deploys everything including the administration console
python -m scripts.teardown      # guided removal: resource group, quota release, Entra objects, local state
```

One run provisions the reference solution (`azd`, Bicep, managed identities, private backends) and the Entra sign-in for the
console, then verifies it. `--plan` on either command shows what it would do and changes nothing. See [deploying](docs/deploying.md).
The synthetic contact centre and the member-token issuer are development stand-ins; production needs reviewed identity,
administrator, contact-centre and content-governance adapters.

## The ten boundaries

These are the framework's contract. Each has a code enforcement point and a regression test
(see [architecture](docs/architecture.md)); treat weakening any of them as a defect.

| ID | Boundary |
|---|---|
| B1 | Authoritative values and determinations (calculations, rule-based outcomes, official records) stay in deterministic systems (read-only, behind a gateway) |
| B2 | Generative AI may explain an authoritative value but never calculates, rounds, estimates or writes one (placeholder binding, 100% numeric-fidelity gate) |
| B3 | Runtime selects only from an approved, versioned strategy catalogue (signed ranking snapshot) |
| B4 | Raw conversations never retrain a model or change behaviour |
| B5 | Learning is bounded continuous ranking of approved strategies only (minimum sample, maximum rank shift, reorder-only) |
| B6 | Fairness can veto a high-performing strategy, for all members |
| B7 | No runtime demographics; demographics exist only in a segregated cohort zone for aggregate audit |
| B8 | Human approval for material change, not for bounded ranking adjustments |
| B9 | Human escalation is always available |
| B10 | The graph must continuously beat governed RAG with static expert ranking (control arm and kill switch) |

Also: guidance, not advice (option-level questions escalate), and an AI disclosure at the start of every session.

## What this is not

A synthetic reference, **not a production service**. Passing local gates is not evidence of conversational quality, live
integration or production acceptance. See the [limits to state with any result](docs/framework.md#limits-to-state-with-any-result).

## Documentation map

| Start here | |
|---|---|
| [Getting started](docs/getting-started.md) | Run it, how one answer is produced, glossary, repository map |
| [Adopting GovernedGraph](docs/adopting.md) | Build on your own domain |
| [Framework concepts](docs/framework.md) | Planes, roles, components, limits |

| Go deeper | |
|---|---|
| [Architecture](docs/architecture.md) | Runtime and offline flows, persistence, security, production seams |
| [Administration console](docs/administration.md) | Rankings, vetoes, suspensions, decisions, conversations; protective actions and access control |
| [Deploying](docs/deploying.md) | Staged Azure deployment and teardown |
| [Evaluation method](docs/evaluation.md) | Hard gates, model comparison and what a result can claim |
| [Agent workflow diagram](docs/diagrams/agent-workflow.md) | The detailed agents-and-gates diagram, step-by-step explanation and editable Mermaid sources |

| The sample | |
|---|---|
| [Demo runbook](examples/pension_wtp/docs/demo-runbook.md) | Local rehearsal and authorized Azure demo setup |
| [Member authentication](examples/pension_wtp/docs/api/member-authentication.md), [handover integration](examples/pension_wtp/docs/api/contact-centre.md) | Sample integration contracts |

## Licence

[Apache-2.0](LICENSE); see [NOTICE](NOTICE). Synthetic sample data and content are illustrative only.
