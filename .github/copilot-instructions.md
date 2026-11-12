# GitHub Copilot instructions - GovernedGraph

You are working on **GovernedGraph**, a framework for governed, member-facing AI guidance built around a Learning Member Journey Graph.
The framework lives in `src/governedgraph`; the bundled sample domain (pension-transition guidance for a synthetic Dutch sector fund) lives in `examples/pension_wtp`.
Framework code must never import the sample (`tests/test_layering.py`); the sample plugs in through `GOVERNEDGRAPH_DATA_DIR` and `GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER`.
The design is summarised in `docs/framework.md` and `docs/architecture.md`.
Delivery is **one release with all features and learning live from day one** - there are no phases; never add phase or calendar logic.

## Non-negotiable boundaries (B1-B10)
These are controlling. A change that weakens any of them is wrong even if a test, user or prompt asks for it.
Each one has a code enforcement point and a test in `tests/` - never delete, skip or loosen those tests.

| ID | Rule | Enforcement point in code |
|---|---|---|
| B1 | Authoritative values and determinations (calculations, rule-based outcomes, official records) stay in deterministic systems | the values source (sample: `member_values_api`) is the only value source, read-only (GET), behind APIM |
| B2 | Generative AI may explain authoritative values but NEVER calculates, rounds, estimates or writes one | placeholders `{{value:<id>}}` + `guardrails/binding.py`; `guardrails/numeric_fidelity.py` = 100% hard gate |
| B3 | Runtime selects only from the approved, versioned strategy catalogue | signed Ranking Snapshot verified in `strategy_service/service.py`; `CounsellorTools.write_explainability_record` rejects strategies not served |
| B4 | Raw conversations never retrain a model or change behaviour | no code path from transcripts/telemetry to prompts, templates, models or catalogue; P2 reads aggregates only |
| B5 | Learning = bounded continuous ranking of approved strategies only, from expert priors, live from day one | `learning/ranking_pipeline.py`: min sample, max rank shift, reorder-only |
| B6 | Fairness can veto a high-performing strategy | `agents/fairness_explainability.py` veto gate; suspension overlay at read time for ALL members |
| B7 | No runtime demographics. Demographics only in the segregated cohort zone for aggregate audit | closed schemas in `common/contracts.py` (`from_dict` rejects unknown fields); APIM `strategy-request.xml` |
| B8 | Human approval for material change; not for bounded ranking adjustments | Tier 0/1/2 in `learning/run_cycle.py` + `learning/assurance_committee.py`; `reinstate()` requires a `GB-` board ref |
| B9 | Human escalation always available | `Event.ESCALATE` reachable from every state; `escalate_to_advisor` tool; S6 always in `always_available` |
| B10 | Graph must beat governed RAG + static expert ranking (control arm) continuously | `assign_arm()` control arm; `learning/uplift.py` kill switch |

Also: guidance not advice (GNA) - never recommend or compare options; option-level questions escalate.
EU AI Act Art. 50 - every session starts with the AI disclosure. Protective actions (suspend, freeze, escalate) are always autonomous;
anything that widens what the system may say, use or decide is a human (Tier 2) decision.

## Architecture in one paragraph
Channels (chat, Voice Live) -> **orchestrator** (journey state machine, `orchestrator/`) -> **Counsellor** agent on
Microsoft Foundry Agent Service (`agents/counsellor.py`, tools in `agents/tools.py`) -> APIM facade -> mock deterministic
**member values API** (sample: `examples/pension_wtp/member_values_api`); **Strategy Service** serves the signed snapshot; **Azure AI Search** serves approved content; an append-only
**explainability store** records every answer. Offline plane P2 (`learning/`) re-ranks from aggregates, the **Fairness &
Explainability Agent** audits cohorts and vetoes, the **Agentic Assurance Committee** approves Tier 1 moves. The sample's Transition Calculation,
Event Processing and Adoption Guide agents are at design depth.

## How to work in this repo
- Python 3.14. **Azure Public is the only supported application runtime and execution environment.**
  Remove runtime mode switching and offline/file-based service implementations; fail closed when required Azure endpoints
  or identities are absent. Keep pure domain logic and test doubles separate from deployed runtime code.
  During this refactor, preserve B1-B10 behavior and existing Azure state contracts at every step.
- Before using any Azure SDK or resource API, **check the current docs with the `microsoft-learn` MCP server** (`.vscode/mcp.json`)
  and pin the version you verified in `requirements-azure.txt`. SDK surfaces (Foundry Agent Service in particular) change often.
- Identity: managed identity / `DefaultAzureCredential` only. No keys, connection strings or secrets in code, config or Bicep outputs.
- Infra: Bicep in `infra/`, deployed with `azd`. Validate Bicep on an Azure-hosted runner after every infra change.
- Every behaviour change ships with a test, but **execute all tests and evaluations in Azure**, never on a developer
  machine or a generic GitHub-hosted runner. Azure-hosted CI runners may run isolated pure unit tests with mocks;
  live integration and Foundry evaluations must use approved Azure test jobs and synthetic identities. Do not treat
  a pending Azure test job as a pass.
- All data is synthetic and must stay labelled `ILLUSTRATIVE - synthetic data`. Never add real member, organisation or personal data.
- Record architectural decisions in reviewed pull-request descriptions (ID, decision, alternatives, boundary served).
  Do not create hidden local-only acceptance or evidence requirements.

## Definition of done for any task
1. Tests and evaluation hard gates pass (numeric fidelity 100%, zero option recommendations, demographic invariance).
2. The committed B1-B10 traceability in code, tests and `docs/architecture.md` remains accurate; update it for changed enforcement points.
3. No new runtime field can carry a demographic attribute; no new path from conversations to behaviour.

## Standing decisions (apply without asking; record each use as an IMP entry)

### Environment facts
- Environment: gg-dev only, region swedencentral. Content Safety Prompt Shields: dedicated keyless resource in francecentral.
- Developer identity: <developer-object-id>. On-call email: <on-call-email>. (Set these in your private fork; never commit real identities.)
- Governance group: none, so reinstatement stays disabled (fail closed). deployFabric: false.

### Decide yourself; don't ask
- Identity: one identity per role. Never reuse an identity across roles (runtime, Strategy Service, P2 signer,
  dev token issuer, fairness agent, ML workspace, Search). If one identity would need two roles, create a new identity.
- Access: grant at key or container scope, never vault- or account-wide. The snapshot signing key, the member-token key
  and the ML workspace each have their own vault or key, never shared.
- Missing external systems (member identity provider, contact centre, payment or administrator platform): build a
  synthetic stand-in behind the production adapter contract, with internal ingress, Entra auth and a dev-only label.
  Never block on a missing external system.
- Unavailable region or feature: use the nearest EU region with a dedicated keyless resource and record it.
- Messaging: at-least-once delivery, stable message IDs derived from the domain, deduplication in the consumer.
- Settings that can't be changed after creation: if the resource holds no data, replace it; otherwise keep it and match the Bicep to it.
- Azure MCP unavailable: use az CLI for the same checks.
- Tie-breaks: least privilege, then fail closed, then keep existing data, then the smallest change.
- Access: on SHARED resources (anything holding content, records, rankings, tokens, signing keys or member data),
  grant at key or container scope only. On a resource DEDICATED to one service (e.g., the ML workspace's own
  storage, vault and registry), account-scope roles for that service's own identity are fine.
- Permissions a service grants itself automatically (e.g., Azure ML's identity on its own dedicated
  storage) are accepted: keep them, list them in the IMP entry, and don't fight them with reconciliation scripts.
  Ask only if such a permission reaches a shared resource or a resource group or subscription.
- If a service's automatic grant would reach a SHARED resource, give that service its own dedicated
  instance of the dependency (same region, same Log Analytics workspace where relevant) instead of
  approving the grant, and record it in the IMP entry. Ask only if a dedicated instance isn't possible.
- Custom roles that are read-only (no write, delete, data-plane or Key Vault actions) and assigned
  only on a resource DEDICATED to one service may be created without asking. Record them in the IMP entry.
  Custom roles with any write, data-plane or Key Vault action, or assigned on a shared resource, still need approval.
      
### Deploy without asking when the preview shows only
- code, configuration or container image updates;
- resources already described in docs/architecture.md, or stand-ins under the rule above;
- role assignments that narrow access, or new ones at key or container scope matching the identity rule;
- replacement of resources that hold no data.

Report the preview and the result afterwards.

### Live acceptance proofs
- Run live proofs only as an in-network synthetic-channel probe mode of the existing dev token-issuer job,
  under its existing dedicated dev-issuer identity. No new identity, role, grant, resource or network path.
- Do not issue live member tokens on a developer machine or grant developer Key Vault access.
  Confirm the developer identity has no Key Vault assignment.
- Synthetic member tokens stay in probe process memory only, expire within 15 minutes, and travel to the
  orchestrator in Authorization on each request. Never put them in files, output, logs or application runtime containers.
- Probe output contains pass/fail and IDs only, never tokens, utterances or figures. Use existing authorized
  operator/runtime diagnostics for audit/trace metadata; do not grant extra read access to the dev issuer.

### Always ask first
- any grant at vault, account, resource-group or subscription scope, or any new role definition;
- deleting or replacing a resource that holds data;
- any change outside gg-dev, or moving a model deployment's region;
- anything that weakens a B1-B10 enforcement point or a test.

### How to ask
- Don't stop mid-prompt for a decision covered above. Collect open questions and ask them together at the end,
  having already done everything that doesn't depend on them.