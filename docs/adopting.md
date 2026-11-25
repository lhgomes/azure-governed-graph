# Adopting GovernedGraph for your domain

You supply two things and change nothing in `src/governedgraph`: a **domain pack** (what may be said and how it is
explained) and a **values source** (where official figures and outcomes come from). New to the project? Read
[getting started](getting-started.md) first.

## The path

1. **Copy the sample.** `cp -r examples/pension_wtp examples/my_domain`, then replace the package name `pension_wtp` with
   `my_domain` in its Python, domain JSON, overlay Dockerfile and Bicep composition.
2. **Supply explicit inputs.** Set `GOVERNEDGRAPH_DATA_DIR` through your Azure
   deployment composition and configure the APIM-backed values endpoint.
   The framework supplies no implicit sample or local-runtime fallback.
3. **Replace the domain pack** in `examples/my_domain/data/` (section 1 below): strategies, templates, prompts, vocabulary,
   approved content, expert priors, fairness thresholds.
4. **Replace the values source** in `examples/my_domain/member_values_api/data.py` (section 2): read-only, scoped to the
   authenticated member, returning display strings from your system of record.
5. **Prepare validation:** domain-owned evaluation cases must cover numeric fidelity
   100%, zero option recommendations and demographic invariance (section 4).
6. **Deploy** with `python -m scripts.deploy --inputs .azure/private-delivery.json`,
   selecting `examples/my_domain/deployment.json` in its `composition` field. The explicit extension owns its overlays and
   workload composition; check the
   [Azure image contract](#azure-image-contract) first.

Validate the renamed composition and its domain expectations in your authorized
Azure environment; copying the sample does not certify adoption.

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
| `catalogue/prompts/counsellor.md` and `catalogue/vocabulary_v1.json` `decision_criteria` | The counsellor prompt, and the descriptions of every intent route (`asks_person`, `asks_choice`, `asks_information`, `only_acknowledges`), explanation topic, teachable concept and answer-review label (`pass_draft`, `advice`, `unsupported`, `off_target`); `decision_criteria` has exactly the groups `intent`, `concepts`, `review` and `route` sent to the Decision model | Tier 2 |
| `catalogue/vocabulary_v1.json` | Closed vocabulary: explanation intents and reasons, concepts, answer goals, languages, step labels, `source_talk` phrases (how a reply must not refer to its own sources, per language), domain advice patterns, fixed member-facing messages, speech vocabulary, search probe, demo/proof profile, benchmark questions | Tier 2 |
| `catalogue/ui/presenter.html` | Optional presenter page for the hosted voice demo | Sample asset |
| `content/approved_content.jsonl` | Approved, versioned passages for retrieval | Content governance |
| `content/public_sources_v1.json` | Source register for passages summarised from public pages: URL, licence note and which passage cites it. Provenance for owner review, not approval evidence | Content governance |
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

## 2. Deterministic values source

Values come through the API gateway facade (`MEMBER_VALUES_API_URL`). Your adapter
must be read-only, scope every call to the authenticated member and return official
display strings from the system of record. Put that source behind the facade, as the sample's
[member-values API](../examples/pension_wtp/member_values_api) does for its synthetic data.

## 3. Domain text

The prompts, closed vocabulary and fixed member-facing wording live in the pack (see the table above), not in
framework code. Replace them for your domain, keep the boundary rules in the prompts, and re-run the evaluation hard
gates. `tests/test_domain_pack.py` fails if sample-domain wording appears in `src/governedgraph`, and Foundry agent
definitions are recreated from the pack, so a prompt change publishes a new agent version, which is served at once
because runtime calls agents by name. Review prompt changes in the pull request, as they widen what the system may say.
Evaluation personas and datasets also belong to your pack.
The fixed `messages.answer_unavailable` acknowledgment is required for internal
answer failures; it must not ask the member to repeat a question already routed
for explanation or claim that the question itself was unsafe. The runtime
appends the approved human-help offer. Keep `messages.clarify_question` for
genuine intent clarification; an unclear reply to a teach-back uses the approved
`acknowledgements.comprehension_retry` wording (per response language), which refers back
to the last explanation instead of asking a new question.
Sample-specific evaluation code and cases belong to the sample, not the framework's deployment script. The reference
sample keeps domain tests and evaluation cases under its own directory. Its
`deployment.json` declares domain/approved composition inputs only, never test or proof policy. Framework B1-B10 tests
stay in `tests/` and cannot be removed or weakened by a sample definition.

## 4. Verify

Run regression and evaluation only in a separately authorized Azure environment,
using explicit domain inputs and the selected immutable release.
See [evaluation](evaluation.md) for live probes and retained harness limitations.
All hard gates must pass: numeric fidelity 100%, zero option recommendations, demographic invariance.
`tests/test_layering.py` fails if framework code imports a sample or domain module.

## Azure image contract

The framework [Dockerfile](../Dockerfile) builds `runtime`, and [Dockerfile.p2](../Dockerfile.p2)
builds `p2`, without sample defaults or files. Your extension supplies matching
runtime/P2 overlay Dockerfiles and a Bicep composition. Build arguments
`FRAMEWORK_RUNTIME` and `FRAMEWORK_P2` are immutable base-image references, not sample selectors.
The explicit extension composition supplies the data directory and hosted values application.
Azure runtime refuses an absent data-directory input. The reference overlays contain:

| Image | Files copied from `examples/<your_package>/` |
|---|---|
| All | `__init__.py`, `data/catalogue/`, `data/content/`, `data/fairness/thresholds_v1.json` |
| Runtime (orchestrator, strategy, values, contact centre, console) | `data/synthetic/members.json`, `member_values_api/` (the member-values app starts `<package>.member_values_api.app:app`) |
| Learning fixtures job | `data/synthetic/outcome_aggregates_illustrative.csv`, `data/synthetic/uplift_window_illustrative.json`, `data/fairness/cohort_aggregates_illustrative.csv` |
| Voice proof | `data/personas/personas.json` |

A missing file makes the image build fail before anything is deployed. Cohorts and personas stay out of the runtime
images (tested). To use other file names, edit your overlay Dockerfile;
`examples/pension_wtp/tests/test_delivery_selection.py` expands the reference `COPY` lines
and fails if a source does not exist for the bundled sample.

## Current limits

- The strategy catalogue ids (S1-S6) and the demo/dev acceptance proofs (`governedgraph.dev`, `voice/probe.py`) assume the
  sample's strategy set; a new pack with different strategies needs matching proof expectations.
- The member reference format (`MBR-...`) and the `en`-always-supported rule are framework conventions.
- The Azure deployment under `infra/` is a reference for the sample topology; adapt names, SKUs and identities to your environment.
- The synthetic contact-centre and member-token issuer are development stand-ins for real adapters.
