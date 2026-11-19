# Security policy

GovernedGraph is a **synthetic Azure reference implementation**, not a supported production service. No version has a published security-support SLA or production-security certification. Security controls described in the architecture are design and implementation contracts, not independent proof that a particular deployment is secure.

## Reporting a vulnerability privately

Do **not** open a public issue, discussion or pull request containing an exploit, credential, member data or nonpublic infrastructure details.

1. If GitHub **private vulnerability reporting** is enabled, use the repository's **Security → Advisories → Report a vulnerability** flow.
2. If that option is unavailable, contact the repository maintainer privately through an appropriate contact method from their GitHub profile. If you cannot find one, create a public issue **requesting a private reporting channel only**; do not disclose the vulnerability in that issue.

Include a concise description, affected commit/component, reproducible conditions, impact and suggested mitigation **only through the private channel**. Do not send live credentials, tokens, production data or secret-bearing logs. No response-time or disclosure deadline is promised by this reference project.

## Security boundaries for contributors

- Do not add shared keys, passwords, tokens, real tenant or subscription identifiers, client secrets, private keys or populated deployment receipts to source control.
- Never bypass member authorization, managed-identity separation, signed snapshots, deterministic value binding, fairness veto, escalation, or the B1–B10 guardrails to make a test green.
- Keep the synthetic member-token issuer, mock values adapter and demo presenter isolated from actual member workloads. This sample is not a production identity provider.
- Do not publish private approval decisions, person-level cohorts, conversations, internal traces, or documents generated in customer environments.