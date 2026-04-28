import os
import sys
import json
import uuid
import requests
import msal
import psycopg2
from urllib.parse import urlparse
from psycopg2.extras import RealDictCursor

from datetime import datetime, timezone
from dotenv import load_dotenv
from anthropic import AnthropicBedrock

# =========================================================
# 1. Load environment variables
# =========================================================
load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")

REGION = os.getenv("AWS_DEFAULT_REGION")
MODEL_ID = os.getenv("MODEL_ID")

TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID")
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/.default"]


# =========================================================
# 0. Utility: URL parsing + DB access + helpers
# =========================================================
def extract_sharepoint_parts(url: str):
    parsed = urlparse(url)

    hostname = parsed.hostname  # e.g., defra.sharepoint.com
    parts = parsed.path.split("/")  # ['', 'teams', 'Team3221', 'SitePages', '...']

    if "teams" in parts:
        idx = parts.index("teams")
    elif "sites" in parts:
        idx = parts.index("sites")
    else:
        raise ValueError(f"Cannot determine site root for URL: {url}")

    # /teams/Team3221 or /sites/SiteName
    site_path = "/" + "/".join(parts[idx:idx + 2])

    return hostname, site_path


def fetch_policy_sources():
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            connect_timeout=2
        )
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT url_id, url, desp, category, type, isactive, datasize
                FROM aia_app.source_path_policydoc
                WHERE isactive = TRUE;
            """)
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print("Connection failed:", e)
        return []


def new_uuid():
    return str(uuid.uuid4())


def timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# =========================================================
# 2. SharePoint Client Class
# =========================================================
class SharePointClient:
    def __init__(self, tenant_id, client_id, client_secret, scopes, authority):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.authority = authority

    def get_access_token(self):
        app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
        )

        result = app.acquire_token_for_client(scopes=self.scopes)

        if "access_token" not in result:
            print("Failed to acquire access token")
            print(json.dumps(result, indent=2))
            sys.exit(1)

        return result["access_token"]

    def read_site_content(self, url):
        token = self.get_access_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }

        #print("Accessing SharePoint site via Microsoft Graph...")
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            raise requests.exceptions.RequestException(
                f"SharePoint API error: {response.text}"
            )

        data = response.json()
        title = data.get("title", "") or data.get("Title", "")
        description = data.get("description", "") or data.get("Description", "")
        
        return f"{title}\n{description}"


# =========================================================
# 3. Claude Evaluation Class
# =========================================================
class ClaudeEvaluator:
    SYSTEM_PROMPT = (
        "You are an expert policy compliance analyst. "
        "Your sole job is to read a policy document and produce evaluation "
        "questions that help validate whether another document adheres to it. "
        "You MUST respond with valid JSON only — no markdown fences, "
        "no preamble, no commentary. Any deviation from pure JSON will "
        "cause a downstream parsing failure."
    )

    def __init__(self):
        self.model_id = MODEL_ID
        self.client = AnthropicBedrock(
            aws_access_key=AWS_ACCESS_KEY_ID,
            aws_secret_key=AWS_SECRET_ACCESS_KEY,
            aws_session_token=AWS_SESSION_TOKEN,
            aws_region=REGION
        )

    def generate_questions(self, policy_url, site_content):
        root_uuid = new_uuid()
        generated_at = timestamp()

        prompt = f"""
Below is the full text of a policy document retrieved from:
{policy_url}

--- POLICY CONTENT START ---
{site_content}
--- POLICY CONTENT END ---

Your task:
1. Read and interpret the policy content above.
2. Generate evaluation questions that help validate whether another document
   adheres to this policy.
3. For each question include:
   - A UUID (generate a new one per question)
   - A clear, specific question
   - A reference to the relevant page or section in the policy
   - A short excerpt from the policy that the question is based on
   - This exact timestamp: {generated_at}

Return ONLY a JSON object in this exact structure (no markdown, no extra text):

{{
  "uuid": "{root_uuid}",
  "url": "{policy_url}",
  "category": "Security",
  "generated_at": "{generated_at}",
  "details": [
    {{
      "uuid": "<generate-a-uuid>",
      "question": "<specific compliance question>",
      "reference": "<page or section>",
      "source_excerpt": "<verbatim short excerpt from the policy>",
      "timestamp": "{generated_at}"
    }}
  ]
}}
"""

        response = self.client.messages.create(
            model=self.model_id,
            max_tokens=2000,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text

        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```", 2)[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.rsplit("```", 1)[0].strip()

        return clean


# =========================================================
# 4. Main Application Class
# =========================================================
class EvaluationApp:
    def __init__(self):
        self.sp_client = SharePointClient(
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
            authority=AUTHORITY
        )
        self.claude = ClaudeEvaluator()

    def run(self):
        #print("Fetching policy URLs from database...")
        policies = fetch_policy_sources()

        if not policies:
            print("No active policy URLs found in database.")
            return

        for policy in policies:
            policy_url = policy["url"]
            print(f"\n=== Scanning Policy: {policy_url} ===")

            try:
                hostname, site_path = extract_sharepoint_parts(policy_url)
            except ValueError as e:
                print(f"Skipping URL {policy_url}: {e}")
                continue

            site_lookup_url = (
                f"https://graph.microsoft.com/v1.0/sites/"
                f"{hostname}:{site_path}"
            )

            
            try:
                site_content = self.sp_client.read_site_content(site_lookup_url)
            except Exception as e:
                print(f"Failed to read SharePoint content for {policy_url}: {e}")
                continue

            questions_json = self.claude.generate_questions(policy_url, site_content)

            print("\n=== Generated Questions for this Policy ===\n")
            print(questions_json)

            try:
                parsed = json.loads(questions_json)
                print(f"✓ Valid JSON — {len(parsed.get('details', []))} question(s) generated.")
            except json.JSONDecodeError as e:
                print(f"✗ JSON parsing failed: {e}")
                continue


# =========================================================
# 5. Entry Point
# =========================================================
if __name__ == "__main__":
    app = EvaluationApp()
    app.run()