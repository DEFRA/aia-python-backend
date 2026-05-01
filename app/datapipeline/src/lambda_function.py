import os
import json
import requests
import psycopg2
import psycopg2.extras
import boto3

# ---------- CONFIG ----------

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
BEDROCK_REGION = os.environ.get["BEDROCK_REGION"]
BEDROCK_MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"

TENANT_ID = os.environ["SHAREPOINT_TENANT_ID"]
CLIENT_ID = os.environ["SHAREPOINT_CLIENT_ID"]
CLIENT_SECRET = os.environ["SHAREPOINT_CLIENT_SECRET"]

# For client credentials scope; adjust if you use a different resource
SHAREPOINT_SCOPE = "https://graph.microsoft.com/.default"

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)


# ---------- DB HELPERS (psycopg2) ----------


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def fetch_document_row(conn):
    """
    Fetch one document that needs questions.
    Adjust WHERE clause as needed.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT id, sharepoint_url
            FROM documents
            WHERE questions IS NULL
            ORDER BY id
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"id": row["id"], "sharepoint_url": row["sharepoint_url"]}


def save_questions(conn, doc_id, questions_json):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE documents
            SET questions = %s
            WHERE id = %s
            """,
            [json.dumps(questions_json), doc_id],
        )
    conn.commit()


# ---------- SHAREPOINT HELPERS ----------


def get_sharepoint_access_token():
    """
    Client credentials flow to get an access token for Graph/SharePoint.
    """
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": SHAREPOINT_SCOPE,
        "grant_type": "client_credentials",
    }
    resp = requests.post(token_url, data=data)
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_sharepoint_content(url: str) -> str:
    """
    Fetch the document content from SharePoint.
    This assumes the URL is accessible via Graph/SharePoint with the token.
    You may need to adapt this depending on your URL pattern.
    """
    token = get_sharepoint_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()

    # If it's text (e.g. .txt, .md, HTML), resp.text is fine.
    # If it's a binary (PDF, DOCX), you’d need extra parsing.
    return resp.text


# ---------- BEDROCK / CLAUDE HELPER ----------


def generate_evaluation_questions(document_text: str):
    """
    Call Anthropic Claude Sonnet via Amazon Bedrock to generate evaluation questions.
    Returns a Python object suitable for JSONB storage.
    """
    prompt = f"""
You are an expert evaluator. Read the following document content and generate a structured set of evaluation questions that could be used to validate another document against this one.

Return the result strictly as JSON with this structure:

{{
  "questions": [
    {{
      "id": "q1",
      "text": "Question text here",
      "type": "open-ended | multiple-choice | yes-no",
      "criteria": "What this question is checking"
    }}
  ]
}}

Do not include any explanation outside the JSON.

Document content:
\"\"\"{document_text[:8000]}\"\"\"  # truncated for safety
"""

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "temperature": 0.2,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(payload),
        contentType="application/json",
        accept="application/json",
    )

    response_body = json.loads(response["body"].read())

    # Claude on Bedrock returns a list of content blocks
    text_parts = []
    for block in response_body.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))

    full_text = "\n".join(text_parts).strip()

    try:
        questions_json = json.loads(full_text)
    except json.JSONDecodeError:
        # Fallback: store raw text if model didn't return valid JSON
        questions_json = {"raw_output": full_text}

    return questions_json


# ---------- LAMBDA HANDLER ----------


def lambda_handler(event, context):
    conn = None
    try:
        conn = get_db_connection()

        doc_row = fetch_document_row(conn)
        if not doc_row:
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "No documents pending questions"}),
            }

        doc_id = doc_row["id"]
        sharepoint_url = doc_row["sharepoint_url"]

        # 1) Fetch content from SharePoint
        content = fetch_sharepoint_content(sharepoint_url)

        # 2) Generate evaluation questions via Claude Sonnet
        questions = generate_evaluation_questions(content)

        # 3) Save questions back into PostgreSQL as JSONB
        save_questions(conn, doc_id, questions)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Questions generated and saved",
                    "document_id": doc_id,
                    "questions_sample": questions,
                }
            ),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
    finally:
        if conn:
            conn.close()
