# ADR: Cost Model Phase 1 — Model-Aware Token Pricing in Orchestrator

**Status:** Accepted  
**Date:** 2026-05-11  
**Component:** Agent Service + Orchestrator (`app/models/status_message.py`, `app/agent_service/worker.py`, `app/orchestrator/main.py`)

---

## Context

Token usage is now tracked from agent execution and persisted into `backend.cost_usage`. Before this decision, `total_cost_usd` was persisted as a placeholder `0.0` because pricing was not configured in the orchestrator path.

To produce usable cost data, pricing must be computed from:

1. `input_tokens`
2. `output_tokens`
3. the exact LLM model ID used for the task

At the time of this ADR, the status message carried token counts but did not carry model identity, which made model-specific pricing impossible.

---

## Decision

Implement **Phase 1** with an in-process pricing map and model propagation across the status pipeline.

### 1) Propagate model identity in status payload

- Add optional `model_id` field to `StatusMessage`.
- Agent Service sets `model_id` from `agent_config.model` when publishing to `aia-status`.

### 2) Compute cost in orchestrator from model + tokens

- Add orchestrator pricing map keyed by model ID with per-million input/output token rates.
- Move rates into app config (`LLM_PRICING_USD_PER_MTOKENS`) with safe defaults.
- Compute:

```text
total_cost_usd = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
```

- Round to 6 decimal places for persistence consistency.

### 3) Safe fallback behavior

- If `model_id` is missing or unknown, default `total_cost_usd` to `0.0` and log a warning.
- If token values are negative, clamp to zero before pricing.

---

## Implemented Changes

- `app/models/status_message.py`
  - Added `model_id: Optional[str] = None` (serialized as `modelId` via camel aliasing).

- `app/agent_service/worker.py`
  - Publishes `model_id` in `StatusMessage` using `agent_config.model`.
  - Extended token logs to include `model_id`.

- `app/orchestrator/main.py`
  - Reads pricing map from app config.
  - Added `_calculate_total_cost_usd(model_id, input_tokens, output_tokens)` helper.
  - Replaced hard-coded `total_cost_usd = 0.0` with computed value.
  - Added warning when model has no configured pricing.

- `app/core/config.py`
  - Added `llm_pricing_usd_per_mtokens` with env alias `LLM_PRICING_USD_PER_MTOKENS`.
  - Added default pricing map for supported Bedrock/Anthropic model IDs.

- `tests/test_orchestrator_token_persistence.py`
  - Updated assertions to validate computed cost values.
  - Added fallback coverage for missing model.

---

## Consequences

### Positive

- `backend.cost_usage.total_cost_usd` now reflects estimated model-aware cost for known models.
- Cost data is useful immediately for reporting and trend analysis.
- Unknown models fail safely without breaking the pipeline.

### Trade-offs

- Pricing map is code-based; rate updates require code change/redeploy.
- No historical pricing table yet (future phases can add effective-dated pricing rows).

---

## Alternatives Considered

1. Keep placeholder `total_cost_usd = 0.0`
   - Rejected: no practical cost visibility.

2. External pricing service lookup in orchestrator
   - Rejected for Phase 1: adds runtime dependency and failure modes.

3. DB-backed pricing table with effective dates
   - Deferred to later phase: better for audit/historical pricing but more migration work.

---

## Follow-Up (Phase 2+)

1. Move pricing map to DB/config with effective dates.
2. Add model/rate reconciliation checks and alerting for unknown model IDs.
3. Consider adding currency/version metadata if pricing semantics evolve.
