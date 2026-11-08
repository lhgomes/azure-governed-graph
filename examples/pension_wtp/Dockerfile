ARG FRAMEWORK_RUNTIME
FROM ${FRAMEWORK_RUNTIME} AS runtime
ARG SOURCE_SHA
LABEL org.opencontainers.image.revision=${SOURCE_SHA}
ENV PYTHONPATH=/app/src:/app/examples GOVERNEDGRAPH_DATA_DIR=/app/examples/pension_wtp/data \
    GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER=pension_wtp.member_values_api.data:get_member_values \
    GOVERNEDGRAPH_TEXT_DEMO_MODULE=pension_wtp.demo.run_demo
COPY --chown=10001:10001 examples/pension_wtp/__init__.py ./examples/pension_wtp/__init__.py
COPY --chown=10001:10001 examples/pension_wtp/data/catalogue ./examples/pension_wtp/data/catalogue
COPY --chown=10001:10001 examples/pension_wtp/data/content ./examples/pension_wtp/data/content
COPY --chown=10001:10001 examples/pension_wtp/data/fairness/thresholds_v1.json ./examples/pension_wtp/data/fairness/thresholds_v1.json
COPY --chown=10001:10001 examples/pension_wtp/data/synthetic/members.json ./examples/pension_wtp/data/synthetic/members.json
COPY --chown=10001:10001 examples/pension_wtp/member_values_api ./examples/pension_wtp/member_values_api

FROM runtime AS probe
COPY --chown=10001:10001 examples/pension_wtp/data/personas/personas.json ./examples/pension_wtp/data/personas/personas.json
CMD ["python", "-m", "governedgraph.dev.issue_member_token", "--public-jwk"]
