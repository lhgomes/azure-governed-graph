# Validation status and evidence limitations

**Review date:** 15 November 2026. **Source baseline inspected:** `main` at `3d93dabc2cc1be735877453e214f9f6a5132eab7`. This is a point-in-time public-readiness inventory, not a live dashboard. Recheck this section after future commits and deployments.

| Evidence type | What is known from the repository | What must not be inferred |
|---|---|---|
| B1–B10 regression | Regression test source and enforcement mappings are retained. The former deployment-dependent GitHub workflow was removed in PR #4. This publication review did not run regression tests. | Test source or previous CI status does not establish a passing latest-commit run. |
| Azure integration and security | The sample includes Bicep, delivery scripts, APIM, identities, synthetic probes and operational checks. No current live Azure deployment/probe evidence was established by this review. | Code presence, `--plan`, or readiness is not end-to-end or security acceptance. |
| Model comparison | The checked-in [30 October 2026 measurement](../evals/results/model-comparison-measurements-2026-10-30.json) is explicitly **SIMULATED / ILLUSTRATIVE** and reports `evaluation_passed: false` due to a failed deterministic advice-refusal gate. | These results are not a current passing production benchmark or evidence that B10 superiority is achieved. |
| Human evaluation | The [27 October review file](../evals/results/human-review-2026-10-27.json) is labelled **UNRATED**. | Human calibration or real human quality approval is not established by an unrated rubric. |
| Sample release record | [Pension release-approval.json](../examples/pension_wtp/release-approval.json) contains `approved: true` and a link to the [illustrative approval context](evaluation.md#welcoming-letter-help-release). | A checked-in declaration or cross-reference does not independently prove the underlying human decision, immutable live pins, release evidence or Microsoft publication authorization. |
| Real-world effectiveness and cost | The docs define evaluation, voice targets, bounded learning and comparison controls. | No general FCR uplift, fairness certification, price, voice SLO or production service-level promise is established. |

## Evidence required for a real deployment claim

Record the deployed source commit, composition, subscription/region **privately**, model/agent pinned versions, data fixture digests, human approvals, B1–B10 regression outcomes, infrastructure checks, authorized synthetic journey probes, content and member authorization tests, fairness audit results, signed rollback checks and voice/Foundry evaluations where applicable. Record failures and missing checks as failures/unavailable, not passes. Never publish raw member data, confidential logs or private identifiers with evidence.

Use the procedures in [deployment](deploying.md), [evaluation](evaluation.md), [the sample runbook](../examples/pension_wtp/docs/demo-runbook.md) and [Azure-only execution](azure-only-execution.md). A successful deployment command is **readiness**, not acceptance or authorization for live members.

This documentation can be updated with fresh, independently verified evidence; until then the project remains a synthetic reference with unverified latest-commit acceptance.
