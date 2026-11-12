#!/bin/sh
# Executed solely by the Azure Container Apps manual CI job.
set -eu
cd /app
if [ "${AZURE_ENV_NAME:-}" != "gg-dev" ] || [ "${GOVERNEDGRAPH_CI_AZURE_JOB:-}" != "true" ]; then
  echo "CI runner is restricted to the approved gg-dev Azure Container Apps Job" >&2
  exit 64
fi
echo "GG_CI_START profile=${GG_CI_PROFILE:-missing} source=${SOURCE_SHA:-image-label}"
python -m pip check
case "${GG_CI_PROFILE:-}" in
  p1)
    # Explicit P2 test partition below. Everything else, including B1-B10 and the
    # extension sample tests, must be discovered and executed. No stale eval JSON.
    python -m pytest -q \
      --ignore=tests/test_container_p2.py \
      --ignore=tests/test_key_isolation.py \
      --ignore=tests/test_ml_isolation.py \
      --ignore=tests/test_p2_components.py \
      --ignore=tests/test_p2_fairness_agent_proof.py \
      --ignore=tests/test_p2_operator_polling.py \
      --ignore=tests/test_scheduled_p2.py \
      --ignore=tests/test_learning_fixtures.py \
      --ignore=tests/test_fairness_foundry.py
    bicep build infra/main.bicep --stdout >/dev/null
    bicep build infra/workloads.bicep --stdout >/dev/null
    bicep build infra/model-evaluation/main.bicep --stdout >/dev/null
    bicep build infra/ci/main.bicep --stdout >/dev/null
    ;;
  p2)
    # Separate requirements-p2.txt (Azure ML's pinned Blob ceiling) from P1.
    python -m pytest -q \
      tests/test_container_p2.py \
      tests/test_key_isolation.py \
      tests/test_ml_isolation.py \
      tests/test_p2_components.py \
      tests/test_p2_fairness_agent_proof.py \
      tests/test_p2_operator_polling.py \
      tests/test_scheduled_p2.py \
      tests/test_learning_fixtures.py \
      tests/test_fairness_foundry.py
    ;;
  *)
    echo "Invalid closed CI profile; refusing to report success" >&2
    exit 64
    ;;
esac
echo "GG_CI_PASS profile=${GG_CI_PROFILE}"
