# Azure-only execution

Azure Public is the only supported application and test execution environment.
Developer workstations and generic GitHub-hosted runners are not application
runtime or test fallbacks.

## Runtime contract

- Required Azure endpoints and identities must be configured. Missing configuration
  fails closed; files, in-process APIs, offline agents and local signing are not
  runtime substitutes.
- P1 uses hosted journey state, APIM-backed values, approved Search, Service Bus,
  Blob audit/outbox, Foundry and governed voice.
- P2 uses separately authorized identities, immutable aggregates, isolated fairness
  audit, signed snapshots and guarded native schedules.
- Pure domain logic and injected test doubles remain separate from deployed
  adapters. Retained legacy evaluation adapters do not establish a supported runtime.
- Synthetic sample adapters run in Azure behind production-facing contracts.
  They do not authorize real member use.
- B1-B10, identity isolation and existing durable state contracts remain controlling.

## Verification and evidence

1. Source-control actions require no deployment identifiers, GitHub environment
   variables or subscription access. The repository has no deployment-dependent
   mandatory GitHub test workflow.
2. Regression source preserves B1-B10 coverage. Source presence is not test execution.
3. Integration tests, synthetic channel probes and Foundry/voice evaluations require
   a separately authorized Azure deployment and its scoped identity model.
4. Infrastructure validation belongs to the explicitly authorized delivery process.
5. Missing or unrun tests and evaluations are not passes. Readiness does not establish
   conversational quality, voice performance or production acceptance.

See [deployment](deploying.md), [evaluation](evaluation.md) and the
[sample runbook](../examples/pension_wtp/docs/demo-runbook.md) for their separate
procedures and limitations.
