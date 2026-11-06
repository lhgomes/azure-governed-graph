# Adopting GovernedGraph for your domain

You supply two things and change nothing in `src/governedgraph`: a **domain pack** (what may be said and how it is
explained) and a **values source** (where official figures and outcomes come from). New to the project? Read
[getting started](getting-started.md) first.

## The path

1. **Copy the sample.** `cp -r examples/pension_wtp examples/my_domain`, then replace the package name `pension_wtp` with
   `my_domain` in the copied `.py` and `.json` files (about twenty references across ten files: imports and `deployment.json`).
2. **Select it.** `export GOVERNEDGRAPH_SAMPLE=my_domain`. One setting points the data directory, values provider and demo at
   your package. Each can still be overridden alone (`GOVERNEDGRAPH_DATA_DIR`, `GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER`,
   `GOVERNEDGRAPH_TEXT_DEMO_MODULE`).
3. **Replace the domain pack** in `examples/my_domain/data/` (section 1 below): strategies, templates, prompts, vocabulary,
   approved content, expert priors, fairness thresholds.
4. **Replace the values source** in `examples/my_domain/member_values_api/data.py` (section 2): read-only, scoped to the
   authenticated member, returning display strings from your system of record.
5. **Run the gates:** `python evals/run_evals.py` and `pytest`. Numeric fidelity 100%, zero option recommendations and
   demographic invariance must all pass (section 4).
6. **Deploy** with `python -m scripts.deploy --sample-definition examples/my_domain/deployment.json`. The runner builds the
   images and configures the infrastructure for your package automatically; check the
   [Azure image contract](#azure-image-contract) first.

This path is exercised: a renamed copy of the sample, selected with `GOVERNEDGRAPH_SAMPLE`, runs the demo and passes the
evaluation gates.

| You change | You never change |
|---|---|
| Everything under `examples/my_domain`: pack, values source, demo, evaluation cases, deployment definition, release approval | Anything under `src/governedgraph`, in particular the enforcement points of boundaries B1-B10 |
| Your own tests, personas and acceptance expectations | The framework tests in `tests/`; a sample definition cannot remove or weaken them |

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

## Azure image contract

The container build is sample-agnostic: `SAMPLE` (the package under examples/) is passed to every image build (`azure.yaml`
`buildArgs`, `az acr build` in the deploy runner) and to Bicep (`sampleName`), and the deploy runner sets it from your
`--sample-definition`. The images contain only the files below, so your package needs them at these paths (the sample's
names, which the [Dockerfile](../Dockerfile) copies):

| Image | Files copied from `examples/<your_package>/` |
|---|---|
| All | `__init__.py`, `data/catalogue/`, `data/content/`, `data/fairness/thresholds_v1.json` |
| Runtime (orchestrator, strategy, values, contact centre, console) | `data/synthetic/members.json`, `member_values_api/` (the member-values app starts `<package>.member_values_api.app:app`) |
| Learning fixtures job | `data/synthetic/outcome_aggregates_illustrative.csv`, `data/synthetic/uplift_window_illustrative.json`, `data/fairness/cohort_aggregates_illustrative.csv` |
| Voice proof | `data/personas/personas.json` |

A missing file makes the image build fail before anything is deployed. Cohorts and personas stay out of the runtime
images (tested). To use other file names, edit the Dockerfile; `tests/test_sample_selection.py` expands its `COPY` lines
and fails if a source does not exist for the bundled sample.

## Current limits

- The strategy catalogue ids (S1-S6) and the demo/dev acceptance proofs (`governedgraph.dev`, `voice/probe.py`) assume the
  sample's strategy set; a new pack with different strategies needs matching proof expectations.
- The member reference format (`MBR-...`) and the `en`-always-supported rule are framework conventions.
- The Azure deployment under `infra/` is a reference for the sample topology; adapt names, SKUs and identities to your environment.
- The synthetic contact-centre and member-token issuer are development stand-ins for real adapters.
