# SKILL.md — AIA Backend Patterns & How-To Guide

Practical recipes for common tasks in this codebase. Complements `CLAUDE.md`.

Recipes 1–14 cover the **Lambda agent pipeline** (`app/agents/evaluation/`).  
Recipes 15–16 cover the **CoreBackend & Orchestrator FastAPI service** (`app/api/`, `app/orchestrator/`).  
Recipe 17 covers **Podman** (local container runtime).  
Recipe 18 covers **moto_server** (local SQS + S3 mock for E2E testing).  
Recipe 19 covers **pre-commit** (git hooks: security scanning, ruff, mypy, pytest).

---

## 1. Adding a New Specialist Agent

### Step 1 — Define the config

In `src/config.py`, add a new config class following the existing pattern:

```python
class MyNewAgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MY_NEW_AGENT_")
    model: str = Field(default="claude-sonnet-4-6-...")
    temperature: float = Field(default=0.0)
    max_tokens: int = Field(default=4096)

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):
        return (
            EnvSettingsSource(settings_cls),
            YamlSettingsSource(settings_cls),
            InitSettingsSource(settings_cls, init_kwargs={}),
        )
```

Then add the corresponding block in `config.yaml`:
```yaml
agents:
  my_new_agent:
    model: claude-sonnet-4-6-...
    temperature: 0.0
    max_tokens: 4096
```

### Step 2 — Create the agent class

Create `src/agents/my_new_agent.py`:

```python
from anthropic import AsyncAnthropic
from src.agents.schemas import AgentResult, Question
from src.config import MyNewAgentConfig

MY_NEW_AGENT_SYSTEM_PROMPT = """
You are an expert in ... Evaluate the provided document against the checklist.
Return a JSON array of assessments with keys: criterion, rating (Green/Amber/Red),
evidence, recommendation.
"""

class MyNewAgent:
    def __init__(self, client: AsyncAnthropic, config: MyNewAgentConfig | None = None):
        self._client = client
        self._config = config or MyNewAgentConfig()

    async def evaluate(self, document: str, questions: list[Question]) -> AgentResult:
        checklist = "\n".join(f"- {q.text}" for q in questions)
        response = await self._client.messages.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            system=MY_NEW_AGENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Document:\n{document}\n\nChecklist:\n{checklist}"}],
        )
        raw = response.content[0].text
        assessments = extract_json(raw)   # from src/utils/helpers.py
        return AgentResult(assessments=assessments, model=self._config.model)
```

### Step 3 — Add the agent type to config.yaml

```yaml
pipeline:
  agent_types:
    - security
    - data
    - risk
    - ea
    - solution
    - my_new_agent   # ← add here
```

### Step 4 — Wire into the handler

In `src/handlers/agent.py`, add routing:

```python
from src.agents.my_new_agent import MyNewAgent

AGENT_MAP = {
    ...,
    "my_new_agent": MyNewAgent,
}
```

### Step 5 — Write tests

Create `src/tests/agents/test_my_new_agent.py` following the pattern in `test_specialist_agents.py`.

---

## 2. Adding a New Lambda Stage

### Step 1 — Create the handler

Create `src/handlers/my_stage.py`:

```python
import asyncio
from src.config import MyStageConfig
from src.utils.redis_client import get_redis_client
from src.utils.eventbridge import EventBridgePublisher

async def handler_logic(event: dict) -> dict:
    config = MyStageConfig()
    redis = await get_redis_client()
    publisher = EventBridgePublisher()

    document_id = event["detail"]["document_id"]

    # ... stage-specific logic

    await publisher.publish("MyStageComplete", {"document_id": document_id})
    return {"statusCode": 200}

def lambda_handler(event: dict, context: object) -> dict:
    return asyncio.run(handler_logic(event))
```

### Step 2 — Define the EventBridge event schema

Add to `src/agents/schemas.py`:

```python
class MyStageCompleteDetail(BaseModel):
    document_id: str
    timestamp: str
    # ... additional fields
```

### Step 3 — Register in CDK stack

See `plans/10-aws-infrastructure.md` for CDK patterns.

---

## 3. Working with Redis Cache

### Read / Write pattern

```python
from src.utils.redis_client import get_redis_client, key_chunks, key_sections
import json

redis = await get_redis_client()

# Write with TTL
await redis.set(key_chunks(document_id), json.dumps(chunks), ex=86400)  # 24h

# Read
raw = await redis.get(key_sections(document_id, "security"))
if raw is None:
    raise ValueError(f"Sections not found for {document_id}")
sections = json.loads(raw)
```

### Fan-in counter

Used by Stage 7 to know when all 5 agents have completed:

```python
from src.utils.redis_client import key_fan_in_count

# Increment (called by each agent after completing)
count = await redis.incr(key_fan_in_count(document_id))
await redis.expire(key_fan_in_count(document_id), 1800)  # 30m TTL

# Check if all agents done
if count == len(config.pipeline.agent_types):
    # trigger compile
```

### Adding a new key type

Add a helper to `src/utils/redis_client.py`:

```python
def key_my_data(document_id: str) -> str:
    return f"aia:my_data:{document_id}"
```

Keep all key definitions in one place.

---

## 4. Publishing EventBridge Events

```python
from src.utils.eventbridge import EventBridgePublisher
from src.agents.schemas import MyEventDetail

publisher = EventBridgePublisher()

await publisher.publish(
    detail_type="MyEventName",
    detail=MyEventDetail(document_id="doc-123", ...),
)
```

`EventBridgePublisher` wraps the synchronous boto3 call in `asyncio.get_event_loop().run_in_executor()` so it doesn't block the async Lambda handler.

---

## 5. Parsing Documents

```python
from src.utils.document_parser import DocumentParser
from src.config import ParserConfig

parser = DocumentParser(config=ParserConfig())

# From bytes (S3 download)
chunks = await parser.parse(file_bytes=data, filename="document.pdf")
# Returns List[str] — text chunks of ~1500 chars each
```

The parser auto-detects scanned PDFs (< 100 chars per page) and falls back to `docling` OCR. DOCX files use `python-docx`.

---

## 6. Adding Config to config.yaml

Non-secret values belong in `config.yaml`. Pydantic will read them automatically via `YamlSettingsSource`.

```yaml
# config.yaml
my_feature:
  timeout_seconds: 30
  max_retries: 3
  batch_size: 10
```

```python
# src/config.py
class MyFeatureConfig(BaseSettings):
    model_config = SettingsConfigDict(yaml_file="config.yaml", yaml_file_key="my_feature")
    timeout_seconds: int = Field(default=30)
    max_retries: int = Field(default=3)
    batch_size: int = Field(default=10)
```

Env vars always override: `MY_FEATURE_TIMEOUT_SECONDS=60` beats the YAML value.

---

## 7. Database Queries

All DB access is async using `asyncpg`. Follow the pattern in `src/db/questions_repo.py`:

```python
import asyncpg
from src.config import DatabaseConfig

async def fetch_my_data(document_id: str) -> list[dict]:
    config = DatabaseConfig()
    conn = await asyncpg.connect(
        host=config.host, port=config.port,
        database=config.name, user=config.user, password=config.password,
    )
    try:
        rows = await conn.fetch(
            "SELECT * FROM my_table WHERE document_id = $1",
            document_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()
```

Always use parameterized queries (`$1`, `$2`...). Never concatenate user input into SQL.

---

## 8. Testing Patterns

### Mocking the Anthropic client

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from anthropic.types import Message, ContentBlock
from src.agents.security_agent import SecurityAgent

@pytest.fixture
def mock_anthropic():
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=Message(
        id="msg_01",
        model="claude-sonnet-4-6",
        role="assistant",
        type="message",
        stop_reason="end_turn",
        usage=MagicMock(input_tokens=100, output_tokens=200),
        content=[ContentBlock(type="text", text='[{"criterion": "...", "rating": "Green"}]')],
    ))
    return client

async def test_agent_returns_result(mock_anthropic):
    agent = SecurityAgent(client=mock_anthropic)
    result = await agent.evaluate(document="...", questions=[...])
    assert result.assessments[0]["rating"] == "Green"
```

### Mocking Redis

```python
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get.return_value = '["chunk1", "chunk2"]'
    redis.set.return_value = True
    return redis

async def test_handler(mock_redis):
    with patch("src.handlers.tag.get_redis_client", return_value=mock_redis):
        result = await handler_logic({"detail": {"document_id": "doc-123"}})
    assert result["statusCode"] == 200
```

### Mocking EventBridge

```python
with patch("src.utils.eventbridge.EventBridgePublisher.publish", new_callable=AsyncMock):
    await handler_logic(event)
```

---

## 9. Handling LLM Response JSON

Claude may wrap JSON in markdown code fences. Always use the helper:

```python
from src.utils.helpers import extract_json

raw_text = response.content[0].text
data = extract_json(raw_text)  # strips ```json ... ``` fences, parses JSON
```

If the LLM returns malformed JSON, `extract_json` raises `ValueError`. Catch it in the agent and surface as a pipeline failure event.

---

## 10. SQS Payload Size Management

SQS has a 256 KB message limit. The pipeline uses 240 KB as the safe threshold (configured in `config.yaml`). When section content exceeds this:

```python
from src.config import PipelineConfig

config = PipelineConfig()
payload = json.dumps(sections)

if len(payload.encode()) > config.sqs_payload_threshold_bytes:
    # Store sections in Redis, send only the Redis key via SQS
    await redis.set(key_sections(doc_id, agent_type), payload, ex=3600)
    sqs_message = {"document_id": doc_id, "agent_type": agent_type, "use_redis": True}
else:
    sqs_message = {"document_id": doc_id, "agent_type": agent_type, "sections": sections}
```

The agent handler (`src/handlers/agent.py`) checks `use_redis` and fetches from Redis accordingly.

---

## 11. Error Handling in Lambda Handlers

```python
async def handler_logic(event: dict) -> dict:
    document_id = event["detail"]["document_id"]
    try:
        # ... stage logic
    except ScannedPdfError:
        await publisher.publish("PipelineError", {
            "document_id": document_id,
            "stage": "parse",
            "error": "Scanned PDF cannot be processed",
        })
        return {"statusCode": 400}
    except Exception as exc:
        logger.exception("Unexpected error in stage X for %s", document_id)
        await publisher.publish("PipelineError", {
            "document_id": document_id,
            "stage": "X",
            "error": str(exc),
        })
        raise   # Let Lambda retry / DLQ handle it
```

Use `raise` for unexpected errors to trigger Lambda retries and eventual DLQ routing.

---

## 12. Ruff & mypy Cheat Sheet

```bash
# Auto-fix lint issues
ruff check src/ --fix

# Format all source files
ruff format src/

# Type check (strict) — must pass with zero errors
mypy src/

# Run a single test file
pytest src/tests/agents/test_security_agent.py -v

# Run only tests matching a pattern
pytest src/tests/ -k "test_parse" -v

# Show coverage for a specific module
pytest src/tests/ --cov=src/handlers --cov-report=term-missing
```

---

## 13. CloudWatch Observability

Handlers emit custom metrics via `boto3` CloudWatch put_metric_data. Follow the pattern in `plans/11-observability.md`:

```python
import boto3
from src.config import EventBridgeConfig

cw = boto3.client("cloudwatch", region_name=EventBridgeConfig().region)
cw.put_metric_data(
    Namespace="AIA/Pipeline",
    MetricData=[{
        "MetricName": "StageLatency",
        "Dimensions": [{"Name": "Stage", "Value": "parse"}],
        "Value": elapsed_ms,
        "Unit": "Milliseconds",
    }],
)
```

---

## 14. Deploying a Lambda Update

Deployment is manual until root-level CI/CD is added:

```bash
cd app/agents/evaluation

# Package
pip install -r requirements.txt -t ./package/
cp -r src/ ./package/
cd package && zip -r ../lambda.zip . && cd ..

# Deploy via AWS CLI
aws lambda update-function-code \
  --function-name aia-stage-parse \
  --zip-file fileb://lambda.zip \
  --region eu-west-2
```

For the `datapipeline` package, GitHub Actions handles deployment automatically on push to `main`.

---

## 15. Adding a New Template Type (CoreBackend / Orchestrator)

A template type controls which specialist agents run for a given document. The mapping is defined in `app/core/config.py` — no YAML file, no DB query.

### Step 1 — Add the template entry

```python
# app/core/config.py
TEMPLATE_AGENTS: dict[str, list[str]] = {
    "SDA":   ["security", "data", "risk", "ea", "solution"],
    "CHEDP": ["security", "data", "risk"],
    "HLD":   ["security", "ea"],          # ← new template
}
```

### Step 2 — Verify fallback behaviour

If a document arrives with a `templateType` that has no entry in `TEMPLATE_AGENTS`, the Orchestrator falls back to a single agent of type `ORCHESTRATOR_DEFAULT_AGENT_TYPE` (env var, default `general`). No code change is needed for the fallback.

### Step 3 — Update the frontend API reference

Add the new value to the `templateType` field description in [docs/corebackend-api.md](docs/corebackend-api.md) so the frontend team knows it is accepted.

### Step 4 — Redeploy

Template config is loaded at import time. A service restart is required to pick up the change.

---

## 17. Podman — Local Container Runtime

Podman is installed at `/opt/podman/bin/podman` (not on PATH by default).  
Always invoke it with the full path, or add it to your shell:

```bash
export PATH="/opt/podman/bin:$PATH"
```

### Check machine and container status

```bash
# Is the Podman VM running?
/opt/podman/bin/podman machine list

# List running containers
/opt/podman/bin/podman ps

# List all containers (including stopped)
/opt/podman/bin/podman ps -a

# List pulled images
/opt/podman/bin/podman images
```

### Start / stop the Podman VM

```bash
# Start the VM (needed after reboot)
/opt/podman/bin/podman machine start

# Stop the VM
/opt/podman/bin/podman machine stop
```

The default machine is `podman-machine-default` (applehv, 5 CPUs, 2 GiB RAM, 100 GiB disk).  
PostgreSQL container `aiadocuments` runs here on port `5432`.

### Start LocalStack (SQS + S3 for local dev)

```bash
# Pull image (only needed once — ~600 MB, can fail on slow network; retry if EOF)
/opt/podman/bin/podman pull docker.io/localstack/localstack:3

# Start container
/opt/podman/bin/podman run -d \
  --name localstack \
  -p 4566:4566 \
  -e SERVICES=s3,sqs \
  -e DEFAULT_REGION=eu-west-2 \
  docker.io/localstack/localstack:3

# Wait ~10 s, then create queues and bucket
bash scripts/start-localstack.sh

# Verify LocalStack is ready
curl -s http://localhost:4566/_localstack/health | python3 -m json.tool
```

### Stop / remove LocalStack

```bash
/opt/podman/bin/podman stop localstack
/opt/podman/bin/podman rm localstack
```

### Start PostgreSQL (if stopped)

```bash
/opt/podman/bin/podman start aiadocuments

# Verify
/opt/podman/bin/podman exec aiadocuments pg_isready -U aiauser
```

### Full local dev startup sequence

```bash
# 1. Start containers
/opt/podman/bin/podman machine start          # if VM not running
/opt/podman/bin/podman start aiadocuments     # PostgreSQL
/opt/podman/bin/podman start localstack       # LocalStack (if already created)
# OR first-time LocalStack:
/opt/podman/bin/podman run -d --name localstack -p 4566:4566 \
  -e SERVICES=s3,sqs -e DEFAULT_REGION=eu-west-2 \
  docker.io/localstack/localstack:3
bash scripts/start-localstack.sh

# 2. Start application services (three terminals)
uvicorn app.api.main:app --host 127.0.0.1 --port 8086 --reload
uvicorn app.orchestrator.main:app --host 127.0.0.1 --port 8001 --reload
uvicorn app.relay_service.main:app --host 127.0.0.1 --port 8002

# 3. Test via mock harness
python scripts/mock_orchestrator.py --agent-types security technical
```

### Kill a port-bound process

```bash
lsof -ti :<port> | xargs kill -9
# e.g.
lsof -ti :8002 | xargs kill -9
```

---

## 18. Local AWS Mock — SQS & S3 with moto_server

`moto_server` is a free, zero-Docker HTTP server that emulates AWS SQS and S3.
It is the preferred local mock: no image pull, no auth token, no container overhead.

### Why moto_server instead of LocalStack

| | moto_server | LocalStack (pip) |
|---|---|---|
| Cost | Free | Requires paid auth token |
| Setup | `pip install moto[server]` | `pip install localstack` + `LOCALSTACK_AUTH_TOKEN` |
| Docker | Not required | Not required (pip version) |
| Container | Not required | Not required (pip version) |
| Startup | Single command | `localstack start` (slower) |

### Install

```bash
pip install "moto[server,sqs,s3]"
```

### Start the server

The account ID **must** match the account embedded in the `.env` queue URLs (`000000000000`).

```bash
MOTO_ACCOUNT_ID=000000000000 moto_server -p 4566
# Listening on http://0.0.0.0:4566 ...
```

Run this in a dedicated terminal or in the background:

```bash
MOTO_ACCOUNT_ID=000000000000 moto_server -p 4566 &
```

### Create queues and bucket

After the server starts, run the provisioning script:

```bash
bash scripts/start-localstack.sh
```

This script creates:
- SQS queue `aia-tasks` → `http://localhost:4566/000000000000/aia-tasks`
- SQS queue `aia-status` → `http://localhost:4566/000000000000/aia-status`
- S3 bucket `pocldnaia001`

### Relevant `.env` settings

```dotenv
AWS_ENDPOINT_URL=http://localhost:4566   # routes all boto3/aiobotocore calls to moto
AWS_ACCESS_KEY_ID=ASIAXIWWR5475WVV5RJN   # any non-empty value works with moto
AWS_SECRET_ACCESS_KEY=<any>
AWS_DEFAULT_REGION=eu-west-2

TASK_QUEUE_URL=http://localhost:4566/000000000000/aia-tasks
STATUS_QUEUE_URL=http://localhost:4566/000000000000/aia-status
S3_BUCKET_NAME=pocldnaia001
```

`AWS_SESSION_TOKEN` is ignored by moto — leave it set or remove it, either works.

### Verify queues and bucket exist

```bash
# List SQS queues
aws --endpoint-url http://localhost:4566 sqs list-queues --region eu-west-2

# List S3 buckets
aws --endpoint-url http://localhost:4566 s3 ls

# Peek at aia-status (non-destructive, visibility_timeout=30s)
aws --endpoint-url http://localhost:4566 sqs receive-message \
  --queue-url http://localhost:4566/000000000000/aia-status \
  --visibility-timeout 30 \
  --region eu-west-2
```

Or use the inline Python helper:

```bash
python -c "
import asyncio, json, os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv('.env')
import aiobotocore.session as aio

async def peek(queue_name):
    url = f\"http://localhost:4566/000000000000/{queue_name}\"
    session = aio.get_session()
    async with session.create_client('sqs', region_name='eu-west-2',
            endpoint_url='http://localhost:4566',
            aws_access_key_id='test', aws_secret_access_key='test') as sqs:
        resp = await sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10,
                                         WaitTimeSeconds=0, VisibilityTimeout=30)
        msgs = resp.get('Messages', [])
        print(f'{queue_name}: {len(msgs)} message(s)')
        for m in msgs:
            print(json.dumps(json.loads(m['Body']), indent=2))

asyncio.run(peek('aia-status'))
"
```

### Full E2E startup sequence (moto_server variant)

```bash
# Terminal 1 — SQS + S3 mock
MOTO_ACCOUNT_ID=000000000000 moto_server -p 4566

# Terminal 2 — provision queues + bucket (once per moto_server restart)
bash scripts/start-localstack.sh

# Terminal 3 — PostgreSQL (must be running for questions_repo)
/opt/podman/bin/podman start aiadocuments

# Terminal 4 — Relay Service (calls real LLM via Anthropic API)
uvicorn app.relay_service.main:app --host 127.0.0.1 --port 8002

# Terminal 5 — push a task and watch the result
python scripts/mock_orchestrator.py \
  --agent-types security technical \
  --file app/agents/evaluation/files/security_policy.md
```

### Reset moto state

moto_server state is in-memory only. Restarting the process clears all queues, messages, and bucket contents. Re-run `scripts/start-localstack.sh` after each restart.

### Using moto in pytest (unit/integration tests)

For tests that need SQS/S3 without a running server, use the moto decorators instead:

```python
import boto3
import pytest
from moto import mock_aws

@mock_aws
def test_sqs_round_trip():
    sqs = boto3.client("sqs", region_name="eu-west-2")
    url = sqs.create_queue(QueueName="aia-tasks")["QueueUrl"]
    sqs.send_message(QueueUrl=url, MessageBody='{"hello": "world"}')
    resp = sqs.receive_message(QueueUrl=url)
    assert json.loads(resp["Messages"][0]["Body"]) == {"hello": "world"}
```

`@mock_aws` intercepts all boto3 calls in-process — no server required.

---

## 16. Writing Async Tests for the Orchestrator

Orchestrator tests use `pytest-asyncio` (included in `requirements-dev.txt`). Run from the repo root with `PYTHONPATH=.`.

### Basic async test

```python
import pytest

@pytest.mark.asyncio
async def test_something_async():
    result = await my_async_function()
    assert result == expected
```

### Testing SessionStore

```python
from app.orchestrator.session import SessionStore

@pytest.mark.asyncio
async def test_session_complete():
    store = SessionStore()
    session = await store.create("doc-1", "SDA", "doc-1_test.docx", {"doc-1_security"})

    all_done = await store.record_result("doc-1", "doc-1_security", {"score": 90})

    assert all_done is True
    assert session.completion_event.is_set()
```

### Testing _process_document (patching internal dependencies)

`_process_document` instantiates its own service objects, so patch at the class level:

```python
from unittest.mock import AsyncMock, MagicMock, patch
from app.orchestrator.main import _process_document
from app.orchestrator.session import DocumentSession

@pytest.mark.asyncio
async def test_process_complete():
    session = DocumentSession(
        doc_id="doc-1", template_type="SDA", s3_key="doc-1_test.docx",
        expected_task_ids={"doc-1_security"},
        collected_results={"doc-1_security": {"score": 90}},
    )
    session.completion_event.set()

    mock_repo = AsyncMock()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main.IngestorService") as mock_ingestor_cls,
        patch("app.orchestrator.main._session_store") as mock_store,
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_ingestor_cls.return_value.extract_text_from_docx = MagicMock(return_value="text")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()

        await _process_document("doc-1", "doc-1_test.docx", "SDA")

    mock_repo.update_status.assert_called_with("doc-1", "COMPLETE", result_md=ANY)
```

### Simulating timeout

```python
import asyncio
from unittest.mock import patch

@pytest.mark.asyncio
async def test_process_timeout():
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        await _process_document(...)
```

### Running orchestrator tests

```bash
PYTHONPATH=. pytest tests/test_orchestrator_session.py -v
PYTHONPATH=. pytest tests/test_orchestrator_summary.py -v
PYTHONPATH=. pytest tests/test_orchestrator_processing.py -v
```

---

## Recipe 19 — pre-commit hooks (security, ruff, mypy, pytest)

All hooks use `language: system` — they run against tools in the **active conda/venv environment**. No GitHub downloads; works behind corporate SSL proxies.

### One-time setup

```bash
pip install -r requirements-dev.txt          # adds pre-commit + detect-secrets
pre-commit install                            # wire the pre-commit stage
pre-commit install --hook-type pre-push       # wire the pre-push stage (pytest)
detect-secrets scan > .secrets.baseline      # allowlist existing known non-secrets
```

### Hook summary

| Stage | Hook | What it does |
|-------|------|--------------|
| pre-commit | `detect-secrets` | Baseline-driven secret scanner; blocks new secrets only |
| pre-commit | `gitleaks` | Pattern scanner for 150+ provider token types (skips gracefully if not installed: `brew install gitleaks`) |
| pre-commit | `detect-private-key` | Regex scan for PEM private-key headers |
| pre-commit | `no-commit-to-main` | Blocks direct commits to the `main` branch |
| pre-commit | `check-merge-conflict` | Detects unresolved `<<<<<<` / `>>>>>>>` markers |
| pre-commit | `check-large-files` | Blocks files over 500 KB |
| pre-commit | `check-yaml` / `check-json` / `check-toml` | Syntax validation |
| pre-commit | `ruff-lint` | `ruff check --fix` scoped to `app/` |
| pre-commit | `ruff-format` | `ruff format` scoped to `app/` |
| pre-commit | `mypy` | Type-checks `app/agents/evaluation/` |
| pre-push | `pytest-evaluation` | Runs evaluation handler + DB tests |
| pre-push | `pytest-datapipeline` | Runs datapipeline tests |

Hook scripts live in `scripts/hooks/` (pure Python, no external dependencies beyond stdlib + PyYAML/tomllib).

### Running manually

```bash
# Run all pre-commit hooks against every file
pre-commit run --all-files

# Run just one hook
pre-commit run ruff-lint --all-files
pre-commit run detect-secrets --all-files

# Simulate the pre-push stage
pre-commit run --all-files --hook-stage pre-push
```

### Allowlisting a false-positive secret

```bash
detect-secrets audit .secrets.baseline
# mark the entry as a false positive in the interactive CLI
```

### Updating the secrets baseline after adding new test fixtures

```bash
detect-secrets scan --baseline .secrets.baseline
```

### Skipping hooks in an emergency (use sparingly)

```bash
SKIP=mypy git commit -m "chore: emergency fix"   # skip one hook by id
git commit --no-verify                            # skip all hooks
```

### Adding a new hook

1. Add a Python script to `scripts/hooks/` (exit code 0 = pass, non-zero = fail).
2. Add a stanza to `.pre-commit-config.yaml` under `repo: local`.
3. Run `pre-commit run <id> --all-files` to verify it behaves correctly.
