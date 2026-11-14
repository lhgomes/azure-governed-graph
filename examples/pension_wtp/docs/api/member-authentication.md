# Synthetic member authentication and values facade

**ILLUSTRATIVE - synthetic data.** The sample does not use a real member identity
provider or member data. Production must supply its own verified identity
integration behind the same authorization contract.

The read-only [OpenAPI contract](member-values.openapi.yaml) describes official
values and administrator display strings. No AI-side calculation or formatting
is permitted.

## Channel contract

The channel supplies a signed RS256 bearer JWT. Configured issuer and audience
must match exactly. Required claims are `iss`, `aud`, `member_ref`, `iat` and
`exp`; lifetime must be positive and at most fifteen minutes. Personal-value
reads additionally require `acr=stepup`.

APIM validates the token and overwrites `X-Authenticated-Member` from the verified
claim. Missing, invalid or expired tokens return 401. A different path member
returns 403 with a correlated scope-violation incident. App-only tokens are not
member proof.

`POST /journey/journeys` accepts only `channel` and `accessibility_mode`; the
orchestrator derives member identity from the JWT. Every turn revalidates
ownership and forwards the original current-request token. Without step-up,
only approved figure-free general passages are available; no personal-value
call is made.

The orchestrator verifies against the configured public JWK and holds no
member-signing key. Production replaces `MEMBER_TOKEN_ISSUER` and
`MEMBER_TOKEN_PUBLIC_JWK` with its identity provider's configuration. The reference
uses a pinned public key, so rotation requires coordinated configuration.
Step-up must be asserted only after actual verification.

## Isolated synthetic issuer

The [issuer](../../../../src/governedgraph/dev/issue_member_token.py) is enabled
only by explicit development configuration. Its dedicated identity signs only
the member-token key. P2 signs only ranking snapshots; runtime and APIM validate
member tokens using public-key configuration without a member-key grant.

The [private issuer job](../../../../infra/modules/dev-member-issuer.bicep)
has no public endpoint and is disabled outside the synthetic development
environment. Disabling a deployment flag does not delete an existing resource
or revoke assignments; any cleanup needs an independently reviewed scope.

Synthetic channel proofs run only inside the private network under the existing
issuer identity:

```bash
python -m scripts.run_dev_proof channel --logs
```

The probe uses the deployment's existing HTTPS endpoints, forwards Authorization
on every request and does not follow redirects. Synthetic tokens expire within
fifteen minutes and stay in memory, including negative controls. Output is pass/fail
and IDs only, never tokens, utterances or figures.

No local issuance, token files, developer Key Vault access, extra identity or
new network path is permitted. Confirm that the developer identity has no Key Vault
assignment. Obtain public JWK bootstrap output from the authorized issuer job,
not by widening developer permissions.

Use existing operator/runtime access to inspect audit and trace metadata by
the emitted journey IDs. The issuer receives no additional diagnostic read grants.
Control-arm assignment and protective freezes must not be changed to make a proof pass.

## Key-isolation verification

Verify each role on its own private compute with its approved immutable image:

- The issuer can sign fixed synthetic diagnostic bytes with the member key,
  but is denied snapshot-key signing.
- P2 can sign the diagnostic with the snapshot key, but is denied member-key signing.
- Verifiers have only public-key read/verify rights on the snapshot key.
- ML platform dependencies are dedicated and separate from signing keys.

The diagnostic discards signatures and creates no member token or usable ranking.
The prohibited key must return HTTP 403 with `ForbiddenByRbac` and the expected
caller identity. Firewall failures, outages, wrong identities and unexpected
signing success are failures, not isolation proof. A no-write plan is not an
executed check. Do not swap identities or use a multi-identity helper.

## Correlation and availability

`X-Journey-ID` and W3C `traceparent` carry correlation, not member attributes.
APIM captures no request/response bodies, bearer token or authenticated-member
header. Application Insights uses managed identity and disables local authentication.

Retries apply only to GET; the backend circuit breaker remains active.
Unavailable values trigger human escalation, never estimation or another member's
data. For proof, reconciliation and operational procedures see the
[runbook](../demo-runbook.md).
