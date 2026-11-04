# Synthetic member authentication and values facade

ILLUSTRATIVE - synthetic data. No real member identity provider or data is used.

The read-only [OpenAPI 3.1 contract](member-values.openapi.yaml) is JSON-formatted
YAML 1.2. The values envelope contains the deterministic administrator's official
values and display strings. No AI-side calculation or formatting is permitted.

## Channel contract

The channel supplies a signed RS256 bearer JWT. The configured issuer and
audience (`gg-apim` by default) must match exactly. Required claims are `iss`,
`aud`, `member_ref`, `iat`, and `exp`; lifetime must be positive and at most
fifteen minutes. The values facade additionally requires `acr=stepup`.
Missing/invalid/expired claims return 401. APIM sets `X-Authenticated-Member`
from the validated claim and overwrites any caller copy. A different path member
returns 403 with a correlated scope-violation incident.

`POST /journey/journeys` accepts only `channel` and `accessibility_mode`.
It no longer accepts `member_ref`; the orchestrator derives it from the verified
JWT. Every journey turn revalidates ownership and refreshes the forwarded token.
Without step-up, only figure-free approved general passages are returned, with
an invitation to complete step-up or contact an advisor. No personal values call
is made. App-only tokens are never member proof.

The orchestrator verifies signatures with the configured public JWK and keeps
no signing key. Production replaces `MEMBER_TOKEN_ISSUER` and
`MEMBER_TOKEN_PUBLIC_JWK` with the fund identity provider's configuration;
this reference uses a pinned public key, so rotation requires a coordinated
configuration update. The fund must assert step-up only after actual verification.

## Dev-only issuer

The [issuer tool](../../../../src/governedgraph/dev/issue_member_token.py) refuses to run unless
`GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER=true`. A separately named `dev-member-token-signing`
RSA key stays in Key Vault. Under the deployed IMP-13 revision, a dedicated
dev-only issuer identity signs that key only; P2 signs snapshots only.
Runtime's snapshot verifier is not widened. The optional
[manual issuer job](../../../../infra/modules/dev-member-issuer.bicep) runs inside the
private network using only the dev issuer, never P2 or runtime. `deployDevIssuer`
defaults to false; the existing `GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER` azd variable
provides explicit dev opt-in. It has no public endpoint or automatic trigger.
Keep it disabled outside dev. Disabling an already-deployed resource in
incremental ARM does not remove it or its grants; review explicit cleanup.
The revised identity/key assignments were deployed on 2026-10-23. The existing
issuer job proved member-key signing and snapshot-key signing denial as its own
dedicated identity; no temporary developer grant or member token was needed.

Public-JWK inspection on an authorized development host uses
`AzureCliCredential` explicitly, never a default credential chain:

```bash
export GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER=true
export AZURE_KEY_VAULT_URL=https://your-dev-vault.vault.azure.net/
export MEMBER_TOKEN_ISSUER=https://synthetic.example/gg-dev
PYTHONPATH=src python -m governedgraph.dev.issue_member_token --public-jwk
```

**Live proofs run only inside the private network**, using `--probe-channel`
on the existing dev-issuer job and its existing identity. This supersedes the
local-channel/temporary developer-grant procedure. No developer Key Vault grant,
new identity, role, resource or network path is permitted. The CLI rejects local
probe execution and no longer has token-file or legacy facade-check modes.

The hosted CLI uses `ManagedIdentityCredential` explicitly for the dedicated
issuer client ID. Public-JWK export remains its default job command. The probe
accepts `--orchestrator-url` for the existing HTTPS orchestrator endpoint, sends
Authorization on every authenticated request, and never follows redirects.
Synthetic `MBR-7Q2K` tokens have step-up and at most 900 seconds validity;
expired and second-member tokens are memory-only negative controls. Tokens,
utterances and figures are never written or printed. Output is pass/fail and IDs
only; errors are sanitized and fail the execution. Control-arm journeys remain
static; the probe observes, never overrides, assignment before demonstrating S5.

Use existing operator/runtime access to verify actual Blob explainability,
numeric-fidelity/agent-version metadata and content-free traces by the emitted
journey IDs; the issuer receives no additional read grants. `DEV_ISSUER_IMAGE`
pins the independently updated job image for subsequent azd provisioning.
Do not enable this synthetic stand-in in production. The fixed-digest
`check_key_isolation` mode remains the no-token cross-key diagnostic.
If overriding a single execution
with Azure CLI, supply the complete execution template using `--yaml`: command,
arguments, image, resources, and all environment entries. Startup overrides do
not automatically retain the original container command or environment. Keep
`AZURE_CLIENT_ID` set to the dedicated dev-issuer identity, never P2 or runtime.

## Key separation acceptance after approved deployment

First verify the new assignments, then remove the four reviewed vault-scope
assignments with `governedgraph.dev.revoke_legacy_key_vault --apply` and their exact
`--assignment-id` arguments. Incremental Bicep alone will not remove them.
No vault-scope grant, including the ML/P2 metadata Reader, may remain.
The existing ML workspace must be smoke-tested after that Reader removal;
fresh workspace creation previously needed it, so this revision does not claim
fresh-workspace bootstrap works without a further approved design.

The issuer check runs in an issuer job execution.
P2's formerly deferred check is proven on its own private compute in an Azure ML job
and again in each resumed native cycle (IMP-40, maintainers' local decision log)
for the exact identity/image evidence. Any rerun of
`governedgraph.dev.check_key_isolation --identity-role pipeline --expected-principal-id
<P2-principal> --probe` must still run only on P2's own compute.
Do not switch the issuer job identity to perform that check.
The image must include the new diagnostic before executing it. Use separate
executions, not a multi-identity helper. Without `--probe`, the command only
prints the planned checks and does not contact Key Vault.
Each actual check signs a fixed synthetic diagnostic digest on its permitted
key, discards the signature, and requires the other key to fail with HTTP 403,
`ForbiddenByRbac` and the expected caller object ID. Firewall failures, outages,
wrong identities and unexpected signing success are failures, not proof.
No token or usable ranking snapshot is generated by this diagnostic.

## Correlation and availability

The caller forwards `X-Journey-ID` and W3C `traceparent`; neither carries member
attributes. APIM uses W3C correlation and 100% request telemetry. Scope incidents
are error traces with the journey ID. No request/response payload, bearer token,
or authenticated-member header is captured. Application Insights accepts telemetry
through APIM's managed identity; local authentication remains disabled.

Retries apply only to GET. The existing backend circuit breaker remains active.
When values are unavailable or the circuit is open, the counsellor escalates,
never estimates or falls back to another member's values.
