FROM python:3.14-slim AS core
ARG SOURCE_SHA
LABEL org.opencontainers.image.revision=${SOURCE_SHA}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
RUN useradd -r -u 10001 app
COPY src ./src

FROM core AS runtime
COPY requirements.txt requirements-azure.txt ./
RUN pip install --no-cache-dir -r requirements-azure.txt && pip check
USER app
EXPOSE 8000
CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000"]
