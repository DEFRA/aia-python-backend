# Defra Security Assessment Tool

An AI-powered compliance checker that evaluates documents against security and compliance checklists using a multi-agent Claude pipeline deployed on AWS. Produces colour-coded PDF reports (Green / Amber / Red).

---

## Architecture

The tool runs as a 9-stage event-driven pipeline on AWS. For the full architecture see [aws_event_driven_orchestration.md](./app/agents/evaluation/files/aws_event_driven_orchestration.md).

**High-level flow:**
```
S3 upload → EventBridge → SQS FIFO → Parse → Tag → Extract Sections
  → 5 Parallel Specialist Agents → Compile → Persist + S3 Move → Notify
```

**Key components:**

| Component | Role |
|-----------|------|
| EventBridge (`defra-pipeline`) | Choreographs all stage transitions — no direct Lambda-to-Lambda calls |
| SQS FIFO | Durable, ordered entry point — message stays invisible until Stage 9 completes |
| Redis (ElastiCache) | Shared pipeline state between all stages (chunks, tagged output, sections, results, counters) |
| 5 Specialist Agents | `security`, `data`, `risk`, `ea`, `solution` — run in parallel, each calls Claude independently |
| RDS PostgreSQL | Stores checklist questions (by agent type) and final assessment results |
| CloudWatch | Observability for every stage transition |

**Source layout:**
```
app/
  agents/
    evaluation/
      main.py                      # Local entry point for development/testing — not the Lambda handler
      scripts/
        docling_pdf_parser.py      # PDF text extraction utility
      files/
        aws_event_driven_orchestration.md  # Full AWS pipeline architecture (authoritative reference)
        security_policy.md                 # Sample document for local testing
      plans/                       # Implementation task plans (01–11 + code review)
      src/
        agents/
          security_agent.py        # Agent base pattern — all agents follow this structure
          tagging_agent.py
          data_agent.py
          ea_agent.py
          risk_agent.py
          solution_agent.py
          schemas.py               # Pydantic models: AssessmentRow, FinalSummary, AgentResult, LLMResponseMeta
          prompts/
            security.py            # System + user prompts (few-shot Green/Amber/Red examples)
            tagging.py
            data.py
            ea.py
            risk.py
            solution.py
        config.py                  # All agent configs and infrastructure configs (BaseSettings)
        db/
          questions_repo.py        # Async PostgreSQL — fetch_questions_by_category()
        handlers/
          parse.py                 # Stage 3 — PDF/DOCX parsing Lambda
          tag.py                   # Stage 4 — Tagging agent Lambda
          extract_sections.py      # Stage 5 — Section extraction + fan-out Lambda
          agent.py                 # Stage 6 — Specialist agent Lambda (all 5 agent types)
          compile.py               # Stage 7 — Compile results Lambda
          persist.py               # Stage 8a — Persist to PostgreSQL Lambda
          s3_move.py               # Stage 8b — Move S3 object Lambda
          notify.py                # Stage 9 — Notify + SQS delete Lambda
        utils/
          document_parser.py       # PDF/DOCX text extraction and chunking
          eventbridge.py           # publish_event() helper
          exceptions.py            # Pipeline-specific exception types
          helpers.py               # strip_code_fences(), extract_json_array()
          pdf_creator_multipage.py # ReportLab PDF builder (primary output)
          pdf_creator.py           # Single-page variant (testing only)
          redis_client.py          # Redis connection management and TTL constants
        tests/                     # Mirrors src/ structure
          agents/
          handlers/
          utils/
          test_config.py
    questiongen/
      src/
        lambda_function.py         # Question generation Lambda
  api/
    items.py
    users.py
  config.py
  main.py
  services/
    user_service.py
  utils/
    helpers.py
```

### Key design decisions

- **EventBridge choreography**: each Lambda only knows its own input event and output event — stages are fully decoupled
- **Redis as pipeline state store**: all inter-stage data flows through Redis; Lambda functions are stateless
- **Content-hash cache keys**: parsed chunks and tagged output are keyed by `sha256(file_bytes)` — resubmissions skip expensive steps automatically
- **Redis counter for fan-in**: parallel agent completion is tracked with `INCR results_count:{docId}` — avoids distributed locks
- **Async-first**: all agents use `anthropic.AsyncAnthropic`; Lambda handlers delegate to `async def` via `asyncio.run()`
- **Pydantic for all schemas**: every agent input/output, Lambda handler event, Redis read-back, EventBridge publish, and report builder input is validated through a Pydantic v2 model — no plain dicts at module boundaries; see [Pydantic Boundary Validation](./CODING_GUIDE.md#pydantic-boundary-validation) for the enforcement rules
- **Deterministic output**: `temperature=0.0` on all agents for consistent, auditable results
- **New agent types**: follow the pattern in `app/agents/evaluation/src/agents/security_agent.py` — `__init__(client, config)` + `async assess(...) -> AgentResult`
- **New prompts**: go in `app/agents/evaluation/src/agents/prompts/` as a `.py` file with named string constants

---

## Coding Conventions

See [CODING_GUIDE.md](./CODING_GUIDE.md) for full standards — including Lambda handler structure, Redis key conventions, and EventBridge event publishing.

Run before committing:
```bash
ruff check . && ruff format . && mypy app/agents/evaluation/src/
```

---

## Workflow Rules

### Never touch `venv/`
Do not read, edit, or create files inside any `venv/` directory. Treat it as a black box.

### Secrets via environment only
All credentials must come from environment variables. Locally: `.env` loaded by `python-dotenv`. In production: Lambda environment variables or AWS Secrets Manager. Required variables include `ANTHROPIC_API_KEY`, `DB_*`, and Redis connection details. Never hardcode credentials.

### Verify imports after changes
After modifying or creating any `src/` module:

```bash
python -c "from app.agents.evaluation.src.agents.security_agent import SecurityAgent"
python -c "from app.agents.evaluation.src.agents.schemas import AgentResult"
python -c "from app.agents.evaluation.src.config import SecurityAgentConfig, DatabaseConfig"
python -c "from app.agents.evaluation.src.db.questions_repo import fetch_questions_by_category"
```

---

## Running locally (development)

```bash
pip install -r app/agents/evaluation/requirements.txt
# fill in ANTHROPIC_API_KEY, DB_*, and Redis vars in your .env

# Run a security assessment against the local pipeline
python app/agents/evaluation/main.py app/agents/evaluation/files/security_policy.md report.pdf Security
```

> In production each stage runs as an independent Lambda function triggered by EventBridge. See [aws_event_driven_orchestration.md](./app/agents/evaluation/files/aws_event_driven_orchestration.md) for the full deployment architecture.
