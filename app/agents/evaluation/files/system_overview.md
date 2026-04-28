# Defra Security Assessment Tool — System Overview

> A guide to how the codebase works, written so both engineers and non-engineers can follow along.
> The first three sections are written in plain English; the remaining sections progressively add technical detail.

---

## Table of Contents

1. [What this tool does (in plain English)](#1-what-this-tool-does-in-plain-english)
2. [The journey of a document](#2-the-journey-of-a-document)
3. [Why it is built this way](#3-why-it-is-built-this-way)
4. [The four-Lambda evaluation pipeline](#4-the-four-lambda-evaluation-pipeline)
5. [The two specialist agents](#5-the-two-specialist-agents)
6. [The web layer (FastAPI)](#6-the-web-layer-fastapi)
7. [Shared infrastructure (PostgreSQL, S3, EventBridge, SQS)](#7-shared-infrastructure)
8. [Code organisation](#8-code-organisation)
9. [Engineering conventions](#9-engineering-conventions)
10. [Tech stack at a glance](#10-tech-stack-at-a-glance)
11. [Local development](#11-local-development)
12. [Glossary](#12-glossary)

---

## 1. What this tool does (in plain English)

Defra teams need to check that documents — security policies, system designs, data-handling procedures — meet a long list of rules. Doing this by hand is slow and inconsistent.

**This tool reads the document for you and produces a colour-coded assessment.**

- **Green** — the document clearly addresses the requirement.
- **Amber** — the document partly addresses it, or is ambiguous.
- **Red** — the document does not address it, or contradicts it.

A large language model (LLM) does the actual reading and judging, and the document is broken down and routed to **two specialist reviewers** — one for security controls, one for information governance — whose answers are emitted to a **terminal SQS Status queue**. A separate front-end (out of scope here) reads from that queue and presents results to the user.

---

## 2. The journey of a document

Imagine a user uploads a 30-page security policy through the web interface. Here is what happens, end to end:

```
   ┌────────────┐
   │   USER     │  Uploads "security_policy.pdf"
   └─────┬──────┘
         │
         ▼
   ┌─────────────────────────────────┐
   │  WEB LAYER (FastAPI)            │  /api/upload
   │  • Validates the user           │
   │  • Stores file metadata in DB   │
   │  • Uploads file to S3           │
   └─────┬───────────────────────────┘
         │
         ▼
   ┌─────────────────────────────────┐
   │  ENTRY POINT                    │
   │  S3 → EventBridge → SQS Tasks   │   message body: {docId, s3Key}
   └─────┬───────────────────────────┘
         │
         ▼
   ┌─────────────────────────────────┐
   │  STAGE 3  PARSE                 │  Reads PDF / DOCX, breaks into chunks
   └─────┬───────────────────────────┘
         │  DocumentParsed (chunks inline or s3Key envelope)
         ▼
   ┌─────────────────────────────────┐
   │  STAGE 4  TAG                   │  AI labels each chunk with topics
   │                                 │  ("authentication", "encryption", …)
   └─────┬───────────────────────────┘
         │  DocumentTagged (tagged chunks inline or s3Key envelope)
         ▼
   ┌─────────────────────────────────┐
   │  STAGE 5  EXTRACT SECTIONS      │  Picks the right chunks for each
   │                                 │  specialist; fans out to 2 agents
   └─────┬───────────────────────────┘
         │
   ┌─────┴────────┐
   ▼              ▼
┌──────┐      ┌────────────┐
│SECUR.│      │ GOVERNANCE │   STAGE 6  (2 agents in parallel)
│agent │      │   agent    │
└───┬──┘      └─────┬──────┘
    │               │
    ▼               ▼
┌──────────────────────────┐
│   SQS Status queue       │  Terminal output: one AgentStatusMessage per agent
└──────────────────────────┘
                ▲
                │
        Front-end / downstream consumer (out of scope)
```

**The key idea:** no single program runs the whole thing. Each stage is an independent Lambda function. When it finishes, it shouts "I'm done!" to a router (EventBridge), which wakes up the next stage. This is called **choreography** — every stage knows its own dance step, but no central conductor exists.

State that has to flow between stages — parsed chunks, tagged chunks — rides along with the EventBridge event itself. If the payload exceeds the SQS / EventBridge inline limit (240 KB) it is offloaded to S3 and the event carries an `s3Key` reference instead. There is no shared cache or database between stages.

---

## 3. Why it is built this way

Several constraints shaped the architecture:

| Constraint | Design response |
|---|---|
| **Reviews must be auditable** — the same document should produce the same report every time. | The LLM is called with `temperature=0.0`, so the model always picks its most likely answer. Output is deterministic. |
| **Documents can be large; AI calls can fail or time out.** | Each stage is small, focused, and independently retryable. If a stage fails, SQS / Lambda redelivery handles the retry. |
| **Different reviewers ask different questions.** | Two specialist agents run in parallel, each with its own prompt and its own checklist. |
| **Cost matters; servers should not idle.** | Everything runs on AWS Lambda, which only bills for milliseconds of actual execution. |
| **A failure midway must not lose the document.** | The user's upload sits in an SQS queue; the Lambda event-source mapping deletes the message only on successful invocation. Failures trigger redelivery up to the maxReceiveCount, then route to a DLQ. |

---

## 4. The four-Lambda evaluation pipeline

This section walks through each stage with concrete detail. **The evaluation pipeline itself consists of four AWS Lambda functions** — Parse, Tag, Extract Sections, and Agent — whose code lives in [app/agents/evaluation/src/handlers/](app/agents/evaluation/src/handlers/). Stages 1 and 2 below are **not Lambdas**: Stage 1 is the FastAPI web layer, and Stage 2 is pure AWS routing (S3 → EventBridge → SQS) with no Lambda in the path. They are described here for context.

### Stage 1 — Upload (web layer, *not a Lambda*)

**Where:** [app/api/documents.py](app/api/documents.py) (HTTP routes), [app/services/upload_service.py](app/services/upload_service.py), [app/services/ingestor_service.py](app/services/ingestor_service.py).
**Trigger:** the document-upload endpoint exposed by the documents router.

The user uploads a file. The upload service validates the request, writes a row to PostgreSQL, and uploads the binary to S3 under `in_progress/<docId>`. The `IngestorService` is then responsible for placing the corresponding `{docId, s3Key}` message on the SQS Tasks queue.

### Stage 2 — Detection (*no Lambda — pure AWS routing*)

S3 emits an "Object Created" event natively to EventBridge. An EventBridge rule routes the event to the **SQS Tasks queue** (FIFO, with `MessageGroupId = "pipeline"` for earliest-first ordering). A Dead-Letter Queue catches messages that fail too many times.

### Stage 3 — Parse

**Handler:** [app/agents/evaluation/src/handlers/parse.py](app/agents/evaluation/src/handlers/parse.py)
**Reads:** SQS message containing `docId` and `s3Key`.
**Publishes:** `DocumentParsed` event with a `payload` envelope (inline JSON or S3 key).

The Lambda downloads the file bytes from S3 and parses them:

- **PDF** — [src/utils/document_parser.py](app/agents/evaluation/src/utils/document_parser.py) uses PyMuPDF (`fitz`) to extract text blocks, then `clean_and_chunk()` merges blocks into ~1,500-character chunks and detects headings via font-size heuristics.
- **DOCX** — `python-docx` iterates paragraphs; the paragraph's style name determines `is_heading`.
- **Scanned PDFs** raise `ScannedPdfError`.

The chunks list is wrapped in a payload envelope by [src/utils/payload_offload.py](app/agents/evaluation/src/utils/payload_offload.py) — under 240 KB they go inline, otherwise to `s3://{bucket}/state/{docId}/chunks.json` with the event carrying just the S3 key.

### Stage 4 — Tag

**Handler:** [app/agents/evaluation/src/handlers/tag.py](app/agents/evaluation/src/handlers/tag.py)
**Agent class:** [TaggingAgent](app/agents/evaluation/src/agents/tagging_agent.py) using prompt [src/agents/prompts/tagging.py](app/agents/evaluation/src/agents/prompts/tagging.py).
**Reads:** the `DocumentParsed` event, resolving the inline-or-S3 payload.
**Publishes:** `DocumentTagged` event with another payload envelope (the tagged chunks).

Each chunk is sent to the LLM in batches (default 15 at a time) along with a fixed taxonomy. The LLM returns, for each chunk, whether it is relevant and which topic tags apply. The schema is `TaggedChunk(chunk_index, page, is_heading, text, relevant, tags, reason)` from [src/agents/schemas.py](app/agents/evaluation/src/agents/schemas.py).

### Stage 5 — Extract Sections

**Handler:** [app/agents/evaluation/src/handlers/extract_sections.py](app/agents/evaluation/src/handlers/extract_sections.py)
**Reads:** the `DocumentTagged` event, resolving the inline-or-S3 payload.
**Loads:** the agent's checklist questions on every invocation via [`load_assessment_from_file`](app/agents/evaluation/src/db/assessment_loader.py) — a JSON file in the configured data folder. The Postgres-backed equivalent ([`fetch_assessment_by_category`](app/agents/evaluation/src/db/questions_repo.py)) is intentionally a `NotImplementedError` placeholder until the assessment schema lands.
**Writes:** two SQS messages (one per surviving agent) to the **Tasks** queue.

For each agent type (`security`, `governance`):

1. Filter the tagged chunks to those where `relevant=True` and at least one tag matches that agent's tag set (mapping defined in `config.yaml` under `pipeline.agent_tag_map`).
2. Re-attach the nearest preceding heading so each section has context.
3. Load the agent's checklist questions.
4. Build an `AgentTaskBody`. If under 240 KB, the document text is inlined in the SQS body. Over 240 KB, it is offloaded to `s3://{bucket}/payloads/{docId}/{agentType}.json` and the SQS body carries only `s3PayloadKey`.

This is the **fan-out** point: from this stage onwards, two independent agent invocations run in parallel. The hand-off is via SQS (back onto the same Tasks queue), **not** EventBridge.

### Stage 6 — Specialist agent (×2 in parallel) — terminal stage

**Handler:** [app/agents/evaluation/src/handlers/agent.py](app/agents/evaluation/src/handlers/agent.py) (one Lambda, dispatches by `agentType`).
**Trigger:** SQS Tasks queue (batch size 1, so each invocation handles one agent).
**Publishes:** an `AgentStatusMessage` to the **SQS Status queue**.

The handler picks the agent class and config from a registry:

```python
AGENT_REGISTRY = {
    "security":   SecurityAgent,
    "governance": GovernanceAgent,
}
```

It instantiates the agent, calls `await agent.assess(document, questions, category_url)`, and posts the result to the Status queue. **The Status queue is the terminal output of this pipeline** — consumption is owned by an external front-end / downstream service (out of scope for this codebase).

On exception, the handler still publishes a `status="failed"` message with `errorMessage` so the consumer can surface the failure.

---

## 5. The two specialist agents

All agent classes live in [app/agents/evaluation/src/agents/](app/agents/evaluation/src/agents/) and follow an identical shape:

```python
class XAgent:
    def __init__(self, client: BedrockClient, agent_config: XAgentConfig) -> None:
        # ``client`` is an async LLM client backed by AWS Bedrock.
        self.client = client
        self.agent_config = agent_config

    async def assess(
        self,
        document: str,
        questions: list[QuestionItem],
        category_url: str,
    ) -> AgentResult:
        # 1. Format questions into a numbered block
        # 2. Invoke the LLM with the agent's system + user prompt
        # 3. Parse the JSON response, validate via Pydantic
        # 4. Return AgentResult
```

Each agent has its own prompt file under [src/agents/prompts/](app/agents/evaluation/src/agents/prompts/) — `security.py`, `governance.py` — containing a system prompt with role + instructions + Green/Amber/Red few-shot examples, and a user template that wraps the document and questions in XML tags.

The output schema (in [src/agents/schemas.py](app/agents/evaluation/src/agents/schemas.py)) is the same across both agents:

```python
class AssessmentRow(BaseModel):
    Question: str
    Rating: Literal["Green", "Amber", "Red"]
    Comments: str
    Reference: Reference

class FinalSummary(BaseModel):
    Interpretation: str
    Overall_Comments: str

class AgentResult(BaseModel):
    assessments: list[AssessmentRow]
    metadata: LLMResponseMeta
    final_summary: FinalSummary | None
```

| Agent | Concern | Example questions |
|---|---|---|
| **SecurityAgent** | Authentication, encryption, access control, secrets handling | "Is MFA required for all admin access?" |
| **GovernanceAgent** | Data protection, retention, ROPA, lawful basis, IG governance | "Is the lawful basis for processing documented?" |

Both agents invoke the LLM with `temperature=0.0` for deterministic, auditable output.

---

## 6. The web layer (FastAPI)

The HTTP-facing application lives at the top of [app/](app/):

| Path | Purpose |
|---|---|
| [app/main.py](app/main.py) | FastAPI app factory, lifespan (DB pool init/shutdown) |
| [app/api/documents.py](app/api/documents.py) | Document-upload, history, and result endpoints |
| [app/api/health.py](app/api/health.py) | Health endpoint |
| [app/api/users.py](app/api/users.py) | User-management endpoints |
| [app/services/](app/services/) | `upload_service`, `ingestor_service`, `orchestrator_service`, `s3_service`, `sqs_service` |
| [app/repositories/](app/repositories/) | Data-access layer (`document_repository`, `user_repository`) |
| [app/models/](app/models/) | Pydantic request/response schemas |
| [app/core/](app/core/) | App config, dependency-injection wiring, JWT auth |

Authentication uses JWTs; the user ID is extracted from the token and used for ownership checks.

---

## 7. Shared infrastructure

### EventBridge — the choreography router

A single custom event bus (`defra-pipeline`) carries inter-stage events. Publishing helper: [src/utils/eventbridge.py](app/agents/evaluation/src/utils/eventbridge.py) (`EventBridgePublisher`).

The live path emits exactly **two events**, each backed by a typed Pydantic detail model in [schemas.py](app/agents/evaluation/src/agents/schemas.py):

| Detail-type | From → To | Carries |
|---|---|---|
| `DocumentParsed` | Stage 3 → 4 | `docId`, `payload` (inline-or-S3 envelope of chunks) |
| `DocumentTagged` | Stage 4 → 5 | `docId`, `payload` (inline-or-S3 envelope of tagged chunks) |

Stage 5 → 6 is hand-off via SQS (the same Tasks queue), not EventBridge. Stage 6 publishes only to SQS Status.

Two further detail models — `SectionsReadyDetail` and `AgentCompleteDetail` — are defined in `schemas.py` but **no handler currently publishes them**. They are reserved for future observability hooks and should not be relied on in the current pipeline.

### Inline-or-S3 payload offload

[src/utils/payload_offload.py](app/agents/evaluation/src/utils/payload_offload.py) wraps cross-stage data in a `{"inline": "<json>"}` envelope below 240 KB, or writes it to `s3://{bucket}/state/{docId}/{stage}.json` and returns `{"s3Key": "..."}`. The receiving handler calls `resolve_payload()` which returns the underlying bytes regardless of which form was used. There is **no Redis** in the pipeline.

### SQS queues

| Queue | Producer | Consumer | Notes |
|---|---|---|---|
| **Tasks** (FIFO) | Web upload + Stage 5 fan-out | Stage 3 / Stage 6 | Lambda event-source mapping deletes messages automatically on successful invocation |
| **Status** | Stage 6 | External consumer (out of scope) | Terminal output: one `AgentStatusMessage` per `(docId, agentType)` |
| **DLQ** | All queues | Operators | Catches poison messages |

### PostgreSQL — durable storage

A documents table managed by [app/repositories/](app/repositories/) tracks upload metadata and pipeline lifecycle.

Checklist data has a planned home in `assessment_categories` / `assessment_questions` tables, but the live path **does not currently read from Postgres**: [`load_assessment_from_file`](app/agents/evaluation/src/db/assessment_loader.py) reads a single JSON file from the data folder (filename configured by `local_runner.assessment_filename`). The Postgres-backed entry point [`fetch_assessment_by_category`](app/agents/evaluation/src/db/questions_repo.py) is a `NotImplementedError` placeholder pending the schema migration.

Driver: `asyncpg` for async paths, with `psycopg2-binary` available as a sync fallback.

### S3 — file lifecycle

Three logical prefixes:

- `in_progress/{docId}.{ext}` — the file while the pipeline is running
- `completed/{docId}.{ext}` — successful completion
- `error/{docId}.{ext}` — terminal failure
- `state/{docId}/{stage}.json` — payload offloads from the inline-or-S3 envelope

### CloudWatch — observability

Every stage emits at least one custom metric (e.g. `ParseDuration`, `TaggingDuration`, `AgentDuration` with a dimension for `agentType`). Standard Lambda metrics are augmented with an alarm on **DLQ depth > 0**.

### Terminal output

The pipeline ends at the SQS Status queue. This codebase publishes one `AgentStatusMessage` per `(docId, agentType)` and stops. Persisting results, building a compiled report, or moving the document to a "completed" S3 prefix are responsibilities of a separate front-end / downstream consumer.

---

## 8. Code organisation

```
aia-python-backend/
├── CLAUDE.md
├── CODING_GUIDE.md
├── README.md
├── compose.yml
├── requirements.txt
│
├── app/
│   ├── main.py
│   ├── api/
│   ├── services/
│   ├── repositories/
│   ├── models/
│   ├── core/
│   ├── orchestrator/
│   ├── utils/
│   │
│   └── agents/
│       ├── evaluation/             # ── THE FOUR-LAMBDA EVALUATION PIPELINE ──
│       │   ├── main.py             # Local dev entry point (bypasses Lambda; mocks SQS)
│       │   ├── README.md           # Local-run instructions and local-vs-prod table
│       │   ├── requirements.txt
│       │   ├── config.yaml
│       │   ├── data/               # Sample input doc + assessment input JSON
│       │   ├── files/
│       │   ├── plans/
│       │   ├── tests/
│       │   └── src/
│       │       ├── config.py       # Per-agent / pipeline / local_runner settings
│       │       ├── agents/
│       │       │   ├── security_agent.py
│       │       │   ├── governance_agent.py
│       │       │   ├── tagging_agent.py
│       │       │   ├── schemas.py            # PayloadEnvelope + every cross-boundary model
│       │       │   └── prompts/
│       │       ├── handlers/                 # The four Lambdas
│       │       │   ├── parse.py
│       │       │   ├── tag.py
│       │       │   ├── extract_sections.py
│       │       │   └── agent.py
│       │       ├── db/
│       │       │   ├── assessment_loader.py  # File-based loader (live path)
│       │       │   └── questions_repo.py     # Postgres equivalent (placeholder, raises NotImplementedError)
│       │       └── utils/
│       │           ├── document_parser.py
│       │           ├── eventbridge.py
│       │           ├── exceptions.py
│       │           ├── helpers.py
│       │           └── payload_offload.py    # Inline-or-S3 envelope helper
│       │
│       └── questiongen/
│           └── src/lambda_function.py
│
└── tests/
```

---

## 9. Engineering conventions

These are enforced by [CODING_GUIDE.md](CODING_GUIDE.md).

### Pydantic at every boundary

The single most important rule: no plain `dict` crosses a module boundary. Every Lambda event, every EventBridge publish, every LLM response is validated through a Pydantic v2 model.

```python
# At the top of every handler
detail = DocumentParsedDetail.model_validate(event["detail"])
```

### Lambda handler shape

Every handler follows the same skeleton:

```python
def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return asyncio.run(_handler(event, context))

async def _handler(event, context):
    detail = SomeDetail.model_validate(event["detail"])
    # orchestration only — no business logic here
    return {"statusCode": 200}
```

Business logic lives in `src/agents/` or `src/utils/`; handlers do nothing but parse, dispatch, and respond.

### Async-first

All LLM calls go through AWS Bedrock via an async client; PostgreSQL uses `asyncpg`. Any blocking AWS SDK calls (sync `boto3`) are wrapped in `loop.run_in_executor()`.

### Style and tooling

- 100-char line length, double quotes, trailing commas
- `X | None`, never `Optional[X]`
- Full type annotations; mypy strict
- Run before committing: `ruff check . && ruff format . && mypy app/agents/evaluation/src/`

### Configuration precedence

1. Constructor kwargs (test injection)
2. Environment variables
3. `.env` file (loaded by `python-dotenv` locally)
4. `config.yaml` (parsed by a custom `YamlSettingsSource`)
5. Pydantic field defaults

Secrets (`DB_PASSWORD`, `ANTHROPIC_API_KEY`) **must** come from env or AWS Secrets Manager — never hardcoded.

---

## 10. Tech stack at a glance

**Language:** Python 3.11+ (3.13 recommended), async-first.

**Core libraries (evaluation pipeline):**

| Library | Role |
|---|---|
| `boto3` | AWS SDK — S3, SQS, EventBridge, CloudWatch, Bedrock |
| AWS Bedrock SDK (async client) | LLM access via AWS Bedrock |
| `pydantic` 2.12 + `pydantic-settings` | Schemas + typed config |
| `asyncpg` | Async PostgreSQL (web/document repo only — not currently used by the evaluation handlers) |
| `PyMuPDF` (`fitz`) | PDF text extraction |
| `python-docx` | DOCX parsing |
| `PyYAML` | `config.yaml` loading |

`reportlab` is still listed in `requirements.txt` and the unused `pdf_creator*.py` files in `src/utils/` reference it, but the live pipeline does not generate PDFs — terminal output is JSON on the SQS Status queue.

**Web layer:** `fastapi`, `uvicorn`, `pyjwt`, `python-multipart`, `httpx`, `aiobotocore`.

**AWS services:** S3, EventBridge (`defra-pipeline` bus, two rules), Lambda (four — Parse / Tag / Extract Sections / Agent), SQS (Tasks queue + Status queue + DLQ), RDS PostgreSQL (documents table; assessment-questions schema is TBC), CloudWatch (metrics, alarms, logs), Bedrock (managed LLM access), IAM.

---

## 11. Local development

A full end-to-end run can be performed without any AWS infrastructure (no Lambda, no EventBridge, no SQS, no S3 — only Bedrock for the LLM calls) via [`main.py`](../main.py):

```bash
python app/agents/evaluation/main.py
```

The runner mocks both ends of the SQS pipeline: the Tasks input is built as a Python `SqsRecordBody` and fed straight into the parse stage; the Status output is written to `data/pipeline_output_<docId>.json`. All defaults — data folder, assessment filename, output template, agent-type display keys — come from `LocalRunnerConfig` in `config.yaml` under `local_runner:`.

For setup, CLI args, configuration overrides, and the full local-vs-production breakdown, see the evaluation [README.md](../README.md).

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **Choreography** | Architectural pattern where each component reacts to events independently, with no central conductor. |
| **EventBridge** | AWS event bus. Routes events from publishers to subscribers based on rules. |
| **Fan-out** | One stage triggers many parallel downstream stages (Stage 5 → two agents). |
| **FIFO queue** | A SQS queue that preserves message order. |
| **Lambda** | AWS's serverless function service. |
| **DLQ (Dead Letter Queue)** | A backup queue for messages that have failed processing too many times. |
| **Receipt handle** | A token returned when a SQS message is read; used to delete the message. With Lambda event-source mappings, deletion happens automatically on successful invocation. |
| **Specialist agent** | One of the two LLM-powered reviewers (Security, Governance). Each has its own prompt and checklist. |
| **temperature** | A parameter controlling AI randomness. `0.0` means "always pick the most likely answer". |
| **Pydantic** | A Python library for typed, validated data models. Used at every boundary in this codebase. |
| **Payload envelope** | A small JSON object — either `{"inline": "<json>"}` or `{"s3Key": "..."}` — that lets a stage carry data either inline or by reference, depending on size. |

---

*For the authoritative AWS architecture spec, see [aws_event_driven_orchestration.md](aws_event_driven_orchestration.md). For coding rules, see [CODING_GUIDE.md](../../../../CODING_GUIDE.md). For local-run instructions, see the evaluation [README.md](../README.md).*
