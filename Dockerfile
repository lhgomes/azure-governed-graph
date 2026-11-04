FROM python:3.14-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app/examples GOVERNEDGRAPH_MODE=azure
COPY requirements.txt requirements-azure.txt ./
RUN pip install --no-cache-dir -r requirements-azure.txt
COPY src ./src
# The bundled sample domain pack (examples/pension_wtp). Point GOVERNEDGRAPH_DATA_DIR at your own pack instead.
COPY examples/pension_wtp/__init__.py ./examples/pension_wtp/__init__.py
COPY examples/pension_wtp/data/catalogue ./examples/pension_wtp/data/catalogue
COPY examples/pension_wtp/data/content ./examples/pension_wtp/data/content
COPY examples/pension_wtp/data/fairness/thresholds_v1.json ./examples/pension_wtp/data/fairness/thresholds_v1.json
RUN useradd -r -u 10001 app && chown -R app /app
USER app

FROM base AS p2
USER root
COPY requirements-p2.txt ./
RUN pip install --no-cache-dir -r requirements-p2.txt && pip check
USER app

FROM base AS dev-fixtures
COPY --chown=10001:10001 examples/pension_wtp/data/synthetic/outcome_aggregates_illustrative.csv ./examples/pension_wtp/data/synthetic/outcome_aggregates_illustrative.csv
COPY --chown=10001:10001 examples/pension_wtp/data/synthetic/uplift_window_illustrative.json ./examples/pension_wtp/data/synthetic/uplift_window_illustrative.json
COPY --chown=10001:10001 examples/pension_wtp/data/fairness/cohort_aggregates_illustrative.csv ./examples/pension_wtp/data/fairness/cohort_aggregates_illustrative.csv
CMD ["python", "-m", "governedgraph.dev.publish_learning_fixtures"]

FROM base AS runtime-base
COPY --chown=10001:10001 examples/pension_wtp/data/synthetic/members.json ./examples/pension_wtp/data/synthetic/members.json
COPY --chown=10001:10001 examples/pension_wtp/member_values_api ./examples/pension_wtp/member_values_api
EXPOSE 8000
# APP_MODULE selects the orchestrator, strategy, values or synthetic contact-centre API.
CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000"]

FROM runtime-base AS dev-voice
COPY --chown=10001:10001 examples/pension_wtp/data/personas/personas.json ./examples/pension_wtp/data/personas/personas.json

FROM runtime-base AS runtime
