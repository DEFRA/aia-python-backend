# Defra Evaluation Pipeline

An AI-powered compliance checker that scores documents against security and information-governance checklists using a multi-agent pipeline. Produces a colour-coded result (Green / Amber / Red) per checklist question.

For the full architecture and design rationale, see [`files/system_overview.md`](./files/system_overview.md) and [`files/aws_event_driven_orchestration.md`](./files/aws_event_driven_orchestration.md).

---

## What it does

Given a document (PDF or DOCX), the pipeline:

1. **Parses** the document into text chunks.
2. **Tags** each chunk with security / governance taxonomy labels.
3. **Extracts** the relevant sections per specialist agent.
4. **Assesses** the sections against the agent's question set (loaded from PostgreSQL).
5. **Emits** one `AgentResult` per agent via the SQS Status queue.

Two specialist agents are wired up: **Security** and **Technical**.

In production the Agent Service consumes `TaskMessage`s from the SQS Tasks queue, runs the full parse → tag → extract → assess pipeline in-process, and publishes `StatusMessage`s back to the SQS Status queue for the Orchestrator to pick up. Locally [`main.py`](./main.py) **bypasses all queue infrastructure** and runs the same business logic in-process — no AWS services are invoked except optionally Bedrock or the Anthropic API for model calls. See [Local vs production](#local-vs-production) below.

---

## Pipeline

```
Orchestrator ──▶ SQS Tasks ──▶ Agent Service ─┬─▶ Security Agent ─┐
                                               └─▶ Technical Agent ─┴─▶ SQS Status ──▶ Orchestrator
```

- Entry point: `TaskMessage` on the SQS Tasks queue (`docId`, `agentType`, `fileContent` or S3 reference, `questions`, `policyDocUrl`).
- The Agent Service runs parse → tag → extract sections → specialist agent in-process for each task.
- Terminal output: one `StatusMessage` per `(docId, agentType)` on the SQS Status queue. The Orchestrator polls this queue and writes the final `resultMd` to PostgreSQL.

---

## Layout

```
app/agents/evaluation/
├── main.py                      # Local end-to-end runner (no SQS — runs directly in-process)
├── config.yaml                  # Operational defaults (models, pipeline, runner)
├── requirements.txt
├── pyproject.toml               # ruff / mypy / pytest config
├── data/
│   └── fictional_product_logistics_report.pdf   # sample input doc
├── files/                       # Architecture and contract docs
├── src/
│   ├── agents/                  # SecurityAgent, TechnicalAgent, TaggingAgent + Pydantic schemas
│   │   ├── schemas.py           # QuestionItem, RawAssessmentRow, AgentLLMOutput, AssessmentRow, AgentResult, Summary
│   │   ├── security_agent.py    # SecurityAgent.assess() → AgentLLMOutput
│   │   ├── technical_agent.py   # TechnicalAgent.assess() → AgentLLMOutput
│   │   ├── tagging_agent.py     # TaggingAgent.tag() → list[TaggedChunk]
│   │   └── prompts/             # System + user prompt markdown files per agent
│   ├── config.py                # All BaseSettings classes (SecurityAgentConfig, TechnicalAgentConfig, PipelineConfig, …)
│   ├── db/
│   │   └── questions_repo.py    # fetch_policy_doc_by_category(), fetch_questions_by_policy_doc_id()
│   ├── handlers/
│   │   └── agent.py             # AGENT_REGISTRY, CONFIG_REGISTRY, SpecialistAgent + SpecialistAgentConfig protocols
│   └── utils/                   # Document parser, helpers, exceptions
└── tests/                       # Mirrors src/
```

---

## Setup

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

The pipeline calls the LLM via the **Anthropic API** (direct) or **AWS Bedrock** (local runner default). Add the following to a `.env` file in this directory (`app/agents/evaluation/.env`):

```
# Anthropic direct API (used by Agent Service and local runner)
ANTHROPIC_API_KEY=...

# Database — required for question/policy-doc lookups
DB_HOST=localhost
DB_PORT=5432
DB_NAME=...
DB_USER=...
DB_PASSWORD=...

# Optional — only if routing through Bedrock
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=eu-west-2
AWS_SESSION_TOKEN=...        # if using temporary creds
```

---

## Running locally

> **Note — SQS is not involved in the local runner.**
> [`main.py`](./main.py) reads a document from disk, drives parse → tag → extract sections → agent in-process, and writes the combined assessment output as JSON to `data/pipeline_output_<docId>.json`. No queues, no EventBridge, no S3 are touched.

From the repo root:

```bash
python app/agents/evaluation/main.py
```

This:

- Reads `data/fictional_product_logistics_report.pdf` (or the path you supply).
- Looks up checklist questions from PostgreSQL for each configured agent type.
- Drives all pipeline stages in-process.
- Writes the combined output to `data/pipeline_output_<docId>.json`.

### Local vs production

| Concern | Production (Agent Service) | Local (`main.py`) |
|---------|---------------------------|-------------------|
| Pipeline trigger | `TaskMessage` on SQS Tasks queue | Document path + doc ID via CLI args |
| Document source | Inline content in `TaskMessage` or S3 download | Local disk |
| Questions source | PostgreSQL (same) | PostgreSQL (same) |
| Parse → Tag → Extract | In-process inside Agent Service worker | In-process inside `main.py` |
| LLM calls | Anthropic API (via `make_llm_client()`) | Anthropic API or Bedrock |
| Result output | `StatusMessage` published to SQS Status queue | JSON file at `data/pipeline_output_<docId>.json` |
| Metrics | CloudWatch (Orchestrator side) | Not emitted |

### CLI args

```bash
python app/agents/evaluation/main.py [<s3Key>] [<docId>] [<output_path>]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `s3Key` | `data/fictional_product_logistics_report.pdf` | Path-like key (resolved locally relative to this dir). |
| `docId` | `UUID-<random>` | Document identifier echoed into the output. |
| `output_path` | `data/pipeline_output_<docId>.json` | Where to write the result JSON. |

### Output shape

```json
{
  "document_id": "UUID-...",
  "Security": {
    "Assessments": [
      {
        "question_id": "3fa85f64-...",
        "Question": "Is a data protection policy in place?",
        "Rating": "Green",
        "Comments": "The document clearly defines ...",
        "Reference": "Section 3.2 — Data Protection Policy"
      }
    ],
    "Summary": {
      "Interpretation": "Strong alignment with security requirements.",
      "Overall_Comments": "The document covers all key areas ..."
    }
  },
  "Technical": { "Assessments": [...], "Summary": {...} }
}
```

`question_id` is the UUID from the `policy_questions` table. `Reference` is a plain string (section / clause from the policy document). `Summary` replaces the old `Final_Summary` key.

---

## Configuration

### `config.yaml`

Operational defaults — committed to git.

| Section | Purpose |
|---------|---------|
| `agents.*` | Per-agent model, max_tokens, temperature. |
| `eventbridge` | EventBridge bus / source (Lambda-mode only — unused locally). |
| `cloudwatch` | Metrics namespace (Lambda-mode only — unused locally). |
| `pipeline` | Configured `agent_types`, agent → tag routing map, `section_labels` (display headings in report). |
| `parser` | PDF text-layer threshold and chunk size. |
| `database` | Non-secret Postgres defaults (port). |
| `local_runner` | Defaults driving `main.py` — data dir, output template, display keys. |

Every field has a corresponding env-var alias (e.g. `SECURITY_MODEL`, `LOCAL_RUNNER_DATA_DIR`). **Precedence: env > .env > yaml > code defaults.**

`pipeline.section_labels` maps agent type → display heading used in the Markdown report:

```yaml
pipeline:
  section_labels:
    security: "Security Policy"
    technical: "Technology Policy"
```

---

## Development

### Lint, type-check, test

Run from this directory (`app/agents/evaluation/`):

```bash
ruff check .
ruff format --check .
mypy src/
pytest tests/
```

All four must pass before committing.

### Verifying imports

```bash
python -c "from app.agents.evaluation.src.agents.security_agent import SecurityAgent"
python -c "from app.agents.evaluation.src.agents.technical_agent import TechnicalAgent"
python -c "from app.agents.evaluation.src.agents.schemas import AgentResult, AgentLLMOutput, RawAssessmentRow"
python -c "from app.agents.evaluation.src.config import SecurityAgentConfig, TechnicalAgentConfig, LocalRunnerConfig"
python -c "from app.agents.evaluation.src.db.questions_repo import fetch_policy_doc_by_category, fetch_questions_by_policy_doc_id"
python -c "from app.agents.evaluation.src.handlers.agent import AGENT_REGISTRY, CONFIG_REGISTRY"
```

---

## Further reading

- [`files/system_overview.md`](./files/system_overview.md) — full system tour (plain-English to deep-dive).
- [`files/aws_event_driven_orchestration.md`](./files/aws_event_driven_orchestration.md) — production AWS topology.
- [`files/system_input_output_SQS.md`](./files/system_input_output_SQS.md) — entry-point and output message contracts.
