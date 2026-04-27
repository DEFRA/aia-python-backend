# ADR: Orchestrator Fan-Out — Dynamic Agent Dispatch

**Status:** Accepted  
**Date:** 2026-04-27  
**Component:** Orchestrator (`app/orchestrator/main.py`, `app/core/config.py`)

---

## Context

The Orchestrator dispatches work to specialist agents via SQS. Initially it sent a single `TaskMessage` driven by `ORCHESTRATOR_DEFAULT_AGENT_TYPE` (hardcoded to `general`). The requirement is to fan-out to multiple specialist agents whose types vary per document template.

The key question: **how does the Orchestrator know which agents to dispatch for a given document?**

---

## Options Considered

### Option 1 — Static global list in config

A flat list of agent types applied to every document regardless of template:

```yaml
orchestrator:
  agent_types: [security, data, risk, ea, solution]
```

**Pro:** Trivial to implement.  
**Con:** No per-template control — every document gets all agents even when the template only requires a subset. Wastes compute and cost.

---

### Option 2 — Template-to-agents mapping in `app/core/config.py` ✅ Chosen

Each `template_type` maps to an explicit list of agent types defined directly in `config.py`:

```python
TEMPLATE_AGENTS: dict[str, list[str]] = {
    "SDA": ["security", "data", "risk", "ea", "solution"],
    "CHEDP": ["security", "data", "risk"],
}
```

`template_type` already flows through the entire pipeline (`OrchestrateRequest` → `TaskMessage` → DB). The Orchestrator reads the list at dispatch time via `config.get_agent_types(template_type)`:

```python
agent_types = config.get_agent_types(template_type)
tasks = [TaskMessage(task_id=f"{doc_id}_{a}", agent_type=a, ...) for a in agent_types]
await asyncio.gather(*[sqs.send_task(t) for t in tasks])
expected_task_ids = {t.task_id for t in tasks}
```

The `SessionStore` already handles multiple `expected_task_ids` — the fan-in completion logic is unchanged.

**Pro:** Different templates get different agents. Defined in one place in source control — auditable, reviewable, and no extra file to manage. No DB query on the hot path. No additional dependency (removes the need for `pyyaml`).  
**Con:** Adding a new template or changing agent assignment requires a code change and redeploy. Acceptable for a controlled set of known specialist agents.

---

### Option 3 — Agent registry in the database

An `agent_registry` table: `(agent_type, template_type, enabled, queue_url)`. Orchestrator queries it at processing time.

**Pro:** Enable/disable agents at runtime without restart.  
**Con:** DB query on every document's hot path. Schema and migration overhead. Overkill until the agent set is large or changes frequently.

---

### Option 4 — Agent self-registration via Redis

Agents write a heartbeat entry (with TTL) on startup: `HSET agent-registry security {...}`. Orchestrator reads the live registry at dispatch time.

**Pro:** Truly dynamic — add a new agent service without touching Orchestrator config.  
**Con:** Significant complexity — heartbeat, TTL, deregistration, stale-entry handling. The fan-out list can change mid-flight if an agent crashes between dispatch and result. Much higher operational surface area.

---

## Decision

**Option 2: template-to-agents mapping in `config.yaml`.**

---

## Implementation

### `app/core/config.py`

- `TEMPLATE_AGENTS` dict holds the authoritative template-to-agents mapping
- `AppConfig.templates` property exposes `TEMPLATE_AGENTS`
- `AppConfig.get_agent_types(template_type)` returns the agent list for a given template; falls back to `[config.orchestrator.default_agent_type]` for unknown templates

### `app/orchestrator/main.py`

- Fan-out loop replaces the single `send_task` call
- `asyncio.gather` dispatches all `TaskMessage` objects concurrently
- `expected_task_ids` is built from the full set of dispatched tasks
- `SessionStore` and the timeout/PARTIAL_COMPLETE logic are unchanged

---

## Consequences

- Each template type is explicitly configured — adding a new template requires a one-line change in `config.yaml` and a redeploy.
- If `template_type` has no entry in `config.yaml`, the Orchestrator falls back to `[default_agent_type]` — no hard failure.
- The `result_md` markdown report will have one `##` section per agent type that responded.
- Fan-out to N agents means N `TaskMessage` entries in `aia-tasks` and N expected entries in `SessionStore`. The 8-minute timeout covers the slowest agent in the set.

---

## When to Revisit

Move to **Option 3 (DB registry)** if:
- Templates and their agent assignments need to be managed at runtime by a non-engineer (e.g., via an admin UI)
- The agent set grows large enough that a config file change becomes operationally inconvenient

Move to **Option 4 (self-registration)** if:
- Agent services need to be added or removed dynamically without any Orchestrator config touch
- A service mesh / dynamic scaling model is adopted
