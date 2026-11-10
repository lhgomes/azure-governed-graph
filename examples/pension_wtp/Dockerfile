ARG FRAMEWORK_RUNTIME
FROM ${FRAMEWORK_RUNTIME} AS sample-base
ARG SOURCE_SHA
LABEL org.opencontainers.image.revision=${SOURCE_SHA}
ENV PYTHONPATH=/app/src:/app/examples GOVERNEDGRAPH_DATA_DIR=/app/examples/pension_wtp/data
COPY --chown=10001:10001 examples/pension_wtp/__init__.py ./examples/pension_wtp/__init__.py
COPY --chown=10001:10001 examples/pension_wtp/data/catalogue ./examples/pension_wtp/data/catalogue
COPY --chown=10001:10001 examples/pension_wtp/data/content ./examples/pension_wtp/data/content
COPY --chown=10001:10001 examples/pension_wtp/data/fairness/thresholds_v1.json ./examples/pension_wtp/data/fairness/thresholds_v1.json
COPY --chown=10001:10001 examples/pension_wtp/data/synthetic/members.json ./examples/pension_wtp/data/synthetic/members.json
COPY --chown=10001:10001 examples/pension_wtp/member_values_api ./examples/pension_wtp/member_values_api

FROM sample-base AS simulation-builder
COPY --chown=10001:10001 examples/pension_wtp/data/personas/personas.json ./examples/pension_wtp/data/personas/personas.json
COPY --chown=10001:10001 examples/pension_wtp/demo/build_simulations.py ./examples/pension_wtp/demo/build_simulations.py
RUN python examples/pension_wtp/demo/build_simulations.py --output /tmp/simulations.json

FROM sample-base AS runtime
COPY --from=simulation-builder --chown=10001:10001 /tmp/simulations.json ./examples/pension_wtp/data/demo/simulations.json

FROM runtime AS probe
ENV GOVERNEDGRAPH_EVAL_DATA_DIR=/app/examples/pension_wtp/evaluation/datasets
COPY --chown=10001:10001 examples/pension_wtp/evaluation/datasets ./examples/pension_wtp/evaluation/datasets
COPY --chown=10001:10001 examples/pension_wtp/data/personas/personas.json ./examples/pension_wtp/data/personas/personas.json
CMD ["python", "-m", "governedgraph.dev.issue_member_token", "--public-jwk"]
