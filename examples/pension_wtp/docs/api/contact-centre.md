# Synthetic contact-centre handover and durable operations

**ILLUSTRATIVE - synthetic data. No live callbacks.**
Production uses Dynamics 365 Contact Center or the fund's existing platform
behind `ContactCentreClient` / `ContactCentre` (IMP-15).

## Internal API

`POST /handovers` requires an Entra app-only bearer token: configured v2 audience/
tenant, `ContactCentre.Access`, and the exact runtime principal. Missing/expired/
wrong-audience tokens receive 401; other principals/delegated callers receive
403. The health probe is unauthenticated but exposes no handover data.
There is no public contact-centre ingress or direct member access.

Required header: `Idempotency-Key: <journey-id>:escalation:<sequence>`.
Closed request contract: `EscalationRequest`, including journey ID, controlled
reason, fixed governed context summary, value IDs (not figures), callback
preference, creation time and escalation ID. Unknown fields, raw summaries and
figures are rejected. Same key/content returns the original handover ID;
conflicting content is 400; concurrent writes are 409/retry; storage failure is
503. The side effect and stored synthetic handover commit together.

`GET /advisor-queue?journey_id=<id>` is read-only and runtime-authorized.
It returns the stored references/status and synthetic-data label. It cannot
modify status, create callbacks or ingest member text.

## Channel-facing view

Through APIM: `GET /journey/journeys/<id>/advisor-queue`, with the **same channel
member token on every request**. The orchestrator verifies the current token and
checks durable journey ownership before calling the internal API. Cross-member
requests are 403. `Accept: application/json` returns references/status;
`Accept: text/html` returns the escaped, read-only advisor table. The local demo
also writes `.run/advisor_queue.html` without requiring a token or live service.

Do not issue tokens in runtime, write tokens to disk or use the legacy Azure
demo's token-file branch. Existing live-channel proof restrictions remain.

## Delivery, inboxes and recovery

- Journey events: MessageId=`{journey_id}:{event_id}`, SessionId=journey ID.
- Escalations: MessageId/Idempotency-Key=`{journey_id}:escalation:{sequence}`.
- Both existing queues retain duplicate detection **false**. Delivery is
  **at least once**, not exactly once. Domain IDs survive retries.
- State/outbox commit before send. Each acknowledged send removes one outbox
  entry durably. Restart recovery retries pending entries every five seconds.
- `journey-inbox` records each consumer's processed IDs durably. Event effects
  and receipts commit atomically; handover uses the idempotent external effect,
  so crash-after-effect/before-receipt is also safe.
- Complete broker messages only after durable consumer commit; renew peek/session
  locks up to five minutes. Lock expiry causes redelivery, never a second effect.
- **No automatic inbox/idempotency eviction:** the current TTL and manual DLQ
  redrive are unbounded in practice. IMP-14 records this window. A future finite
  replay/retention policy needs governance review before receipts can expire.

Turn requests should include a stable `Idempotency-Key` (letters/digits/hyphen/
underscore, maximum 100 characters). Retrying a completed turn returns
`duplicate: true` and the current state, not the original answer: figure-bearing
answers are not persisted. 409 means concurrent journey activity; 503 indicates
delivery pending or unavailable infrastructure and is not a confirmed callback.

## Human operations

Every escalation dead letter triggers a Sev 1 Azure Monitor rule, independently
of the consumer. Email routes to the dev `onCallEmail` action group using common
alert schema. Inspect the dead letter, resolve the failure and redrive with the
**original** body, SessionId and MessageId. Never purge, auto-complete or assign
a fresh escalation ID to hide a delivery failure.

Other proposed alerts: journey-event DLQ, oldest escalation >300 seconds,
explainability write / queue fallback / receiver failure, at least three
unavailable Prompt Shields calls in five minutes (parameterized), and frozen/
non-control fallback Strategy Service. Logs contain only operational metadata.

Before approved deployment, run the no-write Entra plan:

```bash
python scripts/configure_contact_centre.py --runtime-principal <runtime-object-id>
```

After preview approval only: add `--apply`, persist the returned nonsecret
`CONTACT_CENTRE_CLIENT_ID` and `CONTACT_CENTRE_AUDIENCE` in azd, provision and
deploy the reviewed four-service stack. The user-approved dev opt-in is
`GOVERNEDGRAPH_SYNTHETIC_CONTACT_CENTRE=true`; outside dev select the production adapter.
Do not deploy with placeholder/empty Entra configuration.

After deployment: run an advice question, redeliver its same escalation and show
exactly one advisor-queue row. Send one action-group test notification and ask
the user to confirm receipt; record execution/test IDs and receipt in IMP-15.
Those live checks are **pending**, not implied by offline tests or preview.
