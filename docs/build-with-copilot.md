# Building with GitHub Copilot in VS Code

## One-time setup
1. Unzip, `git init`, commit, and push to a new GitHub repository (private until you decide to publish it as the D10 reference repo).
2. Open the folder in VS Code; install the recommended extensions (`.vscode/extensions.json`).
3. Open Copilot Chat -> switch to **Agent** mode. Copilot automatically applies `.github/copilot-instructions.md` and the
   path-specific files in `.github/instructions/`.
4. Start the MCP servers in `.vscode/mcp.json` (Microsoft Learn docs + Azure). Copilot uses them to verify SDK/API versions
   and regional availability instead of guessing.

## Original build sequence
The reference implementation already includes the work below. These prompts are
the original build/check sequence, not a backlog of unimplemented features or
authority to reprovision the accepted dev deployment. Review current source,
the deployment plan and later decisions before rerunning a prompt.
In Copilot Chat type `/` and pick the prompt (or open the file and press the run button):

| # | Prompt | Outcome |
|---|---|---|
| 00 | orient | local mode proven, gaps listed |
| 01 | provision-azure | `azd up` - full stack in an EU region |
| 02 | member-values-facade | read-only APIM facade with member scoping |
| 03 | approved-content-index | hybrid/semantic index, approved versions only |
| 04 | strategy-service-azure | Key Vault-verified snapshots, overlay, roles |
| 05 | foundry-counsellor | counsellor on Foundry Agent Service, Prompt Shields, tracing |
| 06 | orchestrator-durable | durable journey state, Service Bus, escalation on every failure |
| 07 | explainability-store | WORM records, queue fallback, reconciliation |
| 08 | p2-azureml-pipeline | bounded learning, Fairlearn audit, veto, tiers, uplift, kill switch |
| 09 | fairness-agent-and-cohort-zone | L4 protective agent, segregated cohort zone, dashboard |
| 10 | design-depth-agents | contracts + "May NEVER" tests for the other three agents |
| 11 | voice-live | voice demo with persona voices |
| 12 | evaluations | Foundry evaluations, persona harness, model matrix A vs B |
| 13 | observability-and-cost | traces, AI gateway, workbook, cost per resolved query |
| 14 | demo-readiness | reset script, runbook, fallbacks |

These are build steps for one release, not delivery phases.

## Keeping documentation consistent

- Keep the [README](../README.md) as the solution overview, offline entry point,
  scope/limitations summary and navigation map for a public audience. Keep internal
  model comparisons, experiment results, environment-specific endpoints, operational
  status and implementation-decision references out of it. Put pins, job IDs,
  benchmarks and deployment procedures in the existing specialist documents.
- When a change affects architecture, setup, supported behavior or acceptance,
  update the README summary if needed and the relevant child document together.
  Update [boundary traceability](boundaries-traceability.md) for enforcement changes.
- Record decisions and evidence in a local, unpublished decision log (`docs/local/decision-log.md`, ignored by Git);
  published documents may cite its `IMP-nn` entry IDs but never link to it.
  Label superseded release notes as historical; do not leave completed checks
  described as pending or present an old failed release as the current one.
- `.azure/` is Git-ignored environment state. Keep shared evidence and README
  links in tracked documents; a local deployment plan alone is not available
  to someone cloning the repository.
- Distinguish source implementation, recorded deployed acceptance and fresh live
  verification. Tests, healthy revisions and successful job exits do not prove
  semantic quality, physical audio performance or production readiness.
- Preserve failed measurements and their date/version/sample scope. A later
  accepted release does not retroactively repair an older evaluation result.
- Check relative links/anchors and documented commands against source and CI.
  Never mutate live governance state merely to validate documentation.

## Working rhythm
- After each prompt: review the diff, run `@guardrail-reviewer` (custom agent) on the changes, run the tests, commit.
- If Copilot proposes weakening a test or a boundary to make something pass, reject it - the test is the specification.
- Bring errors, design questions or results back to your Cowork project; the decision log, white paper and deck are kept in step there.
