# ADR: Cost Usage Composite Index on (doc_id, agent_name)

**Status:** Accepted  
**Date:** 2026-05-11  
**Component:** Cost Usage Persistence (`app/repositories/cost_usage_repository.py`, `app/utils/postgres.py`, `app/orchestrator/main.py`)

---

## Context

The orchestrator persists token usage from status messages into `backend.cost_usage`.

Current write path uses:

- Filter predicate: `WHERE doc_id = $1::uuid AND agent_name = $2`
- Access pattern: update-or-insert per `(doc_id, agent_name)`
- Read path for per-document views: `WHERE doc_id = ? ORDER BY agent_name ASC`

The existing table already had an index on `doc_id` only:

- `idx_cost_usage_doc_id ON backend.cost_usage(doc_id)`

Question: do we also need a composite index on `(doc_id, agent_name)`?

---

## Options Considered

### Option 1 — Keep only `idx_cost_usage_doc_id`

**Pros:**

- Fewer indexes to maintain on writes
- Lower storage footprint

**Cons:**

- Suboptimal for the upsert predicate that matches both `doc_id` and `agent_name`
- Less efficient for per-document ordering by `agent_name`
- Forces additional filtering work even when `doc_id` is selective

### Option 2 — Add `idx_cost_usage_doc_agent` on `(doc_id, agent_name)` ✅ Chosen

**Pros:**

- Aligns with primary write predicate `(doc_id, agent_name)`
- Supports per-document ordered reads by agent efficiently
- Reduces lookup cost as document/agent rows scale

**Cons:**

- Adds index-maintenance overhead on writes
- Becomes potentially redundant if replaced by a future unique index on the same columns

### Option 3 — Skip non-unique composite index and move directly to unique constraint

**Pros:**

- Strong idempotency guarantee for one row per `(doc_id, agent_name)`
- Enables native `INSERT ... ON CONFLICT (doc_id, agent_name) DO UPDATE`

**Cons:**

- Requires migration planning for any existing duplicates
- Not zero-risk in active environments without cleanup/verification first

---

## Decision

Adopt **Option 2** now:

- Keep `idx_cost_usage_doc_id`
- Add `idx_cost_usage_doc_agent ON backend.cost_usage(doc_id, agent_name)`

This provides immediate performance alignment with current upsert and read patterns while avoiding a risky constraint migration in the same change set.

---

## Consequences

- Current orchestrator persistence remains compatible with existing table schema.
- Query performance for doc+agent lookups is improved and more predictable.
- Write amplification increases slightly due to one additional index.
- The composite non-unique index can be superseded later by a unique index/constraint on the same columns.

---

## Follow-Up (Recommended)

1. Add a migration to enforce uniqueness on `(doc_id, agent_name)` after duplicate audit.
2. Replace custom update-then-insert logic with:
   - `INSERT ... ON CONFLICT (doc_id, agent_name) DO UPDATE`
3. Re-evaluate whether the non-unique composite index should be removed once the unique index exists.

---

## Notes

This ADR is intentionally scoped to index strategy only. Cost pricing (`total_cost_usd`) and pricing-model configuration are tracked separately from this decision.
