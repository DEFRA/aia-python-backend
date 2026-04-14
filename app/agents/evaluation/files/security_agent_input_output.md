# Security Agent Input / Output Reference

## Overview

This document describes both the **input** sent to the Security Assessment Agent and the **output** it returns. Schemas are defined in `src/agents/schemas.py` and the prompt in `src/agents/prompts/security.py`.

Source files:
- Schemas: `src/agents/schemas.py`
- Prompt: `src/agents/prompts/security.py`
- Agent: `src/agents/security_agent.py`
- DB repository: `src/db/questions_repo.py`
- Entry point: `main.py`

---

## Part 1 — Input

The agent receives two inputs that are assembled into a single API call: a **document** and a **list of questions**.

### 1.1 Document

The document is the full text of a file uploaded by the user. It is read from disk in `main.py` and passed as a plain string to `SecurityAgent.assess()`.

```python
document: str = Path(document_path).read_text(encoding="utf-8")
```

Example (truncated):

```
# System Architecture — Payments Platform v2

## 1. Introduction
This document describes the architecture of the Payments Platform...

## 3.1 Authentication
All users authenticate via Azure Active Directory (AAD) using SSO.
MFA is enforced for all roles via Conditional Access policies...
```

---

### 1.2 Questions from PostgreSQL

Questions are no longer loaded from a flat file. They are fetched asynchronously from a PostgreSQL table, filtered by category.

**Table schema:**

```sql
CREATE TABLE checklist_questions (
    id        SERIAL PRIMARY KEY,
    category  VARCHAR(100) NOT NULL,  -- e.g. 'Security'
    question  TEXT         NOT NULL
);
```

**Query executed by `fetch_questions_by_category(dsn, category)`:**

```sql
SELECT   question
FROM     checklist_questions
WHERE    LOWER(category) = LOWER($1)   -- $1 = 'Security'
ORDER BY id;
```

**Result — raw list returned to the agent:**

```python
[
    "Is authentication defined (SSO, OAuth2, Azure AD, MFA)?",
    "Are authorisation models clear (RBAC, ABAC)?",
    "Is data encrypted in transit and at rest?",
    "Are data retention and disposal policies defined?",
    "Is there a defined incident response and breach notification process?"
]
```

---

### 1.3 Assembled User Message

Before calling the API, `_format_questions_block()` converts the list into a numbered string, which is injected into `SECURITY_ASSESSMENT_USER_TEMPLATE`:

```python
def _format_questions_block(questions: list[str]) -> str:
    return "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
```

**Formatted questions block:**

```
1. Is authentication defined (SSO, OAuth2, Azure AD, MFA)?
2. Are authorisation models clear (RBAC, ABAC)?
3. Is data encrypted in transit and at rest?
4. Are data retention and disposal policies defined?
5. Is there a defined incident response and breach notification process?
```

**Final user message sent to Claude (`user` role):**

```
<document>
# System Architecture — Payments Platform v2
...full document text...
</document>

<questions>
1. Is authentication defined (SSO, OAuth2, Azure AD, MFA)?
2. Are authorisation models clear (RBAC, ABAC)?
3. Is data encrypted in transit and at rest?
4. Are data retention and disposal policies defined?
5. Is there a defined incident response and breach notification process?
</questions>

Assess the document against each question. Return ONLY a valid JSON object with the following structure:
{
  "Security": {
    "Assessments": [...],
    "Final_Summary": { ... }
  }
}
```

**API call parameters:**

| Parameter | Value |
|-----------|-------|
| `model` | `claude-opus-4-6` (from `SecurityAgentConfig`) |
| `max_tokens` | `4096` |
| `temperature` | `0.0` |
| `system` | `SECURITY_ASSESSMENT_SYSTEM_PROMPT` |
| `messages[0].role` | `user` |
| `messages[0].content` | Assembled template above |

---

## Part 2 — Output

### 2.1 Raw LLM Output

Claude returns a single JSON object nested under a top-level `"Security"` key. No markdown fences or preamble — raw JSON only.

```json
{
  "Security": {
    "Assessments": [
      {
        "Question": "Is authentication defined (SSO, OAuth2, Azure AD, MFA)?",
        "Coverage": "Green",
        "Evidence": "Authentication is fully defined in Section 3.1, covering SSO via Azure AD, OAuth2 token flows, and MFA enforcement for all user roles."
      },
      {
        "Question": "Are authorisation models clear (RBAC, ABAC)?",
        "Coverage": "Amber",
        "Evidence": "Section 3.2 defines RBAC as the backbone and ABAC for contextual decisions. However, final business-role mapping and attribute sources are pending sign-off."
      },
      {
        "Question": "Are data retention and disposal policies defined?",
        "Coverage": "Red",
        "Evidence": "The document does not mention data retention schedules or disposal procedures. No section addresses data lifecycle management."
      }
    ],
    "Final_Summary": {
      "Interpretation": "Minor gaps - needs remediation",
      "Overall_Comments": "The document demonstrates strong authentication controls but has notable gaps in data lifecycle management. The RBAC/ABAC model is a quick win — finalising business-role mapping and attribute sources would move this to Green."
    }
  }
}
```

#### Coverage Values

| Value | Meaning |
|-------|---------|
| `Green` | The document comprehensively addresses the requirement. Controls are defined, aligned with standards, and implementation is clear. |
| `Amber` | The document partially addresses the requirement. Core elements exist but gaps remain (e.g. pending sign-offs, incomplete coverage, missing automation). |
| `Red` | The document does not address the requirement. Significant gaps, missing controls, or only aspirational statements without implementation detail. |

#### Final Summary Interpretation Values

| Value |
|-------|
| `Strong alignment` |
| `Minor gaps - needs remediation` |
| `Significant risk - requires major revision` |

---

### 2.2 Parsed AgentResult

After Claude's response is received, the raw JSON is validated into Pydantic models (`AgentResult`). The structure is flattened and enriched with API response metadata:

```json
{
  "assessments": [
    {
      "Question": "Is authentication defined (SSO, OAuth2, Azure AD, MFA)?",
      "Coverage": "Green",
      "Evidence": "Authentication is fully defined in Section 3.1..."
    },
    {
      "Question": "Are authorisation models clear (RBAC, ABAC)?",
      "Coverage": "Amber",
      "Evidence": "Section 3.2 defines RBAC as the backbone..."
    },
    {
      "Question": "Are data retention and disposal policies defined?",
      "Coverage": "Red",
      "Evidence": "The document does not mention data retention schedules..."
    }
  ],
  "final_summary": {
    "Interpretation": "Minor gaps - needs remediation",
    "Overall_Comments": "The document demonstrates strong authentication controls..."
  },
  "metadata": {
    "model": "claude-opus-4-6",
    "input_tokens": 1842,
    "output_tokens": 312,
    "stop_reason": "end_turn"
  }
}
```

---

## Part 3 — Schema Reference

Defined in `src/agents/schemas.py`:

### `AssessmentRow`

| Field | Type | Description |
|-------|------|-------------|
| `Question` | `str` | The checklist question being evaluated |
| `Coverage` | `str` | One of `"Green"`, `"Amber"`, or `"Red"` |
| `Evidence` | `str` | Evidence or rationale cited from the assessed document |

### `FinalSummary`

| Field | Type | Description |
|-------|------|-------------|
| `Interpretation` | `str` | One of the three interpretation band values |
| `Overall_Comments` | `str` | Summary of key gaps or strengths across all questions |

### `LLMResponseMeta`

| Field | Type | Description |
|-------|------|-------------|
| `model` | `str` | The Claude model used |
| `input_tokens` | `int` | Number of input tokens consumed |
| `output_tokens` | `int` | Number of output tokens generated |
| `stop_reason` | `str \| None` | API stop reason (e.g. `"end_turn"`) |

### `AgentResult`

| Field | Type | Description |
|-------|------|-------------|
| `assessments` | `list[AssessmentRow]` | One entry per checklist question |
| `metadata` | `LLMResponseMeta` | API response metadata |
| `final_summary` | `FinalSummary \| None` | Overall summary (optional) |

---

## Part 4 — Key Differences: Raw vs Parsed

| | Raw LLM Output | `AgentResult` (Parsed) |
|---|---|---|
| **Structure** | Nested under `Security.Assessments` / `Security.Final_Summary` | Flattened to `assessments` / `final_summary` |
| **Metadata** | Not present | Added from Anthropic API response headers |
| **Validation** | None | Enforced by Pydantic |
