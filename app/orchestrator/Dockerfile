# Agent timeout (used in orchestrator .env)
FROM python:3.13-slim AS base

# Build-time arguments (only those relevant for orchestrator)
ARG PYTHON_ENV=production
ARG HOST=0.0.0.0
ARG PORT=8001
ARG LOG_LEVEL=INFO

# --- AWS credentials (optional: required for local, not for ECS role) ---
ARG AWS_DEFAULT_REGION
ARG AWS_ACCESS_KEY_ID
ARG AWS_SECRET_ACCESS_KEY
ARG AWS_SESSION_TOKEN
ARG S3_BUCKET_NAME
ARG STATUS_QUEUE_URL
ARG TASK_QUEUE_URL

ARG ORCHESTRATOR_DEFAULT_AGENT_TYPE
ARG MAX_PRIORITY_ACTIONS=10

ARG S3_UPLOAD_PREFIX=uploaded_docs

ARG DB_HOST
ARG DB_PORT=5432
ARG DB_NAME
ARG DB_USER
ARG DB_PASSWORD

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    PYTHON_ENV=${PYTHON_ENV} \
    HOST=${HOST} \
    PORT=${PORT} \
    LOG_LEVEL=${LOG_LEVEL} \
    ORCHESTRATOR_DEFAULT_AGENT_TYPE=${ORCHESTRATOR_DEFAULT_AGENT_TYPE} \
    MAX_PRIORITY_ACTIONS=${MAX_PRIORITY_ACTIONS} \
    S3_UPLOAD_PREFIX=${S3_UPLOAD_PREFIX} \
    AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION} \
    AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID} \
    AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY} \
    AWS_SESSION_TOKEN=${AWS_SESSION_TOKEN} \
    S3_BUCKET_NAME=${S3_BUCKET_NAME} \
    STATUS_QUEUE_URL=${STATUS_QUEUE_URL} \
    TASK_QUEUE_URL=${TASK_QUEUE_URL} \
    DB_HOST=${DB_HOST} \
    DB_PORT=${DB_PORT} \
    DB_NAME=${DB_NAME} \
    DB_USER=${DB_USER} \
    DB_PASSWORD=${DB_PASSWORD}

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY app/orchestrator/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY app/orchestrator ./app/orchestrator

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
 && chown -R app:app /app
USER app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://127.0.0.1:8001/docs || exit 1

CMD ["sh", "-c", "uvicorn app.orchestrator.src.main:app --host ${HOST} --port ${PORT}"]