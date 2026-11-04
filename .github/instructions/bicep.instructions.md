---
applyTo: "infra/**"
---
- EU region only (default swedencentral). Data-zone model deployments. Confirm availability with the Azure MCP server before changing models.
- `disableLocalAuth: true` / `allowSharedKeyAccess: false` wherever supported; role assignments in `modules/rbac.bicep` per identity.
- Three identities with different rights: runtime (verify snapshots, read values/content, write records), P2 pipeline (only signer),
  fairness agent (cohort-zone read, suspend). Do not merge them.
- The explainability container keeps its immutability policy; retention length is a DPO decision - do not shorten silently.
- Validate with `az bicep build` and `azd provision --preview` before `azd up`.
