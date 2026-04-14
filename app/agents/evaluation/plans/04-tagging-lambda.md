# Plan 04 — Tagging Agent + Lambda (Stage 4)

**Priority:** 4

**Depends on:** Plan 01, Plan 02, Plan 03 (DocumentParsed event + chunks in Redis)

---

## Goal

Implement the tagging stage:

1. `src/agents/prompts/tagging.py` — TAXONOMY constant + SYSTEM_PROMPT
2. `src/agents/tagging_agent.py` — `TaggingAgent` class
3. `src/handlers/tag.py` — Stage 4 Lambda handler

The tagging agent is the **most expensive LLM call** in the pipeline. Its output is
cached in Redis so re-runs with the same document skip the Claude call entirely.

---

## Source material

Full tagging implementation is specified in `files/pdf_security_tagger.md` (Stage 3
of that guide). Adapt it to the async pattern and the Lambda handler structure used
across the project.

---

## `src/agents/prompts/tagging.py`

```python
"""Tagging agent prompts and taxonomy."""

TAXONOMY: dict[str, str] = {
    "authentication": "Identity verification, MFA, passwords, session tokens, SSO",
    "authorisation": "Access control, permissions, RBAC, privilege, provisioning",
    "encryption": "TLS, AES, key management, data at rest/in transit",
    "vulnerability_management": "CVE, SAST, DAST, patching, dependency scanning",
    "audit_logging": "SIEM, event logging, audit trails, monitoring",
    "data_governance": "Data classification, retention, ownership, lineage, compliance",
    "incident_response": "Breach, remediation, SLA, escalation, forensics",
    "secrets_management": "API keys, credentials, vaults, rotation",
    "network_security": "Firewall, VPN, TLS, segmentation, ingress/egress",
    "compliance": "GDPR, ISO27001, SOC2, regulatory, policy",
}

SYSTEM_PROMPT: str = """You are a document security analyst. You will receive a JSON array
of document chunks. For each chunk, determine which security or governance topics it covers.

Available tags and their meaning:
{taxonomy}

Rules:
- A chunk can have multiple tags if content genuinely overlaps
- is_heading=true chunks should inherit tags from their content, not just their title
- Non-relevant chunks get an empty tags list and relevant=false
- reason: one sentence max, only for relevant chunks, null otherwise

Return ONLY a valid JSON array. No markdown, no preamble. Each element must have exactly:
  chunk_index, page, is_heading, text, relevant, tags, reason
""".format(
    taxonomy="\n".join(f"  {k}: {v}" for k, v in TAXONOMY.items())
)
```

---

## `src/agents/schemas.py` — Add `TaggedChunk`

```python
class TaggedChunk(BaseModel):
    chunk_index: int
    page: int
    is_heading: bool
    text: str
    relevant: bool
    tags: list[str]
    reason: str | None
```

---

## `src/agents/tagging_agent.py`

```python
"""Tagging agent — applies security/governance tags to document chunks."""
from __future__ import annotations

import json

import anthropic

from src.agents.prompts.tagging import SYSTEM_PROMPT
from src.agents.schemas import TaggedChunk
from src.utils.helpers import strip_code_fences


class TaggingAgent:
    """Tags document chunks with security taxonomy labels.

    Args:
        client: Async Anthropic client.
        model: Claude model ID. Defaults to claude-sonnet-4-6.
        batch_size: Chunks per API call (15 keeps prompt + response well within context).
    """

    MODEL = "claude-sonnet-4-6"
    BATCH_SIZE = 15
    MAX_TOKENS = 4096

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        model: str = MODEL,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._client = client
        self._model = model
        self._batch_size = batch_size

    async def tag(self, chunks: list[dict]) -> list[TaggedChunk]:
        """Tag all chunks. Processes in batches to stay within context limits.

        Args:
            chunks: Output of clean_and_chunk() — chunk_index, page,
                    is_heading, char_count, text.

        Returns:
            List of TaggedChunk with relevant, tags, reason added.
        """
        tagged: list[TaggedChunk] = []

        for start in range(0, len(chunks), self._batch_size):
            batch = chunks[start : start + self._batch_size]
            batch_tagged = await self._tag_batch(batch)
            tagged.extend(batch_tagged)

        return tagged

    async def _tag_batch(self, batch: list[dict]) -> list[TaggedChunk]:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self.MAX_TOKENS,
            temperature=0.0,      # deterministic output per project convention
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": json.dumps(batch, ensure_ascii=False, indent=2),
            }],
        )
        raw = strip_code_fences(response.content[0].text)
        return [TaggedChunk.model_validate(item) for item in json.loads(raw)]
```

---

## `src/handlers/tag.py`

### EventBridge trigger event schema

```python
class TagHandlerEvent(BaseModel):
    detail: DocumentParsedDetail
```

### Handler flow

```python
async def _handler(event: dict, context: object) -> dict:
    # 1. Parse + validate EventBridge event
    parsed_detail = DocumentParsedDetail.model_validate(event["detail"])
    doc_id = parsed_detail.docId
    content_hash = parsed_detail.contentHash

    redis = await get_redis()
    eb = EventBridgePublisher()

    # 2. Cache check — skip Claude if already tagged
    tagged_key = key_tagged(content_hash)
    cached = await redis_get_json(redis, tagged_key)

    if cached is None:
        # 3. Load chunks from Redis
        chunks = await redis_get_json(redis, key_chunks(content_hash))
        if chunks is None:
            raise RuntimeError(f"Chunks cache miss for {content_hash} — Stage 3 may not have completed")

        # 4. Run tagging agent
        client = anthropic.AsyncAnthropic()
        agent = TaggingAgent(client)
        tagged_chunks = await agent.tag(chunks)

        # 5. Serialise and cache
        tagged_data = [c.model_dump() for c in tagged_chunks]
        await redis_set_json(redis, tagged_key, tagged_data, TTL_TAGGED)
    else:
        tagged_data = cached

    # 6. Publish DocumentTagged
    await eb.publish(
        detail_type="DocumentTagged",
        detail=DocumentTaggedDetail(
            docId=doc_id,
            taggedCacheKey=tagged_key,
            contentHash=content_hash,
        ).model_dump(),
    )

    return {"statusCode": 200}
```

---

## CloudWatch metric

Emit `TaggingDuration` and `TaggedChunkCount` after a successful (non-cached) tagging run.

---

## Verification

```bash
python -c "from src.agents.tagging_agent import TaggingAgent"
python -c "from src.agents.prompts.tagging import SYSTEM_PROMPT, TAXONOMY"
python -c "from src.handlers.tag import lambda_handler"
python -m pytest tests/test_tagging_agent.py -v
ruff check src/agents/tagging_agent.py src/handlers/tag.py
mypy src/agents/tagging_agent.py src/handlers/tag.py
```

---

## Acceptance Criteria

- [ ] `TAXONOMY` and `SYSTEM_PROMPT` in `src/agents/prompts/tagging.py`
- [ ] `TaggedChunk` Pydantic model in `src/agents/schemas.py`
- [ ] `TaggingAgent.tag()` processes chunks in batches of 15, `temperature=0.0`
- [ ] `TaggingAgent` uses `strip_code_fences()` from `src/utils/helpers.py`
- [ ] Tag Lambda reads chunks from Redis, caches tagged output, publishes `DocumentTagged`
- [ ] Cache hit skips the Claude call entirely
- [ ] Unit tests: mock the Anthropic client, assert `TaggedChunk` structure
- [ ] `ruff check .` and `mypy src/` pass
