#!/usr/bin/env bash
# GitHub-hosted control plane ONLY: send source to Azure ACR Tasks; start/wait for
# an isolated Azure Container Apps Job. Never run pytest/Bicep/application code here.
set -euo pipefail
: "${GG_CI_ACR_NAME:?}"
: "${GG_CI_RESOURCE_GROUP:?}"
: "${GG_CI_JOB_NAME:?}"
: "${GG_CI_PROFILE:?}"
: "${AZURE_SUBSCRIPTION_ID:?}"
[[ "$GG_CI_PROFILE" == p1 || "$GG_CI_PROFILE" == p2 ]] || exit 64
[[ "$GG_CI_ACR_NAME" =~ ^[a-z0-9]{5,50}$ ]] || exit 64
[[ "$GG_CI_RESOURCE_GROUP" =~ ^[a-zA-Z0-9._()-]{1,90}$ ]] || exit 64
[[ "$GG_CI_JOB_NAME" == job-gg-ci-validation ]] || exit 64

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
# The CI registry is dedicated; no production images or secrets are reachable.
REGISTRY_SERVER=$(az acr show -n "$GG_CI_ACR_NAME" --query loginServer -o tsv)
[[ "$REGISTRY_SERVER" == "$GG_CI_ACR_NAME.azurecr.io" ]] || { echo "Unexpected ACR endpoint" >&2; exit 1; }
TAG="${GITHUB_SHA:?}-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}-${GG_CI_PROFILE}"
SOURCE_IMAGE="ci/validation:$TAG"
echo "Building $SOURCE_IMAGE using Azure Container Registry Tasks"
az acr build \
  --registry "$GG_CI_ACR_NAME" \
  --file infra/ci/Dockerfile \
  --target "$GG_CI_PROFILE" \
  --image "$SOURCE_IMAGE" \
  --build-arg "SOURCE_SHA=$GITHUB_SHA" \
  .

# Pin by manifest digest; no mutable latest or guessed image reference.
DIGEST=$(az acr manifest show-metadata --registry "$GG_CI_ACR_NAME" \
  --name "$SOURCE_IMAGE" --query digest -o tsv)
[[ "$DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo "No verified ACR image digest" >&2; exit 1; }
IMAGE="$REGISTRY_SERVER/ci/validation@$DIGEST"
echo "Starting Azure CI job with immutable image digest: $IMAGE"

# Unlike updating the job template, per-execution overrides cannot race between
# the P1 and P2 matrix jobs. Its identity has only AcrPull on a CI-only registry.
EXECUTION=$(az containerapp job start \
  --resource-group "$GG_CI_RESOURCE_GROUP" --name "$GG_CI_JOB_NAME" \
  --image "$IMAGE" --command /bin/sh --args /app/infra/ci/run.sh \
  --query name -o tsv)
[[ "$EXECUTION" =~ ^job-gg-ci-validation-[a-z0-9]+$ ]] || {
  echo "No valid Azure Container Apps Job execution ID returned" >&2
  exit 1
}
echo "Azure CI execution: $EXECUTION"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf '### Azure-hosted %s validation\n\nExecution: `%s`\n\nImage: `%s`\n' \
    "$GG_CI_PROFILE" "$EXECUTION" "$IMAGE" >> "$GITHUB_STEP_SUMMARY"
fi

# Fail closed on a missing execution, terminal error or timeout. 'Queued' is
# never acceptance evidence. No GitHub-hosted fallback is permitted.
EMPTY=0
for ((attempt=1; attempt<=180; attempt++)); do
  STATUS=$(az containerapp job execution list -g "$GG_CI_RESOURCE_GROUP" \
    -n "$GG_CI_JOB_NAME" --query "[?name=='$EXECUTION'].properties.status | [0]" -o tsv)
  case "$STATUS" in
    Succeeded)
      echo "Azure CI passed: $EXECUTION"
      exit 0
      ;;
    Failed|Stopped|Canceled|Cancelled)
      echo "::error::Azure CI execution $EXECUTION ended with $STATUS"
      exit 1
      ;;
    ""|null|None)
      EMPTY=$((EMPTY + 1))
      if ((EMPTY > 20)); then
        echo "::error::Azure CI execution did not appear in the execution listing"
        exit 1
      fi
      ;;
    Running|Pending|Processing|Queued|InProgress)
      EMPTY=0
      ;;
    *)
      echo "Azure CI execution status: $STATUS (not a pass)"
      ;;
  esac
  sleep 20
done
echo "::error::Azure CI execution timed out without a Succeeded status"
exit 1
