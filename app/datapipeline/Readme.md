
Overview:

The AIA Policy Evaluation Data Pipeline automates the extraction, analysis, and storage of policy‑based evaluation questions. These questions are later used to validate whether internal documents comply with organisational policies.
The pipeline is fully serverless and event‑driven, using AWS EventBridge, AWS Lambda, PostgreSQL, Microsoft Graph (SharePoint Online), and Anthropic Bedrock.

High‑Level Architecture
EventBridge Scheduler
        │
        ▼
AWS Lambda Function
        │
        ├── Fetch active policy URLs from PostgreSQL
        │
        ├── For each policy URL:
        │       ├── Parse SharePoint hostname + site path
        │       ├── Retrieve policy content via Microsoft Graph API
        │       ├── Generate evaluation questions using Anthropic Bedrock
        │       └── Store generated JSON results back into PostgreSQL
        │
        ▼
PostgreSQL (results stored)

Diagram:
                   ┌──────────────────────────────  ┐
                   │      EventBridge Scheduler     │
                   │  (Cron-based automated trigger)│
                   └───────────────┬──────────────  ┘
                                   │
                                   ▼
                     ┌────────────────────────┐
                     │      AWS Lambda        │
                     │  Policy Evaluation Job │
                     └─────────────┬──────────┘
                                   │
     ┌─────────────────────────────┼──────────────────────────────────────────┐
     │                             │                                          │
     ▼                             ▼                                          ▼
┌──────────────┐        ┌──────────────────────┐                   ┌──────────────────────┐
│ PostgreSQL   │        │ SharePoint URL       │                   │ Microsoft Graph API  │
│ (Source URLs)│        │ Parsing (Hostname &  │                   │ (SharePoint Content  │
│ aia_app.     │        │ Site Path Extraction)│                   │ Retrieval)           │
│ source_path_ │        └──────────────────────┘                   └──────────────────────┘
│ policydoc    │                    │                                          │
└───────┬──────┘                    │                                          │
        │                           ▼                                          │
        │                ┌──────────────────────┐                              │
        │                │ SharePoint Page      │                              │
        │                │ Content (Title, Desc)│                              │
        │                └───────────┬──────────┘                              │
        │                            │                                         │
        │                            ▼                                         │
        │               ┌──────────────────────────┐                           │
        │               │ Anthropic Bedrock (LLM)  │                           │
        │               │ Generates Evaluation     │                           │
        │               │ Questions (JSON Output)  │                           │
        │               └───────────┬──────────────┘                           │
        │                           │                                          │
        │                           ▼                                          │
        │               ┌──────────────────────────┐                           │
        └──────────────►│ PostgreSQL (Results)     │◄──────────────────────────┘
                        │ aia_app.policy_          │
                        │ evaluation_results       │
                        └──────────────────────────┘

Structure
policy-evaluator/
│
├── app/datapipeline/src
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── sharepoint.py
│   ├── evaluator.py
│   ├── utils.py
│   └── main.py
│
├── app/datapipeline/prompts/
│   └── policy_evaluation_prompt.md
│
├── app/datapipeline
├    └── requirements.txt
└    └── README.md

Pipeline Flow
1. Event Trigger
An AWS EventBridge Scheduler triggers the Lambda function at a configured interval (e.g., hourly, daily, weekly).
This ensures policy evaluations are refreshed automatically without manual intervention.

2. Lambda Execution
When invoked, the Lambda function performs the following steps:
2.1 Fetch Policy Source URLs
The Lambda connects to PostgreSQL and queries:

aia_app.source_path_policydoc

It retrieves all active policy URLs that need evaluation.
Each row contains:
- url_id
- url (SharePoint policy page)
- category
- type
- isactive
- datasize


3. SharePoint Content Retrieval
For each policy URL:
- The URL is parsed to extract:
- SharePoint hostname
- Site path (/teams/... or /sites/...)
- The Lambda calls Microsoft Graph API using:
- Client credentials (Tenant ID, Client ID, Client Secret)
- Scope: https://graph.microsoft.com/.default
- The Graph API returns:
- Page title
- Page description
- Additional metadata (if available)

This content becomes the input for the evaluation model.

4. Evaluation Question Generation (Anthropic Bedrock)
The pipeline uses Anthropic Bedrock to generate structured evaluation questions.
The model receives:
- The policy URL
- The extracted SharePoint content
- A strict JSON‑only system prompt
- A timestamp
- A root UUID for traceability

The model returns a JSON object containing:
- Policy metadata
- Generated evaluation questions
- References
- Source excerpts
- Timestamps
The output is validated to ensure it is valid JSON.

5. Store Results in PostgreSQL
The validated JSON is inserted into:

aia_app.policy_evaluation_results

Each row contains:
- id (UUID)
- policy_url
- category
- generated_at
- result_json (JSONB)
This allows downstream systems to query, analyse, or display the evaluation questions.

Key Features
✔ Fully Automated
No manual triggers required — EventBridge handles scheduling.
✔ Dynamic URL Handling
SharePoint URLs are not hardcoded; they are fetched from PostgreSQL and parsed dynamically.
✔ Robust SharePoint Integration
Uses Microsoft Graph API with OAuth2 client credentials.
✔ AI‑Driven Evaluation
Anthropic Bedrock generates structured, policy‑aligned evaluation questions.
✔ JSONB Storage
Results are stored in PostgreSQL as JSONB for flexible querying and indexing.
✔ Error Handling & Validation
- Invalid URLs are skipped
- SharePoint failures are caught and logged
- JSON output is validated before insertion

Environment Variables
The pipeline requires the following environment variables:

AWS / Bedrock
- AWS_ACCESS_KEY_ID
- AWS_SECRET_ACCESS_KEY
- AWS_SESSION_TOKEN
- AWS_DEFAULT_REGION
- MODEL_ID
SharePoint / Microsoft Graph
- SHAREPOINT_TENANT_ID
- SHAREPOINT_CLIENT_ID
- SHAREPOINT_CLIENT_SECRET

PostgreSQL
- DB_HOST
- DB_PORT
- DB_NAME
- DB_USER
- DB_PASSWORD

Database Tables
1. Source Policy Table
aia_app.source_path_policydoc
Stores policy URLs and metadata.
2. Evaluation Results Table
aia_app.policy_evaluation_results
Stores generated evaluation questions in JSONB format.




