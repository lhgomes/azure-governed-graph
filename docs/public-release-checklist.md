# Public release checklist

This checklist is independent of runtime acceptance. Complete it before publishing changes or communicating production capabilities.

- [ ] Maintainer has verified ownership, copyright authorization, license/NOTICE obligations and synthetic sample provenance.
- [ ] No tenant IDs, actual member data, credentials, internal endpoints, customer transcripts, private approvals, secret-bearing logs or private evaluation receipts are committed.
- [ ] Retained artifacts and Git history have been reviewed separately. Deleting a tracked artifact in a pull request **does not remove it from earlier commits or cloned copies**.
- [ ] Reference documentation links and overview image work, and examples consistently state their synthetic scope.
- [ ] Resource inventory matches deployed Bicep and service identities; unused deployments, Service Bus queues or hosted agents are not reintroduced.
- [ ] Security and identity boundaries, B1–B10, response gates, content-approval journal, signed catalogue publication and fairness veto are explicitly reviewed.
- [ ] Azure-hosted tests/evaluations, if run, include exact code/image/model/agent pins and retained evidence; missing or unavailable tests are declared.
- [ ] Nightly gap review and owner decisions are described as proposals, **not autonomous self-modification**, and their question-retention/privacy obligations are covered.
- [ ] Costs, performance and effectiveness claims use fresh measured data and state their denominators, coverage and limitations.
- [ ] Owner-approved changes and production rollout have their own separate authorization and recovery plans.

See [validation status](validation-status.md), [evaluation](evaluation.md), and [security reporting](../SECURITY.md).
