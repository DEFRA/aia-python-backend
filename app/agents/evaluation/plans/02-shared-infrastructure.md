# Plan 02 — Shared Infrastructure (Redis + EventBridge)

**Priority:** 2 (all Lambda handlers depend on these utilities)

**Depends on:** Plan 01 (src tree in place)

---

## Goal

Implement the two shared utility modules used by every Lambda handler in the pipeline:

1. `src/utils/redis_client.py` — async Redis connection and typed key helpers
2. `src/utils/eventbridge.py` — EventBridge event publisher

Also extend `src/config.py` with `RedisConfig` and `EventBridgeConfig`.

---

## `src/config.py` — Add New Config Classes

Add to the existing config module (do not break `SecurityAgentConfig`, `DatabaseConfig`):

```python
class RedisConfig(BaseSettings):
    host: str = Field(..., env="REDIS_HOST")
    port: int = Field(6379, env="REDIS_PORT")
    ssl: bool = Field(True, env="REDIS_SSL")          # True in prod (ElastiCache TLS)
    db: int = Field(0, env="REDIS_DB")
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 3.0

class EventBridgeConfig(BaseSettings):
    bus_name: str = Field("defra-pipeline", env="EVENTBRIDGE_BUS_NAME")
    source: str = "defra.pipeline"
    region: str = Field("eu-west-2", env="AWS_REGION")
```

---

## `src/utils/redis_client.py`

### Responsibilities
- Single async Redis connection pool shared within a Lambda invocation
- Typed `get`/`set`/`incr`/`delete` wrappers with JSON serialisation
- TTL constants matching the Redis Key Reference table in `aws_event_driven_orchestration.md`

### Interface

```python
import json
from typing import Any

import redis.asyncio as aioredis

from src.config import RedisConfig

# TTL constants (seconds)
TTL_CHUNKS = 86_400        # 24h — chunks:{content_hash}
TTL_TAGGED = 86_400        # 24h — tagged:{content_hash}
TTL_SECTIONS = 3_600       # 1h  — sections:{docId}:{agentType}
TTL_QUESTIONS = 3_600      # 1h  — questions:{agentType}
TTL_RESULT = 3_600         # 1h  — result:{docId}:{agentType}
TTL_RESULTS_COUNT = 3_600  # 1h  — results_count:{docId}
TTL_COMPILED = 3_600       # 1h  — compiled:{docId}
TTL_STAGE8_COUNT = 1_800   # 30m — stage8_count:{docId}
# receipt:{docId} TTL is set equal to SQS visibility timeout at write time

_pool: aioredis.Redis | None = None


async def get_redis(config: RedisConfig | None = None) -> aioredis.Redis:
    """Return (or create) the shared async Redis connection pool."""
    ...


async def redis_get_json(client: aioredis.Redis, key: str) -> Any | None:
    """Get a JSON-decoded value. Returns None on cache miss."""
    ...


async def redis_set_json(
    client: aioredis.Redis, key: str, value: Any, ttl: int
) -> None:
    """JSON-encode and set a value with TTL."""
    ...


async def redis_incr(client: aioredis.Redis, key: str, ttl: int) -> int:
    """Increment a counter, setting TTL only on first write. Returns new value."""
    ...


async def redis_delete_many(client: aioredis.Redis, *keys: str) -> None:
    """Delete multiple keys in a single pipeline call."""
    ...
```

### Key naming helpers

```python
def key_chunks(content_hash: str) -> str: return f"chunks:{content_hash}"
def key_tagged(content_hash: str) -> str: return f"tagged:{content_hash}"
def key_sections(doc_id: str, agent_type: str) -> str: return f"sections:{doc_id}:{agent_type}"
def key_questions(agent_type: str) -> str: return f"questions:{agent_type}"
def key_result(doc_id: str, agent_type: str) -> str: return f"result:{doc_id}:{agent_type}"
def key_results_count(doc_id: str) -> str: return f"results_count:{doc_id}"
def key_compiled(doc_id: str) -> str: return f"compiled:{doc_id}"
def key_stage8_count(doc_id: str) -> str: return f"stage8_count:{doc_id}"
def key_receipt(doc_id: str) -> str: return f"receipt:{doc_id}"
```

### Implementation notes
- Use `redis.asyncio` (bundled with `redis>=4.2`; already in requirements or add it)
- `get_redis()` creates the pool once per Lambda cold start; subsequent calls return the same object
- Use `ssl=True` + `ssl_cert_reqs="none"` for ElastiCache in-transit encryption without cert pinning
- `redis_incr` must use a pipeline: `INCR key` then `EXPIRE key ttl` **only if** `INCR` returns `1` (first write) — avoids resetting TTL on every increment

---

## `src/utils/eventbridge.py`

### Responsibilities
- Publish events to the `defra-pipeline` custom bus
- Enforce the standard event envelope (`source`, `detail-type`, `detail`)
- Wrap boto3 `put_events` and surface errors with context

### Interface

```python
import json
from typing import Any

import boto3

from src.config import EventBridgeConfig


class EventBridgePublisher:
    """Publishes events to the defra-pipeline EventBridge bus."""

    def __init__(self, config: EventBridgeConfig | None = None) -> None: ...

    async def publish(
        self,
        detail_type: str,
        detail: dict[str, Any],
    ) -> None:
        """Publish one event. Raises on failure (let Lambda retry via SQS DLQ)."""
        ...
```

### Event envelope produced

```json
{
  "Source": "defra.pipeline",
  "DetailType": "<detail_type>",
  "Detail": "<json-encoded detail>",
  "EventBusName": "defra-pipeline"
}
```

### Implementation notes
- Use `boto3.client("events")` — synchronous; wrap in `asyncio.get_event_loop().run_in_executor(None, ...)` so it fits the async handler pattern
- Check `FailedEntryCount` on the response; raise `RuntimeError` if > 0
- Keep the publisher stateless (no retry logic here — Lambda + EventBridge handles that)

---

## `src/agents/schemas.py` — Add Shared Event Models

Extend the existing schemas file with Pydantic models for all EventBridge event
`detail` payloads — these are the Pydantic boundary at every inter-Lambda interface:

```python
class DocumentParsedDetail(BaseModel):
    docId: str
    chunksCacheKey: str    # e.g. "chunks:abc123"
    contentHash: str       # sha256 of file bytes

class DocumentTaggedDetail(BaseModel):
    docId: str
    taggedCacheKey: str    # e.g. "tagged:abc123"
    contentHash: str

class SectionsReadyDetail(BaseModel):
    docId: str
    agentType: Literal["security", "data", "risk", "ea", "solution"]

class AgentCompleteDetail(BaseModel):
    docId: str
    agentType: str

class AllAgentsCompleteDetail(BaseModel):
    docId: str

class DocumentCompiledDetail(BaseModel):
    docId: str
    compiledCacheKey: str  # e.g. "compiled:UUID-1234"

class ResultPersistedDetail(BaseModel):
    docId: str

class DocumentMovedDetail(BaseModel):
    docId: str
    destination: Literal["completed", "error"]

class FinaliseReadyDetail(BaseModel):
    docId: str

class PipelineCompleteDetail(BaseModel):
    docId: str
    status: Literal["completed", "error"]
```

---

## Verification

```bash
python -c "from src.utils.redis_client import get_redis, key_chunks, TTL_CHUNKS"
python -c "from src.utils.eventbridge import EventBridgePublisher"
python -c "from src.config import RedisConfig, EventBridgeConfig"
python -c "from src.agents.schemas import DocumentParsedDetail, SectionsReadyDetail"
ruff check src/utils/redis_client.py src/utils/eventbridge.py
mypy src/utils/redis_client.py src/utils/eventbridge.py src/config.py
```

---

## Acceptance Criteria

- [ ] `RedisConfig` and `EventBridgeConfig` added to `src/config.py`
- [ ] `src/utils/redis_client.py` implemented with all key helpers and TTL constants
- [ ] `src/utils/eventbridge.py` implemented with `EventBridgePublisher.publish()`
- [ ] All EventBridge detail Pydantic models added to `src/agents/schemas.py`
- [ ] No existing tests broken
- [ ] `ruff check .` and `mypy src/` pass
