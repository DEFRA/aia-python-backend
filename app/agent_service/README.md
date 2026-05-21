# Agent Service

The Agent Service is an ECS Fargate process that polls the `aia-tasks` SQS queue,
dispatches each task to the correct specialist LLM agent (security, technical),
and publishes results to the `aia-status` queue.

## Structure

```
app/agent_service/
├── src/
│   ├── main.py                  # FastAPI app entry point (:8002)
│   ├── worker.py                # SQS polling loop + dispatch
│   ├── config.py                # Pydantic settings (YAML + env)
│   ├── agents/
│   │   ├── security_agent.py    # Security assessment agent
│   │   ├── technical_agent.py   # Technical compliance agent
│   │   ├── tagging_agent.py     # Document chunk tagging agent
│   │   └── prompts/             # LLM prompt templates
│   ├── database/
│   │   └── questions_repo.py    # PostgreSQL queries
│   ├── handlers/
│   │   └── agent.py             # Agent/config registries
│   ├── utils/
│   │   ├── doc_parser.py        # PDF/DOCX text extraction
│   │   ├── eventbridge.py       # EventBridge publisher
│   │   ├── exceptions.py        # Custom exceptions
│   │   ├── helpers.py           # JSON parsing helpers
│   │   ├── llm_client.py        # LLM client factory
│   │   ├── payload_offload.py   # S3 payload offload
│   │   ├── pdf_creator.py       # Single-page PDF generation
│   │   ├── pdf_creator_multipage.py  # Multi-page PDF generation
│   │   └── retry.py             # Async retry utilities
│   ├── models/
│   │   └── schemas.py           # Pydantic models
│   ├── routes/
│   │   └── product_route.py     # Placeholder routes
│   └── tests/
├── config.yaml                  # Operational defaults
├── requirements.txt             # Service-specific dependencies
├── README.md
├── docs/
├── data/
├── files/
└── plans/
```

## Running

```bash
# Via start scripts (recommended)
./scripts/start-aia.sh

# Standalone
uvicorn app.agent_service.src.main:app --host 127.0.0.1 --port 8002
```

## Configuration

All operational defaults live in `config.yaml`. Secrets and deployment-specific
values are sourced from environment variables (see root `.env`).
