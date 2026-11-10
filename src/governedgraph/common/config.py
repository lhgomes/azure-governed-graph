"""Azure Public runtime configuration for governed deployments."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if not os.getenv("GOVERNEDGRAPH_DATA_DIR"):
    raise ValueError("Runtime requires explicit GOVERNEDGRAPH_DATA_DIR from the domain composition")
DATA_DIR = Path(os.environ["GOVERNEDGRAPH_DATA_DIR"])

@dataclass(frozen=True)
class Settings:
    mode: str = "azure"  # Compatibility for deployment metadata; no runtime mode selection.
    environment_name: str = os.getenv("AZURE_ENV_NAME", "")
    project_endpoint: str = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    project_resource_id: str = os.getenv("AZURE_AI_PROJECT_RESOURCE_ID", "")
    ai_gateway_url: str = os.getenv("AZURE_AI_GATEWAY_URL", "")
    model_deployment: str = os.getenv("AZURE_AI_MODEL_DEPLOYMENT", "gpt-counsellor")
    agent_name: str = os.getenv("AZURE_AI_AGENT_NAME", "gg-counsellor")
    agent_version: str = os.getenv("AZURE_AI_AGENT_VERSION", "")
    intent_agent_version: str = os.getenv("AZURE_AI_INTENT_AGENT_VERSION", "")
    understanding_agent_version: str = os.getenv("AZURE_AI_UNDERSTANDING_AGENT_VERSION", "")
    review_agent_version: str = os.getenv("AZURE_AI_REVIEW_AGENT_VERSION", "")
    fairness_agent_name: str = os.getenv("AZURE_AI_FAIRNESS_AGENT_NAME", "gg-fairness-explainability")
    fairness_agent_version: str = os.getenv("AZURE_AI_FAIRNESS_AGENT_VERSION", "")
    fairness_identity_client_id: str = os.getenv("FAIRNESS_MANAGED_IDENTITY_CLIENT_ID", "")
    compact_deployment: str = os.getenv("AZURE_AI_COMPACT_DEPLOYMENT", "gpt-compact")
    content_safety_endpoint: str = os.getenv("CONTENT_SAFETY_ENDPOINT", "")
    search_endpoint: str = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    search_index: str = os.getenv("AZURE_SEARCH_INDEX", "approved-content")
    content_storage_url: str = os.getenv("CONTENT_STORAGE_URL", "")
    content_storage_resource_id: str = os.getenv("CONTENT_STORAGE_RESOURCE_ID", "")
    content_container: str = os.getenv("CONTENT_CONTAINER", "approved-content")
    embedding_endpoint: str = os.getenv("AZURE_OPENAI_EMBEDDING_ENDPOINT", "")
    embedding_deployment: str = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "embedding")
    embedding_model: str = os.getenv("AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    member_values_url: str = os.getenv("MEMBER_VALUES_API_URL", "")  # APIM facade URL in azure mode
    member_token_issuer: str = os.getenv("MEMBER_TOKEN_ISSUER", "")
    member_token_audience: str = os.getenv("MEMBER_TOKEN_AUDIENCE", "gg-apim")
    member_token_public_jwk: str = os.getenv("MEMBER_TOKEN_PUBLIC_JWK", "")
    strategy_service_url: str = os.getenv("STRATEGY_SERVICE_URL", "")
    strategy_admin_url: str = os.getenv("STRATEGY_ADMIN_URL", "")
    ranking_storage_url: str = os.getenv("RANKING_STORAGE_URL", "")
    strategy_tenant_id: str = os.getenv("STRATEGY_TENANT_ID", "")
    strategy_api_client_id: str = os.getenv("STRATEGY_API_CLIENT_ID", "")
    strategy_publisher_id: str = os.getenv("STRATEGY_PUBLISHER_PRINCIPAL_ID", "")
    strategy_fairness_id: str = os.getenv("STRATEGY_FAIRNESS_PRINCIPAL_ID", "")
    strategy_runtime_id: str = os.getenv("STRATEGY_RUNTIME_PRINCIPAL_ID", "")
    strategy_admin_id: str = os.getenv("STRATEGY_ADMIN_PRINCIPAL_ID", "")
    strategy_governance_group: str = os.getenv("STRATEGY_GOVERNANCE_GROUP_ID", "")
    strategy_recovery_operator_id: str = os.getenv("STRATEGY_RECOVERY_OPERATOR_ID", "")
    p2_fairness_job_id: str = os.getenv("P2_FAIRNESS_JOB_ID", "")
    p2_image: str = os.getenv("GOVERNEDGRAPH_P2_IMAGE", "")
    appinsights_connection_string: str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    explainability_account_url: str = os.getenv("EXPLAINABILITY_STORAGE_URL", "")
    explainability_container: str = os.getenv("EXPLAINABILITY_CONTAINER", "explainability-records")
    journey_storage_url: str = os.getenv("JOURNEY_STORAGE_URL", "")
    service_bus_namespace: str = os.getenv("SERVICE_BUS_NAMESPACE", "")
    contact_centre_url: str = os.getenv("CONTACT_CENTRE_URL", "")
    contact_centre_client_id: str = os.getenv("CONTACT_CENTRE_CLIENT_ID", "")
    contact_centre_audience: str = os.getenv("CONTACT_CENTRE_AUDIENCE", "")
    contact_centre_storage_url: str = os.getenv("CONTACT_CENTRE_STORAGE_URL", "")
    key_vault_url: str = os.getenv("AZURE_KEY_VAULT_URL", "")
    snapshot_signing_key_name: str = os.getenv("SNAPSHOT_SIGNING_KEY_NAME", "ranking-snapshot-signing")
    control_arm_share: float = float(os.getenv("GOVERNEDGRAPH_CONTROL_ARM_SHARE", "0.05"))  # ILLUSTRATIVE
    run_dir: Path = Path(os.getenv("GOVERNEDGRAPH_RUN_DIR", str(REPO_ROOT / ".run")))

    @property
    def is_azure(self) -> bool:
        return True


settings = Settings()
