# Coding Guide

Standards for all Python code in this project. Enforced by **Ruff** (style + lint) and **mypy** (static typing).

---

## Before Starting Any Task

Run these steps before writing or modifying any code.

### 1. Read the codebase

Understand the files relevant to your task before touching them:

- Read `CLAUDE.md` for architecture and key design decisions
- Read any `src/` modules you will modify — do not guess at existing behaviour
- Check `app/agents/evaluation/src/agents/schemas.py` if your task touches data shapes
- Check `app/agents/evaluation/src/config.py` if your task touches configuration or credentials

### 2. Git pre-flight

Check the current state of the working tree before making changes:

```bash
git status          # confirm which files are modified or staged
git diff            # review any uncommitted changes already present
git log --oneline -5  # understand recent context
```

Do not start coding if there are unexpected staged changes — investigate first.

### 3. Verify the environment

Confirm imports resolve and tooling is in a clean state:

```bash
python -c "from app.agents.evaluation.src.agents.security_agent import SecurityAgent"
python -c "from app.agents.evaluation.src.config import SecurityAgentConfig, DatabaseConfig"
ruff check .        # must be clean before you start, not just after
```

---

## Environments

Use `uv` to manage Python versions and virtual environments.

```bash
uv venv -p python3.13    # creates .venv/ at the project root
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -r app/agents/evaluation/requirements.txt
```

The `.venv/` directory must be listed in `.gitignore` and must never be committed.

---

## Tooling

| Tool | Purpose | Run |
|------|---------|-----|
| `ruff check .` | Lint — catches style, unused imports, common bugs | `ruff check .` |
| `ruff format .` | Format — replaces Black + isort | `ruff format .` |
| `mypy app/agents/evaluation/src/` | Static type checking | `mypy app/agents/evaluation/src/` |

Configure both in `pyproject.toml` (create if not present):

```toml
[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "PLR"]

[tool.ruff.lint.pylint]
max-statements = 50  # excludes docstrings

[tool.mypy]
python_version = "3.13"
strict = true
ignore_missing_imports = true
```

---

## Python Style

- **Line length**: 100 characters (Ruff enforces this)
- **Imports**: one import per line; stdlib → third-party → local, separated by blank lines (Ruff `I` rules handle ordering)
- **String quotes**: double quotes `"` (Ruff default)
- **Trailing commas**: use on multi-line collections and function signatures — makes diffs cleaner
- **f-strings** over `.format()` or `%` formatting

```python
# Good
message: str = f"Assessment complete: {len(assessments)} questions"

# Avoid
message = "Assessment complete: {} questions".format(len(assessments))
```

### Prefer the standard library

Before writing a utility function or reaching for a third-party package, check whether the Python standard library already covers it. Prefer `pathlib`, `json`, `re`, `datetime`, `itertools`, `functools`, `collections`, etc. over hand-rolled equivalents or additional dependencies.

```python
# Good — stdlib pathlib
from pathlib import Path

content: str = Path("app/agents/evaluation/files/security_policy.md").read_text(encoding="utf-8")

# Avoid — rolling your own file helpers when stdlib suffices
def read_file(path):
    with open(path) as f:
        return f.read()
```

Only add a third-party dependency when the stdlib genuinely cannot do the job (e.g. `anthropic` for the Claude API, `reportlab` for PDF generation, `pydantic` for schema validation).

---

## Naming Conventions

| Thing | Convention | Example |
|-------|-----------|---------|
| Module / file | `snake_case` | `security_agent.py` |
| Class | `PascalCase` | `SecurityAgent`, `AgentResult` |
| Function / method | `snake_case` | `assess()`, `strip_code_fences()` |
| Variable | `snake_case` | `raw_text`, `final_summary` |
| Constant | `UPPER_SNAKE_CASE` | `SECURITY_ASSESSMENT_SYSTEM_PROMPT` |
| Private helper | leading underscore | `_extract_response_meta()` |
| Type alias | `PascalCase` | `AssessmentList = list[AssessmentRow]` |

**Pydantic model fields** that mirror LLM JSON output use the same capitalisation as the JSON (e.g. `Question`, `Coverage`, `Evidence`) — this is intentional and an exception to snake_case.

---

## Type Hints

All code must be fully type-annotated using Python 3.10+ syntax. mypy `strict` is the target.

### Every variable

Annotate local variables whenever the type is not immediately obvious from the right-hand side.

```python
# Good
logger: logging.Logger = logging.getLogger(__name__)
assessments: list[AssessmentRow] = []
final_summary: FinalSummary | None = None
payload: dict[str, object] = json.loads(cleaned)

# Bad — type inferred but not explicit for non-trivial assignments
assessments = []
final_summary = None
```

### Every function parameter and return type

No parameter or return type may be left unannotated.

```python
# Good
async def assess(self, document: str, questions: list[str]) -> AgentResult:
    ...

def _format_questions_block(questions: list[str]) -> str:
    ...

# Bad — missing annotations
async def assess(self, document, questions):
    ...
```

### Python 3.10+ syntax rules

- Use `X | None` — not `Optional[X]`
- Use `X | Y` — not `Union[X, Y]`
- Use `list[X]`, `dict[K, V]`, `tuple[X, ...]` — not `List[X]`, `Dict[K, V]`, `Tuple[X, ...]`
- Use `type` keyword for aliases (3.12+) or `TypeAlias` from `typing` for 3.10/3.11

```python
# Good (3.10+)
def load(path: str) -> list[str] | None: ...

# Avoid (pre-3.10 style)
from typing import List, Optional
def load(path: str) -> Optional[List[str]]: ...
```

---

## Pydantic Boundary Validation

Every point where data crosses a module boundary — in or out — must be validated through a Pydantic v2 model. No plain `dict` or raw `json.loads()` output may be passed between modules.

### The rule

> **Validate at the boundary, trust within the module.**

Data arriving from outside the module (Lambda event, Redis cache, LLM response, function argument) is untrusted. Validate it with `model_validate()` or `TypeAdapter.validate_json()` on the first line. Data that has already been validated inside the module does not need re-validating as it flows through internal helpers.

### Agent inputs

Use a typed input model and validate at the entry point of `assess()`. Do not pass raw strings through without constraints.

```python
# Good — validated at the boundary; empty document or questions fail immediately
class AssessmentInput(BaseModel):
    document: Annotated[str, Field(min_length=1, max_length=500_000)]
    questions: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]

async def assess(self, document: str, questions: list[str]) -> AgentResult:
    inp: AssessmentInput = AssessmentInput.model_validate(
        {"document": document, "questions": questions}
    )
    ...

# Bad — no validation; empty document or blank questions reach the LLM silently
async def assess(self, document: str, questions: list[str]) -> AgentResult:
    user_content = TEMPLATE.format(document=document, ...)
```

### Agent outputs (LLM response parsing)

Parse the LLM JSON response with `json.loads()` then immediately validate each field through its model. Never pass a raw dict beyond the parsing block.

```python
# Good — raw dict only exists inside the parsing block; validated models exit
payload: dict[str, object] = json.loads(cleaned)
assessments: list[AssessmentRow] = [
    AssessmentRow.model_validate(row)
    for row in payload["Security"]["Assessments"]
]

# Bad — raw dict escapes into the return value
return payload["Security"]
```

### Lambda handler event boundaries

The first statement inside every `async def _handler()` must validate `event["detail"]` through its typed Pydantic model. Never pass the raw `detail` dict deeper into the handler.

```python
# Good — untyped EventBridge detail validated immediately
async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    detail: AgentCompleteDetail = AgentCompleteDetail.model_validate(event["detail"])
    doc_id: str = detail.doc_id
    ...

# Bad — raw dict passed into business logic
async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    doc_id: str = event["detail"]["docId"]   # KeyError risk; no type safety
    ...
```

### EventBridge publish

The `detail` argument to `publish_event()` must be a typed Pydantic model, not a plain dict. Serialise with `.model_dump()`.

```python
# Good — typed model, serialised at the boundary
detail: DocumentParsedDetail = DocumentParsedDetail(
    doc_id=doc_id,
    chunks_cache_key=cache_key,
)
publish_event("DocumentParsed", detail.model_dump(by_alias=True))

# Bad — plain dict, no schema enforcement
publish_event("DocumentParsed", {"docId": doc_id, "chunksCacheKey": cache_key})
```

### Redis read-back

Use `TypeAdapter.validate_json()` when reading cached values back from Redis. Never use bare `json.loads()` and pass the result as a plain dict.

```python
from pydantic import TypeAdapter
from app.agents.evaluation.src.agents.schemas import AgentResult, AgentResultAdapter

# Good — re-validated on the way out of the cache
raw: str | None = await redis.get(f"result:{doc_id}:{agent_type}")
if raw is not None:
    result: AgentResult = AgentResultAdapter.validate_json(raw)

# Bad — cached value treated as trusted; schema drift goes undetected
raw = await redis.get(f"result:{doc_id}:{agent_type}")
result = json.loads(raw)   # unvalidated dict; breaks mypy and runtime type safety
```

Define `TypeAdapter` instances in `app/agents/evaluation/src/agents/schemas.py` alongside the models they wrap:

```python
from pydantic import TypeAdapter

AgentResultAdapter: TypeAdapter[AgentResult] = TypeAdapter(AgentResult)
QuestionListAdapter: TypeAdapter[list[str]] = TypeAdapter(list[str])
```

### Report builder inputs

The PDF builder accepts a `list[ReportDataset]` — a typed Pydantic model, not `list[dict]`. Convert `AgentResult` to `ReportDataset` using model fields, not `model_dump()` into a raw dict.

```python
# Good — typed model handed to the PDF builder
dataset: ReportDataset = ReportDataset(
    section_name="Security",
    section=ReportSection(
        Assessments=[ReportAssessmentRow(**row.model_dump()) for row in result.assessments],
        Final_Summary=ReportFinalSummary(**result.final_summary.model_dump()),
    ),
)
build_security_report(datasets=[dataset], output_path=output_pdf)

# Bad — validated AgentResult converted back to an untyped dict
dataset = {"Security": {"Assessments": [...], "Final_Summary": {...}}}
build_security_report(datasets=[dataset], ...)
```

### ValidationError handling

Catch `pydantic.ValidationError` specifically at boundaries. Log with enough context to reproduce the failure, then re-raise. Do not swallow it.

```python
from pydantic import ValidationError

try:
    inp = AssessmentInput.model_validate({"document": document, "questions": questions})
except ValidationError as exc:
    logger.error("Invalid assess() inputs: %s", exc)
    raise
```

---

## Single Responsibility Functions

Each function or method must do exactly one thing. If you find yourself writing "and" when describing what a function does, split it.

**Signs a function needs splitting:**
- It exceeds 50 statements (enforced by Ruff `PLR0915` — docstrings excluded from the count)
- It has multiple levels of indentation doing unrelated work
- Its name contains "and" (e.g. `parse_and_validate`, `fetch_and_save`)
- It mixes I/O with business logic

```python
# Bad — one function doing three things
async def run(document_path: str) -> None:
    content = Path(document_path).read_text()       # I/O
    questions = content.split("\n")                  # parsing
    response = await client.messages.create(...)     # API call
    pdf = build_report(response)                     # rendering
    pdf.save("output.pdf")                           # I/O

# Good — each concern is isolated and independently testable
def load_document(path: str) -> str:
    """Read document text from disk."""
    return Path(path).read_text(encoding="utf-8")

def parse_questions(text: str) -> list[str]:
    """Split a newline-delimited questions file into a list."""
    return [line.strip() for line in text.splitlines() if line.strip()]

async def run_assessment(document: str, questions: list[str]) -> AgentResult:
    """Call the Claude agent and return structured results."""
    ...

def write_report(result: AgentResult, output_path: str) -> None:
    """Render the assessment result to a PDF file."""
    ...
```

---

## Dependency Injection

Pass dependencies (clients, config, connections) in via constructor or function parameters — never import or instantiate them inside a function body. This keeps every unit independently testable and removes hidden coupling between modules.

```python
# Good — dependencies injected; test can pass a mock client
class SecurityAgent:
    def __init__(self, client: AsyncAnthropic, agent_config: SecurityAgentConfig) -> None:
        self._client = client
        self._config = agent_config

    async def assess(self, document: str, questions: list[str]) -> AgentResult:
        response = await self._client.messages.create(...)
        ...

# Bad — client created inside the class; impossible to test without a real API key
class SecurityAgent:
    async def assess(self, document: str, questions: list[str]) -> AgentResult:
        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])  # hidden dependency
        response = await client.messages.create(...)
        ...
```

The same rule applies to functions:

```python
# Good — Redis and DB pool injected; function is pure and testable
async def get_questions(
    agent_type: str,
    redis: Redis,
    db_pool: asyncpg.Pool,
) -> list[str]:
    ...

# Bad — function reaches out and creates its own connection
async def get_questions(agent_type: str) -> list[str]:
    redis = Redis.from_url(os.environ["REDIS_URL"])  # hidden dependency
    ...
```

**Rules:**
- Constructors and function signatures are the only place dependencies should enter a module
- Module-level singletons (e.g. Lambda connection reuse — see [Connection management](#connection-management-cold-starts)) are the one exception; they must be private (`_redis`, `_db_pool`) and accessed only through a dedicated getter
- Config objects (`SecurityAgentConfig`, `DatabaseConfig`) are dependencies too — inject them, never call `os.environ` directly inside business logic

---

## EAFP vs LBYL

Python favours **EAFP** (Easier to Ask Forgiveness than Permission) over **LBYL** (Look Before You Leap). Attempt the operation and handle the exception, rather than pre-checking conditions that can change between the check and the action.

```python
# Good — EAFP: attempt access, handle the miss
try:
    value: str = data["key"]
except KeyError:
    value = default

# Avoid — LBYL: the key could disappear between the check and the access
if "key" in data:
    value = data["key"]
```

```python
# Good — EAFP: attempt the parse, handle failure
try:
    payload: dict[str, object] = json.loads(raw)
except json.JSONDecodeError as exc:
    logger.error("Invalid JSON from Claude. raw=%.200s", raw)
    raise ValueError("Could not parse assessment response") from exc

# Avoid — LBYL: pre-checking JSON validity duplicates the parse work
if raw.strip().startswith("{"):
    payload = json.loads(raw)
```

**When LBYL is appropriate:**

Use LBYL for cheap, reliable pre-conditions that make intent clearer — particularly at function entry:

```python
def build_report(datasets: list[dict[str, object]], output_path: str) -> None:
    if not datasets:
        raise ValueError("datasets must not be empty")
    ...
```

**Rule of thumb:** if the check and the action are a single logical step, use EAFP. If the check is a meaningful guard on inputs, use LBYL at the function boundary.

---

## Error Handling

**Only catch exceptions you can handle or meaningfully log.** Let unexpected errors propagate.

```python
# Good — catch specific, log with context, re-raise
try:
    payload: dict[str, object] = json.loads(cleaned)
except json.JSONDecodeError as exc:
    logger.error("Failed to parse Claude response. raw=%.200s error=%s", raw_text, exc)
    raise ValueError(f"Could not parse assessment response: {exc}") from exc

# Bad — swallowing errors silently
try:
    payload = json.loads(cleaned)
except Exception:
    pass
```

**Rules:**
- Catch the most specific exception type available (`json.JSONDecodeError`, `KeyError`, `anthropic.APIError`)
- Always log with enough context to reproduce the problem (include relevant variable values)
- Use `raise ... from exc` to preserve the exception chain
- Do not use bare `except:` or `except Exception:` unless re-raising immediately
- Validate at system boundaries (user input, Claude API responses) — trust internal module calls
- Domain-specific exception classes must subclass `Exception` and end with `Error`:

```python
class DocumentParseError(Exception): ...
class AgentResponseError(Exception): ...
```

### Lambda error handling

Lambda handlers must **raise** unhandled exceptions — do not catch-and-suppress at the handler level. EventBridge and SQS rely on exceptions to trigger retries and route to the DLQ.

```python
# Good — exception propagates, Lambda marks invocation as failed, SQS retries
async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    detail: dict[str, Any] = event["detail"]
    doc_id: str = detail["docId"]
    result = await run_agent(doc_id)   # raises on failure → Lambda retries
    await publish_event("AgentComplete", {"docId": doc_id})
    return {"statusCode": 200}

# Bad — exception swallowed, Lambda reports success, document silently lost
async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    try:
        result = await run_agent(event["detail"]["docId"])
    except Exception:
        logger.error("something went wrong")   # no re-raise!
    return {"statusCode": 200}
```

When calling `put_events()`, always check `FailedEntryCount` — the call itself does not raise on partial failure:

```python
response = _events.put_events(Entries=[...])
if response["FailedEntryCount"] > 0:
    failed = response["Entries"]
    raise RuntimeError(f"EventBridge put_events partial failure: {failed}")
```

---

## Docstrings & Comments

Use **Google-style** docstrings for all public functions, methods, and classes.

```python
def build_security_report(datasets: list[dict[str, object]], output_path: str = "report.pdf") -> None:
    """Build a multi-page PDF from one or more assessment datasets.

    Args:
        datasets: List of dicts, each with a top-level section key (e.g. "Security")
            containing "Assessments" and "Final_Summary".
        output_path: File path for the generated PDF.

    Raises:
        ValueError: If a dataset is missing required keys.
    """
```

**When to write a docstring:**
- All public functions and methods
- All classes
- Modules — one-line summary at the top is sufficient

**When to write an inline comment:**
- When the *why* is not obvious from the code
- Never restate what the code does — explain intent or constraint

```python
# Good — explains a non-obvious constraint
temperature=0.0  # Deterministic output required for consistent audit trails

# Bad — just restates the code
temperature=0.0  # Set temperature to 0
```

**Private / internal helpers** (prefixed `_`) need a one-line docstring minimum.

---

## Lambda Handler Pattern

Each pipeline stage is a Lambda function. Use this structure consistently across all handlers.

> See [Stage-by-Stage Breakdown](./app/agents/evaluation/files/aws_event_driven_orchestration.md#stage-by-stage-breakdown) for each stage's responsibilities.

```python
import asyncio
import json
import logging
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point — delegates to async core."""
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    detail: dict[str, Any] = event["detail"]
    doc_id: str = detail["docId"]

    # Read state from Redis, call Claude if needed, publish to EventBridge
    ...

    return {"statusCode": 200}
```

**Rules:**
- The public `handler()` is sync (Lambda requirement) — delegate immediately to `async def _handler()`
- Parse `event["detail"]` into typed variables at the top; never pass the raw event dict deeper
- Keep handlers thin — orchestration only; business logic lives in `src/agents/` or `src/utils/`
- Log `doc_id` at INFO level on entry so CloudWatch can correlate logs to documents

### Connection management (cold starts)

Initialise Redis and database connections **outside** the handler function so they are reused across warm invocations. Never open a new connection on every call.

```python
from redis.asyncio import Redis
import asyncpg

# Module-level — created once per container, reused across warm invocations
_redis: Redis | None = None
_db_pool: asyncpg.Pool | None = None


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    return _redis


async def _get_db_pool() -> asyncpg.Pool:
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(dsn=os.environ["DATABASE_URL"])
    return _db_pool
```

---

## Redis Usage Conventions

Redis (ElastiCache) is the shared state store between all pipeline stages.

> See [Redis Key Reference](./app/agents/evaluation/files/aws_event_driven_orchestration.md#redis-key-reference) for the full key inventory and TTLs.

### Key naming

Keys follow the pattern `{entity}:{identifier}:{qualifier}`:

| Example key | Entity | Identifier | Qualifier |
|-------------|--------|------------|-----------|
| `chunks:{content_hash}` | `chunks` | content hash | — |
| `sections:{docId}:security` | `sections` | docId | agent type |
| `result:{docId}:risk` | `result` | docId | agent type |
| `questions:{agentType}` | `questions` | agent type | — |

Never use filenames or display names as cache keys — always use stable, content-derived or UUID-based identifiers.

### TTL constants

Define TTLs as named constants; never scatter magic numbers:

```python
CHUNK_CACHE_TTL: int = 86_400        # 24 hours — parsed content (content-hash key)
TAGGED_CACHE_TTL: int = 86_400       # 24 hours — tagging is the most expensive LLM call
SECTION_CACHE_TTL: int = 3_600       # 1 hour  — per-agent section slices
QUESTION_CACHE_TTL: int = 3_600      # 1 hour  — invalidate on question table update
RESULT_CACHE_TTL: int = 3_600        # 1 hour  — individual agent results
STAGE8_COUNTER_TTL: int = 1_800      # 30 min  — fan-in counter for Persist + Move
```

### Cache-aside pattern

Check Redis first; fall back to source on a miss; write back immediately.

Use `redis.asyncio` (built into `redis-py` v4+) — no extra dependency, works natively with `asyncio.run()` in Lambda:

```python
from redis.asyncio import Redis
```

```python
async def get_questions(agent_type: str, redis: Redis, db_pool: asyncpg.Pool) -> list[str]:
    """Return checklist questions, using Redis as a read-through cache."""
    cache_key: str = f"questions:{agent_type}"
    cached: str | None = await redis.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    questions: list[str] = await fetch_questions_by_category(db_pool, agent_type)
    await redis.setex(cache_key, QUESTION_CACHE_TTL, json.dumps(questions))
    return questions
```

### Fan-in with INCR

Use atomic `INCR` to coordinate completion of parallel stages — no locks needed:

```python
TOTAL_AGENTS: int = 5  # security, data, risk, ea, solution

count: int = await redis.incr(f"results_count:{doc_id}")
await redis.expire(f"results_count:{doc_id}", RESULT_CACHE_TTL)
if count == TOTAL_AGENTS:
    await publish_event("AllAgentsComplete", {"docId": doc_id})
```

---

## EventBridge Event Publishing

All stage transitions are published to the `defra-pipeline` custom event bus.

> See [EventBridge Rules Reference](./app/agents/evaluation/files/aws_event_driven_orchestration.md#eventbridge-rules-reference) for the full rule-to-target mapping.

### Standard publish helper

```python
import json
import boto3
from typing import Any

_events = boto3.client("events")

EVENT_BUS_NAME: str = "defra-pipeline"
EVENT_SOURCE: str = "defra.pipeline"


def publish_event(detail_type: str, detail: dict[str, Any]) -> None:
    """Publish a single event to the defra-pipeline EventBridge bus.

    Args:
        detail_type: PascalCase event name matching an EventBridge rule
            (e.g. "DocumentParsed", "AllAgentsComplete").
        detail: Arbitrary JSON-serialisable dict placed in the event's detail field.
    """
    _events.put_events(
        Entries=[
            {
                "Source": EVENT_SOURCE,
                "EventBusName": EVENT_BUS_NAME,
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
            }
        ]
    )
```

**Conventions:**
- `detail-type` values are **PascalCase** and describe a completed action in past tense: `DocumentParsed`, `AgentComplete`, `ResultPersisted`
- Always include `docId` in `detail` — every downstream stage needs it
- Pass cache keys (e.g. `chunksCacheKey`) through the event rather than data payloads — keep events small
- Never publish raw document content in an event

---

## Agent Types

The pipeline runs five specialist agents in parallel at Stage 6. Each maps to a checklist category in PostgreSQL and a Redis key qualifier.

> See [Stage 6 — Specialist Agents](./app/agents/evaluation/files/aws_event_driven_orchestration.md#stage-6--specialist-agents-parallel) for the full parallel execution pattern.

| Agent type | Purpose | Redis key qualifier | DB `agent_type` value |
|------------|---------|--------------------|-----------------------|
| `security` | Security controls assessment | `security` | `"security"` |
| `data` | Data handling and privacy | `data` | `"data"` |
| `risk` | Risk identification | `risk` | `"risk"` |
| `ea` | Enterprise architecture alignment | `ea` | `"ea"` |
| `solution` | Solution design review | `solution` | `"solution"` |

All five follow the same `SecurityAgent` pattern: `__init__(client, config)` + `async assess(document, questions) -> AgentResult`. New agent types must be added to this table and to the PostgreSQL `checklist_questions` table with the matching `agent_type` value.

---

## Development Workflow (TDD)

All development on this project follows a strict test-first cycle. This applies equally to human developers and the coding agent.

### The loop — for every discrete unit of work

1. **Write a failing test** — before any implementation, write a test that defines the expected behaviour and confirm it fails
2. **Implement** — write the minimum code to make the test pass; do not over-engineer
3. **Verify green** — run `pytest app/agents/evaluation/src/tests/ -v --tb=short -x` and confirm the test passes
4. **Quality gate** — run ruff and mypy; fix all errors before moving on:
   ```bash
   ruff check . && ruff format . && mypy app/agents/evaluation/src/
   ```
5. **Repeat** for the next unit of work

No code is considered done until it has a passing test and a clean quality gate.

### Why test-first

- Forces the expected behaviour to be defined before implementation begins
- Ensures every piece of new code is testable by design — if it is hard to test, the design is wrong
- Gives a clear, objective signal of when the work is complete
- Prevents regressions — the test stays in the suite permanently

### Scope

| Task type | Write failing test first? | Safety net |
|-----------|--------------------------|------------|
| New feature | Yes — always | New test written by test-agent |
| Bug fix | Yes — test reproduces the bug before the fix | New test written by test-agent |
| Refactor | No — behaviour must not change | Existing suite must stay green throughout |

For refactoring: confirm the full suite is green before starting, run tests after each discrete change, and stop immediately if anything breaks. Never carry a failing test forward during a refactor.

---

## Testing

Run the test suite with:
```bash
pytest app/agents/evaluation/src/tests/
```

### Test layout

Mirror the `src/` structure under `src/tests/`:
```
app/agents/evaluation/src/tests/
  agents/
    test_specialist_agents.py  # Unit tests for all agent assess() methods
    test_tagging_agent.py
    test_schemas.py            # Pydantic model validation tests
  handlers/
    test_agent_handler.py      # Lambda handler tests with mock EventBridge events
    test_parse.py
    test_tag.py
    test_extract_sections.py
  utils/
    test_document_parser.py
    test_eventbridge.py
    test_redis_client.py
  test_config.py
```

### Unit tests — agents

Mock the Anthropic client to avoid live API calls:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agents.evaluation.src.agents.security_agent import SecurityAgent
from app.agents.evaluation.src.agents.schemas import AgentResult


@pytest.mark.asyncio
async def test_assess_returns_agent_result() -> None:
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=MagicMock(
        content=[MagicMock(text='{"Security": {"Assessments": [], "Final_Summary": {"Interpretation": "Strong alignment", "Overall_Comments": ""}}}')],
        model="claude-opus-4-6",
        usage=MagicMock(input_tokens=100, output_tokens=50),
        stop_reason="end_turn",
    ))

    agent = SecurityAgent(client=mock_client, agent_config=MagicMock())
    result: AgentResult = await agent.assess("doc text", ["Q1", "Q2"])

    assert isinstance(result, AgentResult)
```

### Lambda handler tests — mock EventBridge events

Construct the `event` dict to match the EventBridge envelope exactly:

```python
@pytest.mark.asyncio
async def test_agent_handler_publishes_event(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[dict] = []
    monkeypatch.setattr(
        "app.agents.evaluation.src.handlers.agent.publish_event",
        lambda dt, d: published.append({"detail_type": dt, "detail": d}),
    )

    event = {
        "detail": {
            "docId": "test-doc-001",
            "agentType": "security",
        }
    }
    from app.agents.evaluation.src.handlers.agent import handler
    handler(event, {})

    assert any(e["detail_type"] == "AgentComplete" for e in published)
```

### Redis tests — fakeredis

Use `fakeredis` for in-memory Redis in tests — no real ElastiCache needed:

```python
import fakeredis.aioredis as fakeredis
import pytest

@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()

@pytest.mark.asyncio
async def test_cache_aside_miss_populates_cache(redis: fakeredis.FakeRedis) -> None:
    # On a miss, the function should write to Redis and return from DB
    ...
```

Install with: `pip install fakeredis[aioredis]`
