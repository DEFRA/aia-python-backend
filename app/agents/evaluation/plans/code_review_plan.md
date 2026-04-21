# Code Review & Implementation Plan: Defra/src

## Context

Review of the `Defra/src` Python codebase — an AI-powered security and GDPR compliance assessment
tool that uses Claude to evaluate documents and generate PDF reports. Several modules are missing
or incomplete, preventing the code from running at all.

---

## Project Overview

**Purpose**: Automated document compliance checking (Security + GDPR) using Claude AI, with PDF output.
**Status**: Early-stage prototype — good foundations but blocked by missing modules.

---

## Findings

### Critical Blockers

| # | File | Issue |
|---|------|-------|
| 1 | `src/agents/security_agent.py` | Imports `src.config.SecurityAgentConfig` — **file does not exist** |
| 2 | `src/agents/security_agent.py` | Imports `src.utils.helpers` (`extract_jason_array`, `strip_code_fences`) — **file does not exist** |
| 3 | `src/agents/schemas.py` | **Empty** — expected to define `AgentResult`, `AssessmentRow`, `LLMResponseMeta` Pydantic models |
| 4 | `scripts/gdpr_compliance_agent.py` | Only 13 lines — `GDPRComplianceAgent.__init__` body is missing |

### Code Quality Issues

| # | File | Issue |
|---|------|-------|
| 5 | `src/agents/security_agent.py` | Typo: `extract_jason_array` (should be `extract_json_array`) |
| 6 | `src/utils/pdf_creator.py` | Hardcoded sample JSON embedded — not suitable beyond local testing |
| 7 | `src/utils/pdf_creator_multipage.py` | Same — hardcoded sample data in script body |
| 8 | `sharepoint_reader_dotenv.py` | Sits at project root rather than `src/utils/` |
| 9 | All agents | No error handling for Claude API failures or malformed JSON |
| 10 | All | No tests |

### Architecture Gaps

- No `main.py` or CLI entry point
- No `.env.example` documenting required environment variables
- No logging configuration
- `scripts/docling_pdf_parser.py` is completely empty

---

## What Works Well

- **Prompt engineering** (`src/agents/prompts/security.py`): well-structured few-shot examples, clear JSON schema
- **PDF generation** (`src/utils/pdf_creator_multipage.py`): solid colour-coded ReportLab implementation
- **SharePoint integration** (`sharepoint_reader_dotenv.py`): well-documented, env-based config
- **GDPR prompt** (`scripts/gdpr_compliance_system_prompt.py`): comprehensive GDPR obligation coverage
- **Documentation** (`files/`): solid checklist, policy template, scoring guide

---

## Implementation Steps (priority order)

1. **Create `src/agents/schemas.py`** — `AssessmentRow`, `LLMResponseMeta`, `AgentResult` Pydantic models
2. **Create `src/utils/helpers.py`** — `extract_json_array()`, `strip_code_fences()`
3. **Create `src/config.py`** — `SecurityAgentConfig` (API key, model, max tokens, etc.)
4. **Fix typo** in `security_agent.py`: `extract_jason_array` → `extract_json_array`
5. **Complete `scripts/gdpr_compliance_agent.py`** — mirror the security agent pattern
6. **Add error handling** to agent `run()` methods
7. **Create `.env.example`** documenting required environment variables
8. **Add `main.py`** entry point to wire up the full pipeline

---

## Files to Create / Modify

| Action | Path |
|--------|------|
| Create | `src/agents/schemas.py` |
| Create | `src/utils/helpers.py` |
| Create | `src/config.py` |
| Create | `.env.example` |
| Create | `main.py` |
| Edit   | `src/agents/security_agent.py` (fix typo, add error handling) |
| Edit   | `scripts/gdpr_compliance_agent.py` (complete implementation) |

---

## Verification

1. `python -c "from src.agents.security_agent import SecurityAgent"` — should import cleanly
2. Run `python main.py` against `files/security_policy.md` + `files/evaluation_questions.txt`
3. Confirm PDF is generated with correct Green/Amber/Red formatting
4. Verify GDPR agent produces equivalent structured output
