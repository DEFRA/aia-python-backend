FROM python:3.13-slim AS base

ARG PYTHON_ENV=production
ARG HOST=0.0.0.0
ARG PORT=8002
ARG LOG_LEVEL=INFO
ARG DB_HOST
ARG DB_PORT=5432
ARG DB_NAME
ARG DB_USER
ARG DB_PASSWORD
ARG AWS_DEFAULT_REGION
ARG AWS_ACCESS_KEY_ID
ARG AWS_SECRET_ACCESS_KEY
ARG AWS_SESSION_TOKEN
ARG SQS_TASK_QUEUE_URL
ARG SQS_STATUS_QUEUE_URL
ARG S3_BUCKET_NAME
ARG MODEL_ID
ARG LLM_PROVIDER=bedrock
ARG MAX_PRIORITY_ACTIONS=10
ARG AGENT_TIMEOUT_SECONDS=480
ARG WORKER_STUCK_TASK_TIMEOUT_MINUTES=120

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    PYTHON_ENV=${PYTHON_ENV} \
    PORT=${PORT} \
    HOST=${HOST} \
    LOG_LEVEL=${LOG_LEVEL} \
    DB_HOST=${DB_HOST} \
    DB_PORT=${DB_PORT} \
    DB_NAME=${DB_NAME} \
    DB_USER=${DB_USER} \
    DB_PASSWORD=${DB_PASSWORD} \
    AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION} \
    AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID} \
    AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY} \
    AWS_SESSION_TOKEN=${AWS_SESSION_TOKEN} \
    TASK_QUEUE_URL=${SQS_TASK_QUEUE_URL} \
    STATUS_QUEUE_URL=${SQS_STATUS_QUEUE_URL} \
    S3_BUCKET_NAME=${S3_BUCKET_NAME} \
    MODEL_ID=${MODEL_ID} \
    LLM_PROVIDER=${LLM_PROVIDER} \
    MAX_PRIORITY_ACTIONS=${MAX_PRIORITY_ACTIONS} \
    AGENT_TIMEOUT_SECONDS=${AGENT_TIMEOUT_SECONDS} \
    WORKER_STUCK_TASK_TIMEOUT_MINUTES=${WORKER_STUCK_TASK_TIMEOUT_MINUTES}

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Place source under app/agent_service/ so imports like
# `from app.agent_service.*` resolve with PYTHONPATH=/app
COPY . app/agent_service/
RUN touch app/__init__.py

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
 && chown -R app:app /app
USER app

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://127.0.0.1:8002/health || exit 1

CMD ["sh", "-c", "uvicorn app.agent_service.main:app --host ${HOST} --port ${PORT}"]
