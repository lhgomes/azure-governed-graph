FROM python:3.14-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app/examples GOVERNEDGRAPH_MODE=azure
COPY requirements.txt requirements-azure.txt ./
RUN pip install --no-cache-dir -r requirements-azure.txt
COPY src ./src
# The sample (domain pack and values API) is chosen at build time: `--build-arg SAMPLE=<package under examples/>`.
# An empty or missing value means the bundled pension_wtp. Every stage inherits GOVERNEDGRAPH_SAMPLE, which also makes
# the framework's data directory, values provider and demo defaults resolve to the same package at run time.
ARG SAMPLE=pension_wtp
ENV GOVERNEDGRAPH_SAMPLE=${SAMPLE:-pension_wtp}
RUN case "$GOVERNEDGRAPH_SAMPLE" in ""|[0-9]*|*[!A-Za-z0-9_]*) \
      echo "SAMPLE must be the name of a Python package under examples/" >&2; exit 1;; esac
COPY examples/${GOVERNEDGRAPH_SAMPLE}/__init__.py ./examples/${GOVERNEDGRAPH_SAMPLE}/__init__.py
COPY examples/${GOVERNEDGRAPH_SAMPLE}/data/catalogue ./examples/${GOVERNEDGRAPH_SAMPLE}/data/catalogue
COPY examples/${GOVERNEDGRAPH_SAMPLE}/data/content ./examples/${GOVERNEDGRAPH_SAMPLE}/data/content
COPY examples/${GOVERNEDGRAPH_SAMPLE}/data/fairness/thresholds_v1.json ./examples/${GOVERNEDGRAPH_SAMPLE}/data/fairness/thresholds_v1.json
RUN useradd -r -u 10001 app && chown -R app /app
USER app

FROM base AS p2
USER root
COPY requirements-p2.txt ./
RUN pip install --no-cache-dir -r requirements-p2.txt && pip check
USER app

FROM base AS dev-fixtures
COPY --chown=10001:10001 examples/${GOVERNEDGRAPH_SAMPLE}/data/synthetic/outcome_aggregates_illustrative.csv ./examples/${GOVERNEDGRAPH_SAMPLE}/data/synthetic/outcome_aggregates_illustrative.csv
COPY --chown=10001:10001 examples/${GOVERNEDGRAPH_SAMPLE}/data/synthetic/uplift_window_illustrative.json ./examples/${GOVERNEDGRAPH_SAMPLE}/data/synthetic/uplift_window_illustrative.json
COPY --chown=10001:10001 examples/${GOVERNEDGRAPH_SAMPLE}/data/fairness/cohort_aggregates_illustrative.csv ./examples/${GOVERNEDGRAPH_SAMPLE}/data/fairness/cohort_aggregates_illustrative.csv
CMD ["python", "-m", "governedgraph.dev.publish_learning_fixtures"]

FROM base AS runtime-base
COPY --chown=10001:10001 examples/${GOVERNEDGRAPH_SAMPLE}/data/synthetic/members.json ./examples/${GOVERNEDGRAPH_SAMPLE}/data/synthetic/members.json
COPY --chown=10001:10001 examples/${GOVERNEDGRAPH_SAMPLE}/member_values_api ./examples/${GOVERNEDGRAPH_SAMPLE}/member_values_api
EXPOSE 8000
# APP_MODULE selects the orchestrator, strategy, values or synthetic contact-centre API.
CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000"]

FROM runtime-base AS dev-voice
COPY --chown=10001:10001 examples/${GOVERNEDGRAPH_SAMPLE}/data/personas/personas.json ./examples/${GOVERNEDGRAPH_SAMPLE}/data/personas/personas.json

FROM runtime-base AS runtime
