"""Publish the exact owner-approved catalogue using the existing isolated P2 signer."""
import logging

from governedgraph.strategy_service.catalogue_release import sign_release

log = logging.getLogger("governedgraph.learning")


def publish_catalogue(service, signer, lease_check) -> None:
    import httpx

    state = service.refresh()
    pending = state.get("catalogue_pending")
    if pending is None:
        return
    if state["frozen"]:
        log.warning("catalogue_publication_waiting_for_recovery", extra={"decision_ref": pending["decision_ref"]})
        return
    lease_check()
    if pending["base_version"] != service.catalogue.version:
        raise ValueError("owner catalogue decision references a superseded baseline")
    release, baseline = sign_release(pending["decision_ref"], pending["bundle"], signer,
                                     service.thresholds["threshold_version"])
    lease_check()
    try:
        service.activate_catalogue(release, baseline)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 409:
            raise
        log.warning("catalogue_publication_conflict", extra={"decision_ref": pending["decision_ref"]})
    else:
        log.info("catalogue_published", extra={"decision_ref": pending["decision_ref"]})
