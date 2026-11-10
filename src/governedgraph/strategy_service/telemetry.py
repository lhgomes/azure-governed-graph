"""Managed-identity Application Insights export for strategy audit events."""
import logging

from governedgraph.common.config import settings


def configure_audit() -> None:
    if not settings.appinsights_connection_string:
        raise ValueError("Application Insights routing metadata is required in Azure mode")
    from governedgraph.agents.foundry_tracing import configure_tracing
    from governedgraph.observability import configure_events

    configure_tracing()
    configure_events()
    logging.getLogger("governedgraph.strategy_service").setLevel(logging.INFO)
