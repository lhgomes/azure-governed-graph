# ACR Tasks builds this image inside Azure. This is NOT built on GitHub runners.
FROM python:3.14-slim AS ci-base
ARG SOURCE_SHA
LABEL org.opencontainers.image.revision=${SOURCE_SHA}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app/examples \
    GOVERNEDGRAPH_DATA_DIR=/app/examples/pension_wtp/data AZURE_ENV_NAME=gg-dev
WORKDIR /app
# Pinned Bicep CLI binary. Compilation occurs in the Azure Container Apps Job,
# not during the ACR build or on a GitHub runner.
ARG BICEP_VERSION=0.49.2
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://github.com/Azure/bicep/releases/download/v${BICEP_VERSION}/bicep-linux-x64" -o /usr/local/bin/bicep \
    && chmod 0755 /usr/local/bin/bicep \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-azure.txt requirements-p2.txt ./
COPY . /app
RUN chmod 0755 /app/infra/ci/run.sh && useradd -r -u 10001 ciuser && chown -R ciuser:ciuser /app
USER ciuser

FROM ci-base AS p1
USER root
RUN python -m pip install --no-cache-dir -r requirements-azure.txt 'pytest>=8,<10' && python -m pip check
USER ciuser
ENV GG_CI_PROFILE=p1
CMD ["/bin/sh", "/app/infra/ci/run.sh"]

FROM ci-base AS p2
USER root
RUN python -m pip install --no-cache-dir -r requirements-p2.txt 'pytest>=8,<10' && python -m pip check
USER ciuser
ENV GG_CI_PROFILE=p2
CMD ["/bin/sh", "/app/infra/ci/run.sh"]
