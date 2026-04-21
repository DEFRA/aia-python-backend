# Plan 01 — Src Tree Restructure

**Priority:** 1 (must be done first — all subsequent plans depend on this layout)

## Goal

Establish the canonical `src/` directory structure that maps 1-to-1 with the 9-stage
event-driven pipeline. All subsequent plans add files into this skeleton.

---

## Current State

```
src/
  __init__.py
  config.py
  agents/
    __init__.py
    schemas.py
    security_agent.py
    prompts/
      __init__.py
      security.py
  db/
    __init__.py
    questions_repo.py
  plans/
    code_review_plan.md          ← move to top-level plans/
  utils/
    helpers.py
    pdf_creator.py
    pdf_creator_multipage.py
    (no __init__.py)
```

---

## Target State

```
plans/                           ← top-level, not inside src/
  01-src-tree-restructure.md
  02-shared-infrastructure.md
  ...

src/
  __init__.py
  config.py                      ← extend with Redis + EventBridge settings (Plan 02)

  agents/
    __init__.py
    schemas.py                   ← extend with TaggedChunk, CompiledResult models (Plan 04+)
    security_agent.py            ← exists
    tagging_agent.py             ← new (Plan 04)
    data_agent.py                ← new (Plan 06)
    risk_agent.py                ← new (Plan 06)
    ea_agent.py                  ← new (Plan 06)
    solution_agent.py            ← new (Plan 06)
    prompts/
      __init__.py
      security.py                ← exists
      tagging.py                 ← new (Plan 04)
      data.py                    ← new (Plan 06)
      risk.py                    ← new (Plan 06)
      ea.py                      ← new (Plan 06)
      solution.py                ← new (Plan 06)

  db/
    __init__.py
    questions_repo.py            ← exists

  handlers/                      ← new directory (all Lambda entry points)
    __init__.py
    parse.py                     ← Stage 3 (Plan 03)
    tag.py                       ← Stage 4 (Plan 04)
    extract_sections.py          ← Stage 5 (Plan 05)
    agent.py                     ← Stage 6 dispatcher (Plan 06)
    compile.py                   ← Stage 7 (Plan 07)
    persist.py                   ← Stage 8a (Plan 08)
    s3_move.py                   ← Stage 8b (Plan 08)
    notify.py                    ← Stage 9 (Plan 09)

  utils/
    __init__.py                  ← new (currently missing)
    helpers.py                   ← exists
    pdf_creator.py               ← exists
    pdf_creator_multipage.py     ← exists
    redis_client.py              ← new (Plan 02)
    eventbridge.py               ← new (Plan 02)
```

---

## Steps

### 1. Move plans out of src/

Move `src/plans/code_review_plan.md` → `plans/code_review_plan.md` and delete
`src/plans/` directory.

```bash
mv src/plans/code_review_plan.md plans/
rmdir src/plans
```

### 2. Create missing `__init__.py` files

```bash
touch src/utils/__init__.py
touch src/handlers/__init__.py
```

### 3. Create `src/handlers/` directory with stub files

Each stub is a valid Python module with a `lambda_handler` function skeleton and a
docstring describing the stage. No logic yet — that comes in the respective plans.

```
src/handlers/__init__.py
src/handlers/parse.py
src/handlers/tag.py
src/handlers/extract_sections.py
src/handlers/agent.py
src/handlers/compile.py
src/handlers/persist.py
src/handlers/s3_move.py
src/handlers/notify.py
```

Stub template (example for `parse.py`):

```python
"""Stage 3 — Parse Lambda handler.

Triggered by SQS (polling). Parses PDF/DOCX to chunks and publishes DocumentParsed.
Full implementation: plans/03-parse-lambda.md
"""
from __future__ import annotations

import asyncio
from typing import Any


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    raise NotImplementedError("See plans/03-parse-lambda.md")
```

### 4. Create stub agent files

```
src/agents/tagging_agent.py
src/agents/data_agent.py
src/agents/risk_agent.py
src/agents/ea_agent.py
src/agents/solution_agent.py
```

Stub template (example for `data_agent.py`):

```python
"""Data governance specialist agent.

Full implementation: plans/06-specialist-agents.md
"""
from __future__ import annotations

from src.agents.schemas import AgentResult


class DataAgent:
    """Evaluates data governance sections of a document."""

    async def assess(self, sections: list[dict], questions: list[str]) -> AgentResult:
        raise NotImplementedError("See plans/06-specialist-agents.md")
```

### 5. Create stub prompt files

```
src/agents/prompts/tagging.py
src/agents/prompts/data.py
src/agents/prompts/risk.py
src/agents/prompts/ea.py
src/agents/prompts/solution.py
```

Each stub exports `SYSTEM_PROMPT: str = ""  # TODO: see plans/`.

### 6. Create stub utility files

```
src/utils/redis_client.py
src/utils/eventbridge.py
```

Full implementation deferred to Plan 02.

---

## Verification

After completing this plan, the following must all succeed:

```bash
python -c "from src.handlers import parse, tag, extract_sections, agent, compile, persist, s3_move, notify"
python -c "from src.agents import tagging_agent, data_agent, risk_agent, ea_agent, solution_agent"
python -c "from src.agents.prompts import tagging, data, risk, ea, solution"
python -c "from src.utils import redis_client, eventbridge"
ruff check . && mypy src/
```

---

## Acceptance Criteria

- [ ] `src/handlers/` exists with 8 stub Lambda handler files + `__init__.py`
- [ ] `src/agents/` has 5 agent files (1 existing + 4 stubs)
- [ ] `src/agents/prompts/` has 6 prompt files (1 existing + 5 stubs)
- [ ] `src/utils/__init__.py` exists
- [ ] `src/utils/redis_client.py` and `eventbridge.py` exist as stubs
- [ ] `src/plans/` is removed; plan files live at top-level `plans/`
- [ ] `ruff check .` passes with no errors
- [ ] `mypy src/` passes (stubs may use `# type: ignore` temporarily)
