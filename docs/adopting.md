# Adopting GovernedGraph for your domain

The bundled sample ([examples/pension_wtp](../examples/pension_wtp)) is the template. A domain adopts the
framework by supplying a **domain pack** and a **deterministic values source**, without touching the
boundary enforcement points in `src/governedgraph`.

## 1. Domain pack (`GOVERNEDGRAPH_DATA_DIR`)

Point `GOVERNEDGRAPH_DATA_DIR` at a directory with this layout (see the sample's `data/` for working files):

| File | Contents | Change control |
|---|---|---|
| `catalogue/strategy_catalogue_v1.json` | Approved, versioned explanation strategies | Tier 2 (B3, B8) |
| `catalogue/templates_v1.json` | Fallback templates and the session-opening AI `disclosure`; placeholders only, never figures | Tier 2 |
| `catalogue/expert_priors_v1.json` | Static expert ranking per request key (also the control arm) | Tier 2 (B10) |
| `catalogue/prompts/{counsellor,intent,understanding,answer-review}.md` | The four agent prompts | Tier 2 |
| `catalogue/vocabulary_v1.json` | Closed vocabulary: explanation intents and reasons, concepts, answer goals, languages and step labels, domain advice patterns, fixed member-facing messages, speech vocabulary, search probe, demo/proof profile, benchmark questions | Tier 2 |
| `catalogue/ui/presenter.html` | Optional presenter page for the hosted voice demo | Sample asset |
| `content/approved_content.jsonl` | Approved, versioned passages for retrieval | Content governance |
| `fairness/thresholds_v1.json` | Minimum samples, maximum rank shift, cohort-gap limit, audit dimensions | Tier 2 (B5, B6) |
| `fairness/cohort_aggregates_*.csv` | Aggregate cohort counts, kept in the segregated cohort zone (B7) | Aggregate only |
| `synthetic/outcome_aggregates_*.csv` | Aggregate outcome counts that drive ranking (B4, B5) | Aggregate only |
| `personas/personas.json` | Simulator personas for evaluation; never passed to the runtime agent | Test assets |

No runtime file may carry a demographic attribute. Closed schemas in `governedgraph.common.contracts` reject unknown fields.
`governedgraph.common.domain` validates the pack when it is first loaded and fails closed: a missing prompt, an
unknown concept in an answer goal, an invalid advice pattern or an incomplete message set stops the service instead
of falling back to permissive defaults.

The framework owns the routes that protect the boundaries: option-level questions escalate (B9), a human is always
reachable, and the comprehension loop is host-controlled. A pack adds explanation intents; it cannot remove those routes.

## 2. Deterministic values source (`GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER`)

Set `GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER` to `module:callable` implementing
`get_member_values(member_ref, authenticated_member_ref) -> MemberValues`. It must be read-only, scope every call to the
authenticated member and return display strings from the system of record. In Azure mode, values come through the
API gateway facade (`MEMBER_VALUES_API_URL`); put your system of record behind it, as the sample's
[member-values API](../examples/pension_wtp/member_values_api) does for its synthetic data.

## 3. Domain text

The prompts, closed vocabulary and fixed member-facing wording live in the pack (see the table above), not in
framework code. Replace them for your domain, keep the boundary rules in the prompts, and re-run the evaluation hard
gates. `tests/test_domain_pack.py` fails if sample-domain wording appears in `src/governedgraph`, and Foundry agent
definitions are recreated from the pack, so a prompt change produces a new agent version for approval (Tier 2).
Evaluation personas and datasets also belong to your pack.
Sample-specific evaluation code and cases belong to the sample, not the framework's deployment script. The reference
sample declares its local tests, evaluation/demo entry points and required deployed channel modes in
`examples/pension_wtp/deployment.json`; `scripts/deploy.py` runs that definition automatically. Framework B1-B10 tests
stay in `tests/` and cannot be removed or weakened by a sample definition.

## 4. Verify

```bash
export PYTHONPATH=src:examples
pytest
python evals/run_evals.py
```

All hard gates must pass: numeric fidelity 100%, zero option recommendations, demographic invariance.
`tests/test_layering.py` fails if framework code imports a sample or domain module.

## Current limits

- The strategy catalogue ids (S1-S6) and the demo/dev acceptance proofs (`governedgraph.dev`, `voice/probe.py`) assume the
  sample's strategy set; a new pack with different strategies needs matching proof expectations.
- The member reference format (`MBR-...`) and the `en`-always-supported rule are framework conventions.
- The Azure deployment under `infra/` is a reference for the sample topology; adapt names, SKUs and identities to your environment.
- The synthetic contact-centre and member-token issuer are development stand-ins for real adapters.
