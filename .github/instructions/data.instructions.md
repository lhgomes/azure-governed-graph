---
applyTo: "examples/**/data/**"
---
- Synthetic only, watermarked `ILLUSTRATIVE - synthetic data`.
- `synthetic/*` in a domain pack is runtime-side: NO demographic columns. Cohort attributes live only in `fairness/*` (segregated cohort zone).
- Catalogue, templates, priors and thresholds are board-approved artefacts: changing them is a Tier 2 material change (B8) -
  bump the version, add the approval reference, and note it in the local decision log (`docs/local/decision-log.md`).
- Templates carry placeholders (`{{value:<id>}}`, `{{asof:<id>}}`), never figures.
