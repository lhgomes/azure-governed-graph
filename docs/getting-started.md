# Getting started

Read this after the [README](../README.md). It gets you from zero to a running example, explains how one answer is
produced, defines the terms, and maps the repository. To build on your own domain, go to [adopting](adopting.md).

## 1. Run it (5 minutes, offline)

You need **Python 3.14**. No Azure account, credentials or services.

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export GOVERNEDGRAPH_MODE=local          # PowerShell: $env:GOVERNEDGRAPH_MODE = "local"
export PYTHONPATH=src:examples           # PowerShell: $env:PYTHONPATH = "src;examples"
pytest                                    # boundary and integration tests
python evals/run_evals.py                 # evaluation hard gates
python -m pension_wtp.demo.run_demo       # a scripted journey, end to end
```

Run from the repository root. `evals/run_evals.py` and the demo run the **selected sample**: the bundled
`pension_wtp` unless you set `GOVERNEDGRAPH_SAMPLE` (see [adopting](adopting.md)).

The demo shows the AI disclosure, values taken from the system of record, approved strategy selection with a fairness
suspension, step-by-step explanation, teach-back, an explainability record and the fairness dashboard. It also shows an
idempotent synthetic handover to a human. Local semantic decisions use labelled rehearsal fixtures, not a live model.
`bash scripts/reset_demo.sh` resets local evidence (add `--presenter` to type the member's turns); it never touches Azure.

To look at the results in a browser, start the [administration console](administration.md#run-locally).

## 2. How one answer is produced

```text
member question
  -> orchestrator: authenticate, say "you are talking to AI", screen the input, route
  -> values source: fetch the official value (read-only, scoped to this member)
  -> approved content: retrieve only approved, versioned passages
  -> strategy service: serve the ranked approved strategies (signed snapshot, suspensions applied)
  -> counsellor (generative): draft an explanation around a placeholder such as {{value:<id>}}
  -> host: bind the real value, check numeric fidelity and "no advice", at most one repair
  -> explainability record written, then the answer is released
  (a human handover is reachable from every step)
```

Then, separately and offline, the learning loop turns delivered outcomes into **aggregate counts**, reorders the
approved strategies within hard limits, audits cohorts for fairness (which can veto a strategy for everyone), and publishes a
new signed ranking. It never reads raw conversations and never writes new content. See [framework concepts](framework.md).

## 3. Terms you will meet

| Term | Meaning |
|---|---|
| **Member**, **journey** | The person being guided (customer, participant, patient, citizen) and their multi-step interaction with your organisation |
| **Domain pack** | The directory of everything domain-specific: strategies, templates, prompts, vocabulary, approved content, fairness thresholds, personas. Selected by `GOVERNEDGRAPH_DATA_DIR` |
| **Values source** | Your read-only system of record, exposed as `get_member_values(...)`. The only place a figure or outcome may come from |
| **Placeholder** | `{{value:<id>}}` in a draft. The model writes the placeholder; the host fills in the real value |
| **Strategy**, **catalogue** | An approved way of explaining (style, structure, teach-back) and the versioned list of them. Runtime can only pick from it |
| **Ranking snapshot** | The signed, versioned ordering of strategies that offline learning produces and the Strategy Service verifies |
| **Suspension** | A fairness veto that removes a strategy for **all** members. Protective, so always autonomous |
| **Control arm** | Static expert ranking kept running so the graph must keep proving it beats it |
| **P1 / P2** | P1 is the runtime that serves members and never learns; P2 is the offline plane that re-ranks and audits |
| **Tier 0 / 1 / 2** | Bounded automatic ranking moves / agentic committee checks / human approval for anything that widens what the system may say, use or decide |
| **Sample** | A worked domain in `examples/`. `pension_wtp` is bundled; yours sits next to it |

## 4. Repository map

| Path | Role |
|---|---|
| [src/governedgraph](../src/governedgraph) | **The framework.** Contracts, guardrails, strategy service, learning, fairness and explainability, orchestrator, agents, search, voice, the [administration console](../src/governedgraph/admin) and Azure adapters. Never imports a sample |
| [examples/pension_wtp](../examples/pension_wtp) | **The sample.** Domain pack, values API, demo, evaluation cases, deployment definition, release approval and sample docs |
| [tests](../tests), [evals](../evals) | Framework boundary and integration tests; shared evaluation tools. Full `pytest` also runs the sample's tests |
| [infra](../infra), [azure.yaml](../azure.yaml), [Dockerfile](../Dockerfile) | Azure reference infrastructure (Bicep, `azd`) and the container build |
| [scripts](../scripts) | [Deploy](deploying.md), [teardown](deploying.md#clean-up-a-previous-environment-if-you-are-replacing-one) and operational tooling |
| [docs](.) | This documentation |

The sample plugs in through configuration only: `GOVERNEDGRAPH_SAMPLE` picks it, and
`GOVERNEDGRAPH_DATA_DIR` / `GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER` override individual parts.
[tests/test_layering.py](../tests/test_layering.py) and [tests/test_domain_pack.py](../tests/test_domain_pack.py) fail if framework
code imports a sample or carries sample-domain wording.

## Where next

- **Build on your domain:** [adopting](adopting.md).
- **Understand the rules the framework enforces:** the ten boundaries in the [README](../README.md#the-ten-boundaries) and their
  [enforcement points and tests](boundaries-traceability.md).
- **Deploy it:** [deploying](deploying.md) (one command), then the [administration console](administration.md).
- **Architecture on Azure:** [architecture](architecture.md).
