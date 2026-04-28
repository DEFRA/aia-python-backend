# Defra Evaluation Pipeline

An AI-powered compliance checker that scores documents against security and information-governance checklists using a multi-agent pipeline. Produces a colour-coded result (Green / Amber / Red) per checklist question.

For the full architecture and design rationale, see [`files/system_overview.md`](./files/system_overview.md) and [`files/aws_event_driven_orchestration.md`](./files/aws_event_driven_orchestration.md).

---

## What it does

Given a document (PDF or DOCX), the pipeline:

1. **Parses** it into chunks.
2. **Tags** each chunk with security / governance taxonomy labels.
3. **Extracts** the relevant sections per specialist agent.
4. **Assesses** the sections against the agent's checklist.
5. **Emits** one assessment result per agent.

Two specialist agents are wired up: **Security** and **Governance**.

In production each stage is a separate AWS Lambda joined by EventBridge + SQS, with documents in S3 and metrics in CloudWatch. Locally [`main.py`](./main.py) **bypasses all of that** and runs the same business logic in-process — no AWS services are invoked except Bedrock for the model calls. See [Local vs production](#local-vs-production) below.

---

## Pipeline

```
SQS Tasks ─▶ Parse ─▶ Tag ─▶ Extract Sections ─┬─▶ Security Agent ─┐
                                               └─▶ Governance Agent ─┴─▶ SQS Status
```

- Entry point: SQS Tasks message body `{"docId": "...", "s3Key": "..."}`.
- Cross-stage payloads use an inline-or-S3 envelope ([`src/utils/payload_offload.py`](./src/utils/payload_offload.py)) — small payloads inline, large ones offloaded to S3.
- Terminal output: one `AgentStatusMessage` per `(docId, agentType)` on the SQS Status queue. There is no compile / persist / notify stage in this codebase.

---

## Layout

```
app/agents/evaluation/
├── main.py                      # Local end-to-end runner (mocks SQS)
├── config.yaml                  # Operational defaults (models, pipeline, runner)
├── requirements.txt
├── pyproject.toml               # ruff / mypy / pytest config
├── data/
│   ├── fictional_product_logistics_report.pdf   # sample input doc
│   └── sample_policy_assessment.json            # checklist questions for "Security"
├── files/                       # Architecture and contract docs
├── src/
│   ├── agents/                  # SecurityAgent, GovernanceAgent, TaggingAgent + Pydantic schemas
│   ├── config.py                # All BaseSettings classes
│   ├── db/                      # Assessment loader + (future) Postgres reads
│   ├── handlers/                # Per-stage Lambda handlers (parse / tag / extract_sections / agent)
│   └── utils/                   # Document parser, EventBridge publisher, payload offload, helpers
└── tests/                       # Mirrors src/
```

---

## Setup

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

The local runner authenticates via **AWS Bedrock**. Add the following to a `.env` file in this directory (`app/agents/evaluation/.env`):

```
SECURITY_MODEL=...
GOVERNANCE_MODEL=...
TAGGING_MODEL=...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=eu-west-2
AWS_SESSION_TOKEN=...        # if using temporary creds
```

---

## Running locally

> **Note — both ends of the SQS pipeline are mocked.**
> - **SQS Tasks (input)** — the runner does **not** read from a real queue; it constructs the SQS Tasks message body in Python (`{"docId": "...", "s3Key": "..."}`, validated through `SqsRecordBody`) and feeds it straight into the parse stage.
> - **SQS Status (output)** — the runner does **not** publish to a real queue either; the agent's terminal `AgentStatusMessage` is written as JSON to `data/pipeline_output_<docId>.json` instead. The output filename and folder are configurable via `local_runner.output_filename_template` / `local_runner.data_dir` in [`config.yaml`](./config.yaml).
>
> This lets you exercise the pipeline end-to-end without provisioning SQS or LocalStack. See [Local vs production](#local-vs-production) for the full list of mocked / bypassed AWS services.

From the repo root:

```bash
python app/agents/evaluation/main.py
```

This:

- Builds a **mock** SQS Tasks body `{"docId": "UUID-...", "s3Key": "data/fictional_product_logistics_report.pdf"}` — no real SQS interaction.
- Reads the document from local disk.
- Drives parse → tag → extract sections → agent end-to-end.
- Writes the combined output to `data/pipeline_output_<docId>.json`.

### Local vs production

The handler code in `src/handlers/` is real Lambda code (each module exposes a `lambda_handler(event, context)` and uses boto3 + EventBridge in the live path). The local runner deliberately **does not** go through any of that — it imports the underlying functions and runs them in-process so you can iterate without an AWS account or LocalStack.

| Concern                  | Production (deployed Lambdas)                                   | Local (`main.py`)                                       |
|--------------------------|-----------------------------------------------------------------|---------------------------------------------------------|
| Pipeline trigger         | SQS Tasks queue (real message)                                  | Mock body built as a Python `SqsRecordBody`             |
| Document storage         | S3 bucket                                                       | Local disk (`s3Key` resolved relative to this folder)   |
| Parse stage              | Lambda triggered by SQS                                         | `_parse_bytes()` called in-process                      |
| Tag → Extract Sections   | Lambdas chained by EventBridge events                           | Direct function calls; no events published              |
| Cross-stage payloads     | Inline ≤240 KB, else offloaded to S3 via `payload_offload`      | All passed in memory; offload code path not exercised   |
| Agent → terminal output  | One `AgentStatusMessage` per `(docId, agentType)` to SQS Status | One JSON file at `data/pipeline_output_<docId>.json`    |
| Metrics                  | CloudWatch (`Defra/Pipeline` namespace)                         | Not emitted                                             |
| LLM calls                | AWS Bedrock                                                     | AWS Bedrock (only AWS service the local runner touches) |

**The infrastructure that wires the Lambdas, EventBridge rules, and SQS queues together is provisioned separately by ops — it does not live in this repo.** Use the local runner for application-logic iteration; use a deployed environment for any infrastructure-level testing.

### CLI args

```bash
python app/agents/evaluation/main.py [<s3Key>] [<docId>] [<output_path>]
```

| Arg          | Default                                       | Description                                                  |
|--------------|-----------------------------------------------|--------------------------------------------------------------|
| `s3Key`      | `data/fictional_product_logistics_report.pdf` | Path-like key (resolved locally relative to this dir).       |
| `docId`      | `UUID-<random>`                               | Document identifier echoed into the output.                  |
| `output_path`| `data/pipeline_output_<docId>.json`           | Where to write the result JSON.                              |

Examples:

```bash
# Defaults
python app/agents/evaluation/main.py

# Custom doc and id, output at a custom path
python app/agents/evaluation/main.py data/my_doc.pdf UUID-1234 data/result.json
```

### Output shape

Matches [`files/system_input_output_SQS.md`](./files/system_input_output_SQS.md):

```json
{
  "docId": "UUID-...",
  "Security": {
    "Assessments": [
      { "Question": "...", "Rating": "Green|Amber|Red", "Comments": "...", "Reference": { "text": "...", "url": "..." } }
    ],
    "Final_Summary": { "Interpretation": "...", "Overall_Comments": "..." }
  },
  "Governance": { "Assessments": [...], "Final_Summary": {...} }
}
```

A `Governance` section appears only if a matching assessment file is present in `data/` (see Configuration below).

---

## Configuration

### `config.yaml`

Operational defaults — committed to git.

| Section        | Purpose                                                                              |
|----------------|--------------------------------------------------------------------------------------|
| `agents.*`     | Per-agent model, max_tokens, temperature, batch size.                                |
| `eventbridge`  | EventBridge bus / source (Lambda only — unused locally).                             |
| `cloudwatch`   | Metrics namespace (Lambda only — unused locally).                                    |
| `pipeline`     | Configured `agent_types`, SQS inline-payload limit, agent → tag routing map.         |
| `parser`       | PDF text-layer threshold and chunk size.                                             |
| `database`     | Non-secret Postgres defaults (port).                                                 |
| `local_runner` | Defaults driving `main.py` — data dir, assessment filename, output template, display keys. |

Every field has a corresponding env-var alias (e.g. `SECURITY_MODEL`, `LOCAL_RUNNER_DATA_DIR`). **Precedence: env > .env > yaml > code defaults.**

### Adding another assessment category

1. Drop a JSON file into `data/` matching the shape of [`data/sample_policy_assessment.json`](./data/sample_policy_assessment.json) (`uuid`, `url`, `category`, `details: [{question, reference, ...}]`).
2. Set `local_runner.assessment_filename` to that file (only one assessment file is read per run).
3. Make sure the agent type is listed in `pipeline.agent_types` and has a `display_keys` entry mapping the lowercase agent type to the `category` value used in the file.

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
python -c "from app.agents.evaluation.src.agents.schemas import AgentResult"
python -c "from app.agents.evaluation.src.config import SecurityAgentConfig, LocalRunnerConfig"
python -c "from app.agents.evaluation.src.db.assessment_loader import load_assessment_from_file"
```

---

## Further reading

- [`files/system_overview.md`](./files/system_overview.md) — full system tour (plain-English to deep-dive).
- [`files/aws_event_driven_orchestration.md`](./files/aws_event_driven_orchestration.md) — production AWS topology.
- [`files/system_input_output_SQS.md`](./files/system_input_output_SQS.md) — entry-point and output message contracts.
