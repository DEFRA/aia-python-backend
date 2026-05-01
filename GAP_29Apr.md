# Agent Pipeline — Gap Analysis
**Date:** 2026-04-29  
**Branch:** agent-uplift  
**Reference:** `app/agents/evaluation/plans/aia-architecture-brainstorm.md`

---

## 1. Well-Aligned ✅

| Area | Brainstorm | Code |
|---|---|---|
| SQS dispatch | Single `aia-tasks` queue, internal dispatch by `agentType` | `agent.py` — `AGENT_REGISTRY = {"security": SecurityAgent, "governance": GovernanceAgent}` |
| Status queue | Agent publishes `AgentStatusMessage` to `aia-status` | `agent.py` publishes to SQS status queue on success and failure |
| Inline-or-S3 payload | `fileContent=null` + `s3Key` fallback for oversized content | `payload_offload.py` — 240 KB threshold |
| LLM determinism | `temperature=0.0` | Both agents: `temperature: 0.0` in `config.yaml` |
| CloudWatch metrics | Per-stage observability | `ParseDuration`, `TaggingDuration`, `AgentDuration`, `AgentSuccess`, `AgentFailure` |
| Config precedence | env > yaml > code defaults | `YamlSettingsSource` in `src/config.py` — three-tier precedence |
| Error isolation | Failures → status queue; no Lambda retry loop | `agent.py` catches all exceptions → publishes `status="failed"` — never re-raises |
| SQS long polling | 20s recommended | `WaitTimeSeconds=20` in handlers |

---

## 2. Structural Divergence (deliberate architectural shifts)

| Area | Brainstorm | Code | Notes |
|---|---|---|---|
| **Compute model** | ECS Fargate — one Relay Service container polling SQS | AWS Lambda event-driven pipeline | Lambda replaces the ECS Agent POD |
| **Stage choreography** | Orchestrator extracts text → embeds in `TaskMessage` → agent processes full document | 4 Lambda stages: Parse → Tag → Extract Sections → Agent via EventBridge | Two preprocessing stages added |
| **Tagging** | Not planned — agents receive full document text | Stage 4 `TaggingAgent` classifies chunks with taxonomy tags | Net-new addition |
| **Section extraction** | Agents filter relevant content themselves | Stage 5 `extract_sections.py` pre-filters chunks per agent via `agent_tag_map` | Agents now receive pre-filtered documents |
| **Fan-in / compile** | `DeterministicSummary.generate()` inside FastAPI Orchestrator | `compile.py` Lambda deleted; FastAPI `MarkdownSummaryGenerator` handles it | Lambda pipeline has no compile stage — results flow to SQS status queue; FastAPI Orchestrator fans in |

---

## 3. Gaps — Planned but Not Implemented ❌

### 3.1 Agent Roster
| Brainstorm | Code | Gap |
|---|---|---|
| 5 agents: `security`, `data`, `risk`, `ea`, `solution` | 2 agents: `security`, `governance` | `data`, `risk`, `ea`, `solution` removed; `governance` is new and was not in brainstorm |

**Impact:** High — core scope change. Agent coverage reduced from 5 specialist domains to 2.

---

### 3.2 Bedrock Integration
| Brainstorm | Code | Gap |
|---|---|---|
| IAM auth, no API key, VPC endpoint, cross-region inference profiles | Anthropic API directly (`ANTHROPIC_API_KEY` env var); `main.py` uses `AsyncAnthropicBedrock` locally but `agent.py` uses `AsyncAnthropic` | Agents are not Bedrock-backed in any deployed path |

**Impact:** High — production deployment blocker. IAM-based auth and VPC endpoint are required for AWS-native deployment.

---

### 3.3 Assessment Questions from Database
| Brainstorm | Code | Gap |
|---|---|---|
| RDS table: `agentType + templateType` → questions + `category_url` at runtime | File-based JSON (`load_assessment_from_file`); `questions_repo.py` stub raises `NotImplementedError` | Questions are hardcoded in a JSON file on disk |

**Impact:** Medium — questions cannot be managed at runtime without redeployment.

---

### 3.4 Redis State
| Brainstorm | Code | Gap |
|---|---|---|
| In-memory for POC → Redis/ElastiCache for multi-instance production | Not implemented anywhere | FastAPI Orchestrator uses in-memory `SessionStore`; no Redis integration |

**Impact:** Medium — required before horizontal scaling of the FastAPI Orchestrator.

---

### 3.5 PDF Support in CoreBackend
| Brainstorm | Code | Gap |
|---|---|---|
| Both PDF and DOCX accepted | Lambda `document_parser.py` handles both; CoreBackend `IngestorService` is DOCX-only | PDF uploads via CoreBackend will fail at text extraction |

**Impact:** Medium — Lambda pipeline supports PDF; the CoreBackend HTTP path does not.

---

### 3.6 Health Endpoint for Relay Service
| Brainstorm | Code | Gap |
|---|---|---|
| Lightweight HTTP on port 8080 for ECS health check | No health endpoint in Lambda pipeline (Lambda does not need one) | Only relevant if compute model is moved back to ECS |

**Impact:** Low — not needed for Lambda.

---

## 4. Schema Mismatches ⚠️

| Field | Brainstorm | Code | Impact |
|---|---|---|---|
| Document ID key | `documentId` | `docId` (Lambda pipeline) | **High** — FastAPI Orchestrator `StatusMessage` uses `document_id`; Lambda `AgentStatusMessage` uses `docId`; fan-in will not correlate correctly |
| Status values | `SUCCESS` / `ERROR` | `completed` / `failed` | Medium — Orchestrator must handle both or one side must align |
| Assessment rating case | `GREEN` / `AMBER` / `RED` | `Green` / `Amber` / `Red` | Low — cosmetic; consistent within codebase |
| Assessment row fields | `{questionId, rating, comments, section}` | `{Question, Rating, Comments, Reference: {text, url}}` | Low — schema diverged; only matters when consumers compare against brainstorm contract |
| S3 payload threshold | ~200 KB | 240 KB (`sqs_inline_limit`) | Low — functional; value just differs |

---

## 5. Integration Gap — Lambda Pipeline ↔ FastAPI Orchestrator

This is the largest systemic gap. The brainstorm describes one integrated end-to-end flow. The current codebase has **two separate pipelines** that partially overlap:

```
CoreBackend
    │
    └─► FastAPI Orchestrator ──► aia-tasks SQS ──► polls aia-status SQS (StatusMessage.document_id)
                                        │
                                        ▼
                           Lambda Pipeline:
                           parse.py → tag.py → extract_sections.py → agent.py
                                                                          │
                                                                          └─► aia-status SQS (AgentStatusMessage.docId)
```

**Problems:**

1. **Field name clash** — Lambda publishes `AgentStatusMessage` with `docId`; FastAPI Orchestrator expects `StatusMessage` with `document_id`. Fan-in will silently fail to match.
2. **Text extraction duplication** — FastAPI Orchestrator calls `IngestorService.extract_text_from_docx()` and embeds text in `TaskMessage`. Lambda Stage 3 `parse.py` also downloads from S3 and extracts text independently. Both pipelines do the same work.
3. **No wiring** — It is not clear which pipeline is active in production. The FastAPI Orchestrator publishes to `aia-tasks` with `TaskMessage` (containing `file_content`). Lambda `parse.py` expects `SqsRecordBody(docId, s3Key)` — a different schema. They cannot both consume the same queue without schema alignment.

---

## 6. Summary — Prioritised Action List

| Priority | Item | File(s) |
|---|---|---|
| 🔴 High | Align `docId` vs `document_id` across Lambda `AgentStatusMessage` and FastAPI `StatusMessage` | `app/agents/evaluation/src/agents/schemas.py`, `app/models/status_message.py` |
| 🔴 High | Decide and document which pipeline is active — Lambda or FastAPI Orchestrator — and wire the integration | `app/orchestrator/main.py`, `app/agents/evaluation/src/handlers/agent.py` |
| 🔴 High | Define agent roster — confirm whether governance replaces data/risk/ea/solution or supplements them | `app/agents/evaluation/config.yaml`, `src/agents/` |
| 🟡 Medium | Implement Bedrock client in `agent.py` (replace `AsyncAnthropic` with `AsyncAnthropicBedrock`) | `app/agents/evaluation/src/handlers/agent.py`, `src/agents/*.py` |
| 🟡 Medium | Implement `questions_repo.py` — PostgreSQL-backed questions loading | `app/agents/evaluation/src/db/questions_repo.py` |
| 🟡 Medium | Add PDF support to CoreBackend `IngestorService` | `app/services/ingestor_service.py` |
| 🟢 Low | Redis integration for FastAPI Orchestrator `SessionStore` | `app/orchestrator/session.py` |
| 🟢 Low | Align status value strings (`SUCCESS`/`ERROR` vs `completed`/`failed`) | `app/agents/evaluation/src/agents/schemas.py` |
