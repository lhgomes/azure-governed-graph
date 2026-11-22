# Strategy catalogue: versions, coverage and source upgrades

Part of the [architecture](architecture.md#strategy-catalogue-and-governed-publication). This page
describes how catalogue versions, expert priors and source-pack upgrades behave. The upgrade is a
separate operation from normal deployment.

The source catalogue's S1-S5 version 1.1 adds `second_contact` eligibility without
changing first-contact eligibility or template versions. Version 1.0 definitions
remain available for historical evidence and verified predecessor selection
during rolling delivery, not selection after upgrade. Separate
second-contact expert priors emphasize focused teach-back and clarification;
they never borrow first-contact outcomes. S6 remains unchanged and always
available. The catalogue, expert baseline and revised counsellor instructions
are a material change requiring reviewed Tier 2 delivery and newly verified
signed snapshots before live activation (B3, B5, B8, B10). Updating source alone
does not activate them or certify conversation quality.

Sample catalogue and priors explicitly cover every catalogue-eligible combination
of explanation intent, journey stage, channel and accessibility mode. New
contexts use the approved default order, except second-contact contexts use the
focused second-contact order; eligibility filters apply before signing. The
signed snapshots, not an unsigned runtime default, authorize treatment. The same
complete priors seed P2 and the control arm, and regression coverage checks all
supported contexts. An upgrade that adds coverage archives prior state and resets
live ordering to the approved baseline; protective suspensions and pauses remain
effective.

Source-pack upgrades use an explicit reviewed predecessor pack and exact target
hashes in [source_upgrade_v1.json](../examples/pension_wtp/data/catalogue/source_upgrade_v1.json).
They are a separate existing-target operation through
[`scripts.upgrade_catalogue`](../scripts/upgrade_catalogue.py), not part of normal
deployment. Normal deployment initializes expert priors before workloads and
does not load or sign upgrade metadata. Empty state and snapshots matching the
running catalogue do not depend on old upgrade files, even for future fresh
catalogue versions. The exceptional upgrade alone rolls out compatible workloads
before state activation. The existing P2 signing
identity signs the human-approved plan and target expert baseline; the separate
Strategy Service initializer verifies both and archives the entire old signed
state before an ETag-conditional manifest update. New versions use their approved
expert priors, never old-version learned ordering. Suspensions and owner pauses
survive unchanged. Frozen state, pending or widening owner decisions and
owner-managed catalogues block this source-pack upgrade. Immutable history is
retained; no permission, schema-field, unfreeze or reinstatement bypass is added.
See [explicit upgrades](deploying.md#explicit-source-catalogue-upgrade).
