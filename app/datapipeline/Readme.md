# AIA Policy Evaluation Data Pipeline

Automates the extraction and storage of structured evaluation questions from Defra SharePoint policy pages. Questions are used downstream by the AIA assessment pipeline to evaluate design documents against organisational policies.

---

## Architecture

```
EventBridge Scheduler
        │
        ▼
AWS Lambda (data pipeline)
        │
        ├── Load policy source URLs
        │       ├── [default]  data_pipeline.source_path_policydoc (PostgreSQL)
        │       └── [flag]     data/policy_sources.json (local file)
        │
        ├── For each active policy URL:
        │       ├── Fetch page content via Microsoft Graph API (SharePoint)
        │       ├── Check change — skip if last_modified unchanged
        │       ├── Extract evaluation questions via Anthropic Bedrock (LLM)
        │       ├── [flag] Write debug file (URL + content + questions)
        │       └── Write to data_pipeline normalised tables (PostgreSQL)
        │
        ▼
PostgreSQL — data_pipeline schema
```

---

## Directory Structure

```
app/datapipeline/
├── data/
│   └── policy_sources.json          # Local policy source list (feature-flag mode)
├── debug/                           # Debug output files (git-ignored, SAVE_DEBUG_OUTPUT=true)
│   └── .gitignore
├── prompts/
│   └── policy_evaluation_prompt.md  # LLM system prompt (edit without touching Python)
├── src/
│   ├── __init__.py
│   ├── db.py                        # fetch_policy_sources, load_local_policy_sources,
│   │                                #   insert_policy_document, delete_questions_for_doc,
│   │                                #   insert_questions
│   ├── evaluator.py                 # QuestionExtractor — calls Anthropic Bedrock
│   ├── main.py                      # Pipeline orchestrator (run entry point)
│   ├── schemas.py                   # PolicySource, ExtractedQuestion, SyncRecord
│   ├── sharepoint.py                # SharePointClient — Microsoft Graph API
│   ├── sync.py                      # Change detection (get/upsert policy_document_sync)
│   ├── utils.py                     # url_to_hash, page_name_from_url, new_uuid
│   ├── lambda_function.py           # Lambda handler wrapper (do not modify)
│   └── tests/
│       ├── test_db.py
│       ├── test_evaluator.py
│       ├── test_main.py
│       ├── test_sharepoint.py
│       ├── test_sync.py
│       └── test_utils.py
├── requirements.txt
└── Readme.md
```

---

## Pipeline Flow

### 1. Trigger
AWS EventBridge Scheduler invokes the Lambda on a configured schedule (hourly / daily).

### 2. Load Policy Sources
Policy URLs are loaded from one of two sources, controlled by the `USE_LOCAL_POLICY_SOURCES` feature flag:

| Mode | Source |
|------|--------|
| `false` (default) | `data_pipeline.source_path_policydoc` in PostgreSQL |
| `true` | `data/policy_sources.json` bundled with the Lambda package |

Only rows/entries where `isactive = true` are processed.

### 3. SharePoint Content Retrieval
For each policy URL the pipeline makes two Graph API calls:

**Step 1 — resolve site ID**
```
GET /v1.0/sites/{hostname}:{site_path}
```
Returns the opaque `site_id` needed for the pages endpoint, plus site-level fallback metadata (title, description, `lastModifiedDateTime`).

**Step 2 — fetch page content (SitePages URLs only)**
```
GET /v1.0/sites/{site_id}/pages/microsoft.graph.sitePage
    ?$filter=name eq '{page.aspx}'&$expand=canvasLayout
```
Returns the full page body via `canvasLayout.horizontalSections[].columns[].webparts[].innerHtml`, HTML-stripped to plain text, along with the page-level `lastModifiedDateTime`.

**Fallback behaviour**

| Condition | Behaviour |
|-----------|-----------|
| URL is a document library or non-SitePages path | Uses site title + description (Step 1 only) |
| Pages API returns empty result | Falls back to site metadata |
| Pages API returns a non-200 status | Logs a warning, falls back to site metadata |
| Site API returns a non-200 status | Raises `RequestException` — pipeline marks URL as failed |

Page-level `lastModifiedDateTime` is used when available; site-level timestamp is the fallback.

### 4. Change Detection
Before calling the LLM the pipeline checks `data_pipeline.policy_document_sync`:
- If `last_modified` is unchanged since the last run → **skip** (no LLM call, no DB write)
- If changed or never synced → proceed

### 5. Question Extraction (Anthropic Bedrock)
The LLM receives the page content plus a category hint and returns a JSON array of `ExtractedQuestion` objects:

```json
[
  {
    "question_text": "Does the system encrypt data at rest?",
    "reference": "Section 3.2",
    "source_excerpt": "All data must be encrypted at rest using AES-256.",
    "categories": ["security"]
  }
]
```

The system prompt is loaded from `prompts/policy_evaluation_prompt.md` — edit the prompt there without touching Python code.

### 6. Debug Output (optional)
When `SAVE_DEBUG_OUTPUT=true`, the pipeline writes a plain-text file for each successfully processed URL **before** the DB write. This lets you inspect exactly what was fetched and what questions were generated without querying the database.

**File location:** `app/datapipeline/debug/` by default (override with `DEBUG_OUTPUT_DIR`).

**File name:** derived from the last URL path segment — e.g. `Strategic-Architecture-Principles.aspx.txt`.

**File format:**
```
=== SOURCE URL ===
https://defra.sharepoint.com/teams/Team3221/SitePages/Strategic-Architecture-Principles.aspx

=== RAW CONTENT ===
Strategic Architecture Principles

Policy text extracted from the SharePoint page canvas...

=== QUESTIONS GENERATED ===
[
  {
    "question_text": "Does the solution follow the defined architecture principles?",
    "reference": "Section 2",
    "source_excerpt": "All solutions must adhere to the strategic architecture principles.",
    "categories": ["technical"]
  }
]
```

The debug write is **best-effort** — if the file cannot be written (e.g. permissions, disk space) a warning is logged and the pipeline continues normally. The `debug/` directory is git-ignored so these files are never committed.

### 7. Persist to PostgreSQL
Results are written to the `data_pipeline` schema:

| Table | Purpose |
|-------|---------|
| `source_path_policydoc` | Source — active policy URLs (read only) |
| `policy_documents` | One row per unique policy URL processed |
| `questions` | Extracted questions linked to a policy document |
| `question_categories` | Junction table — question ↔ category mapping |
| `policy_document_sync` | Housekeeping — last-modified timestamp per URL |

**Re-run behaviour (idempotent):** when a changed page is processed, existing questions for that `policy_doc_id` are deleted before inserting the new set. This prevents stale questions from accumulating across runs. `question_categories` rows are removed automatically via `ON DELETE CASCADE`.

**Question-level `isactive` flag:** every question has an `isactive BOOLEAN NOT NULL DEFAULT TRUE` column. Set it to `false` to exclude a specific question from agent assessment runs without deleting it. The pipeline always inserts new questions as active; deactivation is a manual, deliberate action.

```sql
-- Deactivate a specific question
UPDATE data_pipeline.questions SET isactive = false WHERE question_id = '<uuid>';

-- Re-activate
UPDATE data_pipeline.questions SET isactive = true WHERE question_id = '<uuid>';
```

Agents querying for assessment questions must filter `WHERE isactive = true`.

---

## Environment Variables

### Required (always)

| Variable | Description |
|----------|-------------|
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port (default: `5432`) |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `SHAREPOINT_TENANT_ID` | Azure AD tenant ID |
| `SHAREPOINT_CLIENT_ID` | Azure AD app client ID |
| `SHAREPOINT_CLIENT_SECRET` | Azure AD app client secret |
| `AWS_DEFAULT_REGION` | AWS region for Bedrock |
| `MODEL_ID` | Bedrock model ID (e.g. `anthropic.claude-3-7-sonnet-20250219-v1:0`) |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_ACCESS_KEY_ID` | — | AWS access key (not needed when using IAM role) |
| `AWS_SECRET_ACCESS_KEY` | — | AWS secret key (not needed when using IAM role) |
| `AWS_SESSION_TOKEN` | — | AWS session token (temporary credentials) |
| `USE_LOCAL_POLICY_SOURCES` | `false` | Set to `true` to load policy URLs from the bundled JSON file instead of the database |
| `LOCAL_POLICY_SOURCES_PATH` | `data/policy_sources.json` | Override the local sources file path (used when `USE_LOCAL_POLICY_SOURCES=true`) |
| `SAVE_DEBUG_OUTPUT` | `false` | Set to `true` to write a per-URL debug file containing the source URL, raw content, and extracted questions |
| `DEBUG_OUTPUT_DIR` | `app/datapipeline/debug/` | Directory for debug files (used when `SAVE_DEBUG_OUTPUT=true`) |

---

## Local Sources File (`data/policy_sources.json`)

Used when `USE_LOCAL_POLICY_SOURCES=true`. Entries with `isactive: false` are skipped automatically.

```json
[
  {
    "url_id": 1,
    "url": "https://defra.sharepoint.com/teams/Team3221/SitePages/Strategic-Architecture-Principles.aspx",
    "desp": "Strategic Architecture Principles",
    "category": "technical",
    "type": "page",
    "isactive": true,
    "datasize": null
  }
]
```

Fields match the `data_pipeline.source_path_policydoc` schema. Query parameters are stripped from URLs (SharePoint `xsdata`/`sdata` tracking params are session-specific and not needed by the Graph API).

---

## Running Locally

```bash
pip install -r app/datapipeline/requirements.txt

# Copy and fill in required variables
cp .env.example .env

# Start local PostgreSQL via Podman (schema + seed data applied automatically)
./scripts/start-datapipeline-dev.sh

# Run with database sources (default — requires Podman DB running)
python -m app.datapipeline.src.main

# Run with local sources file (no DB read for source list)
USE_LOCAL_POLICY_SOURCES=true python -m app.datapipeline.src.main

# Run with debug output enabled — writes one .txt file per URL to app/datapipeline/debug/
SAVE_DEBUG_OUTPUT=true python -m app.datapipeline.src.main

# Combine both flags (useful for local inspection without a live DB)
USE_LOCAL_POLICY_SOURCES=true SAVE_DEBUG_OUTPUT=true python -m app.datapipeline.src.main

# Stop / remove the container when done
# Default container name is 'aiadocuments' (set DATAPIPELINE_CONTAINER to override)
podman stop aiadocuments
podman rm   aiadocuments
```

## Running Tests

```bash
pytest app/datapipeline/src/tests/ -v
pytest app/datapipeline/src/tests/ --cov=app/datapipeline/src --cov-report=term-missing
```

---

## Key Design Decisions

- **Normalised schema** — questions and categories are stored in separate tables rather than JSONB blobs, enabling indexed queries by category, policy document, or question text.
- **`url_hash` as sync key** — `policy_document_sync` uses a SHA-256 hex digest of the source URL as its primary key, avoiding issues with long URLs and special characters.
- **Change detection** — `lastModifiedDateTime` from Graph API is compared against the stored value; unchanged pages are skipped without calling the LLM.
- **Full page content via canvasLayout** — the pipeline fetches actual SharePoint page text through the Graph Pages API (`/pages/microsoft.graph.sitePage?$expand=canvasLayout`), not just site metadata. This yields the complete policy body for LLM extraction. Site metadata is used only as a fallback for non-SitePages URLs or when the pages API fails.
- **Idempotent question writes** — on a changed page, existing questions are deleted before the new set is inserted. Stale questions do not accumulate across re-runs.
- **Question-level `isactive` flag** — `questions.isactive` defaults to `true` for every inserted row. Operators can set individual questions to `false` to exclude them from agent assessment without losing the record. The pipeline never touches this column after insertion; deactivation is always a manual action. Agents must filter `WHERE isactive = true` when fetching questions.
- **Prompt in Markdown** — the LLM system prompt lives in `prompts/policy_evaluation_prompt.md` and is loaded at cold-start via `Path`, keeping it reviewable and editable outside Python code.
- **Feature flag for source list** — `USE_LOCAL_POLICY_SOURCES=true` allows the pipeline to run in development or test environments without a populated `source_path_policydoc` table.
- **Debug output flag** — `SAVE_DEBUG_OUTPUT=true` writes a plain-text file per URL (source URL + raw content + questions JSON) to `app/datapipeline/debug/`. The write is best-effort and never blocks the pipeline. Files are git-ignored. Intended for local inspection and troubleshooting only — never enable in production.
- **psycopg2 (sync)** — the Lambda uses synchronous psycopg2, appropriate for a single-threaded Lambda handler. The evaluation pipeline (ECS) uses asyncpg.
