import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import msal
import psycopg2
import requests
from anthropic import AnthropicBedrock
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


# =========================================================
# Logging configuration
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =========================================================
# Environment & configuration
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
# Utility functions
# =========================================================
def extract_sharepoint_parts(url: str) -> Tuple[str, str]:
    """
    Extract SharePoint hostname and site path from a URL.

    Example:
        https://defra.sharepoint.com/teams/Team3221/SitePages/...
        -> ("defra.sharepoint.com", "/teams/Team3221")
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL has no hostname: {url}")

    parts = parsed.path.split("/")  # ['', 'teams', 'Team3221', 'SitePages', '...']

    if "teams" in parts:
        idx = parts.index("teams")
    elif "sites" in parts:
        idx = parts.index("sites")
    else:
        raise ValueError(f"Cannot determine site root for URL: {url}")

    site_path = "/" + "/".join(parts[idx:idx + 2])
    return hostname, site_path


def new_uuid() -> str:
    return str(uuid.uuid4())


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# =========================================================
# Data access
# =========================================================
@dataclass
class PolicySource:
    url_id: int
    url: str
    desp: str
    category: str
    type: str
    isactive: bool
    datasize: int


def fetch_policy_sources() -> List[Dict[str, Any]]:
    """
    Fetch active policy sources from the database.
    Returns a list of dict rows (RealDictCursor).
    """
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            connect_timeout=2,
        )
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT url_id, url, desp, category, type, isactive, datasize
                FROM aia_app.source_path_policydoc
                WHERE isactive = TRUE;
                """
            )
            rows = cur.fetchall()
        logger.info("Fetched %d active policy source(s) from database.", len(rows))
        return rows
    except Exception as exc:
        logger.error("Database connection or query failed: %s", exc, exc_info=True)
        return []
    finally:
        if conn is not None:
            conn.close()


# =========================================================
# SharePoint client
# =========================================================
class SharePointClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scopes: List[str],
        authority: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.authority = authority

    def _create_app(self) -> msal.ConfidentialClientApplication:
        return msal.ConfidentialClientApplication(
            client_id=self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
        )

    def get_access_token(self) -> str:
        app = self._create_app()
        result = app.acquire_token_for_client(scopes=self.scopes)

        if "access_token" not in result:
            logger.error("Failed to acquire access token: %s", json.dumps(result, indent=2))
            raise RuntimeError("Could not acquire access token from MSAL.")

        return result["access_token"]

    def read_site_content(self, url: str) -> str:
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        logger.info("Requesting SharePoint site content: %s", url)
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            logger.error("SharePoint API error (%s): %s", response.status_code, response.text)
            raise requests.exceptions.RequestException(
                f"SharePoint API error: {response.status_code} {response.text}"
            )

        data = response.json()
        title = data.get("title") or data.get("Title") or ""
        description = data.get("description") or data.get("Description") or ""

        return f"{title}\n{description}".strip()


# =========================================================
# Claude evaluation
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

    def __init__(self) -> None:
        if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, REGION, MODEL_ID]):
            raise RuntimeError("Missing required AWS/Anthropic configuration environment variables.")

        self.model_id = MODEL_ID
        self.client = AnthropicBedrock(
            aws_access_key=AWS_ACCESS_KEY_ID,
            aws_secret_key=AWS_SECRET_ACCESS_KEY,
            aws_session_token=AWS_SESSION_TOKEN,
            aws_region=REGION,
        )
        self.base_prompt = load_prompt("../prompts/policy_evaluation_prompt.md")

    def _build_prompt(self, policy_url: str, site_content: str, category: str) -> str:
        root_uuid = new_uuid()
        generated_at = timestamp()

        return f"""
{self.base_prompt}

---
Policy URL: {policy_url}
Timestamp: {generated_at}
Category: {category}

--- POLICY CONTENT START ---
{site_content}
--- POLICY CONTENT END ---

Return the JSON object using the structure defined in the prompt file.
Root UUID: {root_uuid}
""".strip()

    def generate_questions(self, policy_url: str, site_content: str, category: str) -> str:
        prompt = self._build_prompt(policy_url, site_content, category)

        logger.info("Calling Claude model for policy URL: %s", policy_url)
        response = self.client.messages.create(
            model=self.model_id,
            max_tokens=2000,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text
        clean = self._strip_markdown_fences(raw)
        return clean

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        clean = text.strip()
        if clean.startswith("```"):
            # Remove leading ``` and optional language tag
            parts = clean.split("```", 2)
            if len(parts) >= 2:
                clean = parts[1]
            if clean.lstrip().startswith("json"):
                clean = clean.lstrip()[4:]
            if "```" in clean:
                clean = clean.rsplit("```", 1)[0]
        return clean.strip()


# =========================================================
# Main application
# =========================================================
class EvaluationApp:
    def __init__(self) -> None:
        self.sp_client = SharePointClient(
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
            authority=AUTHORITY,
        )
        self.claude = ClaudeEvaluator()

    def run(self) -> None:
        policies = fetch_policy_sources()
        if not policies:
            logger.warning("No active policy URLs found in database.")
            return

        for policy in policies:
            policy_url = policy["url"]
            category = policy.get("category", "")
            logger.info("=== Scanning Policy: %s ===", policy_url)

            try:
                hostname, site_path = extract_sharepoint_parts(policy_url)
            except ValueError as exc:
                logger.warning("Skipping URL %s: %s", policy_url, exc)
                continue

            site_lookup_url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:{site_path}"

            try:
                site_content = self.sp_client.read_site_content(site_lookup_url)
            except Exception as exc:
                logger.error("Failed to read SharePoint content for %s: %s", policy_url, exc)
                continue

            try:
                questions_json = self.claude.generate_questions(policy_url, site_content, category)
            except Exception as exc:
                logger.error("Claude evaluation failed for %s: %s", policy_url, exc)
                continue

            logger.info("Generated questions JSON for policy %s:\n%s", policy_url, questions_json)

            try:
                parsed = json.loads(questions_json)
                details = parsed.get("details", [])
                logger.info("✓ Valid JSON — %d question(s) generated.", len(details))
            except json.JSONDecodeError as exc:
                logger.error("✗ JSON parsing failed for %s: %s", policy_url, exc)


# =========================================================
# Entry point
# =========================================================
def main() -> None:
    try:
        app = EvaluationApp()
        app.run()
    except Exception as exc:
        logger.critical("Fatal error in EvaluationApp: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
