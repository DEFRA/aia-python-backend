FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY app/orchestrator/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY app ./app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
 && chown -R app:app /app
USER app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://127.0.0.1:8001/docs || exit 1

CMD ["uvicorn", "app.orchestrator.main:app", "--host", "0.0.0.0", "--port", "8001"]
