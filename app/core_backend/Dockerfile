FROM python:3.13-slim AS base

ARG PYTHON_ENV=production
ARG HOST=0.0.0.0
ARG PORT=8086
ARG LOG_LEVEL=INFO
ARG USER_ID_HEADER=x-user-id
ARG JWT_SECRET
ARG DB_HOST
ARG DB_PORT=5432
ARG DB_NAME
ARG DB_USER
ARG DB_PASSWORD
ARG AWS_DEFAULT_REGION
ARG AWS_SESSION_TOKEN
ARG S3_BUCKET_NAME
ARG S3_UPLOAD_PREFIX
ARG ORCHESTRATOR_URL
ARG MAX_FILE_UPLOAD

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    PYTHON_ENV=${PYTHON_ENV} \
    PORT=${PORT} \
    HOST=${HOST} \
    LOG_LEVEL=${LOG_LEVEL} \
    USER_ID_HEADER=${USER_ID_HEADER} \
    JWT_SECRET=${JWT_SECRET} \
    DB_HOST=${DB_HOST} \
    DB_PORT=${DB_PORT} \
    DB_NAME=${DB_NAME} \
    DB_USER=${DB_USER} \
    DB_PASSWORD=${DB_PASSWORD} \
    AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION} \
    AWS_SESSION_TOKEN=${AWS_SESSION_TOKEN} \
    S3_BUCKET_NAME=${S3_BUCKET_NAME} \
    S3_UPLOAD_PREFIX=${S3_UPLOAD_PREFIX} \
    ORCHESTRATOR_URL=${ORCHESTRATOR_URL} \
    MAX_FILE_UPLOAD=${MAX_FILE_UPLOAD}

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY src/ ./

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
 && chown -R app:app /app
USER app

EXPOSE 8086

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://127.0.0.1:8086/health || exit 1

CMD ["sh", "-c", "uvicorn api.main:app --host ${HOST} --port ${PORT}"]