# ADR: Orchestrator Session Storage — In-Memory vs Persistent

**Status:** Accepted  
**Date:** 2026-04-27  
**Component:** Orchestrator (`app/orchestrator/session.py`)

---

## Context

While the Orchestrator processes a document it tracks per-document agent dispatch state: which task IDs are expected, which results have arrived, and an `asyncio.Event` that signals completion. This state needs to live somewhere for the duration of a single assessment run (up to 8 minutes by default).

Three options were evaluated: in-memory Python dict, RDS PostgreSQL, and Redis.

---

## Decision

**Use in-memory session state** (`SessionStore` — a Python dict protected by `asyncio.Lock`).

---

## Options Considered

### Option 1 — In-Memory (chosen)

| | |
|-|-|
| **Pros** | Zero latency — `asyncio.Event` fires the moment the last result arrives; no I/O on the hot path. Simple to implement and reason about. RDS is only touched at terminal status write. |
| **Cons** | Lost on process restart — in-flight documents remain `PROCESSING` until the `cleanup_stuck_documents` job resets them (runs every 5 minutes). Single-instance only — two Orchestrator replicas would each have an isolated store. |

### Option 2 — RDS PostgreSQL

| | |
|-|-|
| **Pros** | Sessions survive crashes and redeploys without waiting for the cleanup cycle. Active sessions are observable via SQL. |
| **Cons** | `asyncio.Event` has no RDS equivalent — the clean `await asyncio.wait_for(event.wait(), timeout)` must be replaced with a polling loop, adding per-poll latency and complexity. Every agent result arrival requires a DB round-trip. RDS is designed for durable data; session state is transient (meaningful for ~8 minutes, worthless after). Adds unnecessary load to the primary data store. |

### Option 3 — Redis (ElastiCache)

| | |
|-|-|
| **Pros** | Low-latency reads/writes. TTL-managed automatic cleanup. **PUBLISH/SUBSCRIBE** can replace `asyncio.Event` — the status poller publishes on a per-document channel; `_process_document` subscribes and unblocks when all results arrive. Architecturally consistent — Redis/ElastiCache is already planned for the agent pipeline stages. |
| **Cons** | Adds an infrastructure dependency to a component that does not currently need it. Over-engineering for a single-instance POC deployment. |

---

## Rationale

For the current POC the Orchestrator runs as a **single process inside one ECS task**. The restart risk is already mitigated by `cleanup_stuck_documents` (resets `CLAIMED`/stuck `PROCESSING` rows every 5 minutes — see `app/repositories/document_repository.py`). There is no need to share session state across instances.

Moving to RDS would **break the event model**: `asyncio.Event` is the cleanest way to suspend `_process_document` while the status queue poller fills in results. Replacing it with DB polling introduces avoidable complexity and latency for state that is inherently short-lived.

---

## When to Revisit

Reconsider this decision when either of the following is true:

1. **Multiple Orchestrator replicas are needed** — the status queue poller on replica A can receive results for a session that only exists on replica B, causing silent discards and stuck documents.
2. **The 5-minute stuck-document window is unacceptable** — if users require faster recovery after an Orchestrator crash, persistent sessions become worthwhile.

In either case, **migrate to Redis pub/sub** (not RDS). Redis preserves the event-driven model via PUBLISH/SUBSCRIBE and is the natural fit for transient, high-throughput pipeline state.

---

## Consequences

- `SessionStore` is in-memory only; an Orchestrator restart loses all in-progress sessions.
- Documents stuck in `PROCESSING` after a restart are recovered within 5 minutes by `cleanup_stuck_documents`.
- The current design supports exactly one Orchestrator instance. Horizontal scaling requires a Redis migration before the second instance is added.
