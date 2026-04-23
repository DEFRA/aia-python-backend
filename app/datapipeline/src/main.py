import os
import json
import uuid
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import msal
from anthropic import Anthropic

# ---------------------------------------------------------
# 1. Load environment variables
# ---------------------------------------------------------
load_dotenv()

TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID")
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")

 
SHAREPOINT_SITE = "https://defra.sharepoint.com/teams/Team3182"
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-3-5-sonnet-20240620"   # Claude 4.6 equivalent model family


# ---------------------------------------------------------
# 2. Acquire SharePoint Access Token using MSAL
# ---------------------------------------------------------
def get_sharepoint_token():
    authority = f"https://login.microsoftonline.com/{TENANT_ID}"
    scope = ["https://graph.microsoft.com/.default"]

    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=authority,
        client_credential=CLIENT_SECRET
    )

    result = app.acquire_token_silent(scope, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=scope)

    if "access_token" not in result:
        raise Exception("Failed to obtain token: " + str(result))

    return result["access_token"]


# ---------------------------------------------------------
# 3. Read SharePoint site content (example: site title)
# ---------------------------------------------------------
def read_sharepoint_content():
    token = get_sharepoint_token()

    endpoint = f"{SHAREPOINT_SITE}/_api/web?$select=Title,Description"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;odata=nometadata"
    }

    response = requests.get(endpoint, headers=headers)

    if response.status_code != 200:
        raise Exception(f"SharePoint API error: {response.text}")

    data = response.json()

    # Combine title + description as sample content
    title = data.get("Title", "")
    description = data.get("Description", "")

    return f"Site Title: {title}\nDescription: {description}"


# ---------------------------------------------------------
# 4. Generate evaluation questions using Claude
# ---------------------------------------------------------
def generate_questions_with_claude(text, url):
    client = Anthropic(api_key=CLAUDE_API_KEY)

    system_prompt = (
        "You generate evaluation questions from policy or documentation text. "
        "Return STRICT JSON only. No commentary."
    )

    user_prompt = f"""
Generate evaluation questions from the following SharePoint content.

Each question must include:
- uuid
- question
- reference (infer a logical reference if unknown)
- source_excerpt
- timestamp (ISO format)

Return JSON in this structure:

{{
  "uuid": "<root-uuid>",
  "url": "{url}",
  "category": "SharePoint Evaluation",
  "details": [
    {{
      "uuid": "<question-uuid>",
      "question": "string",
      "reference": "string",
      "source_excerpt": "string",
      "timestamp": "ISO-8601"
    }}
  ]
}}

Here is the content:
{text}
"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        temperature=0.2,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    output = ""
    for block in response.content:
        if block.type == "text":
            output += block.text

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"error": "Claude returned invalid JSON", "raw_output": output}


# ---------------------------------------------------------
# 5. Main execution
# ---------------------------------------------------------
if __name__ == "__main__":
    print("Reading SharePoint content...")
    sharepoint_text = read_sharepoint_content()

    print("Generating evaluation questions with Claude...")
    result_json = generate_questions_with_claude(sharepoint_text, SHAREPOINT_SITE)

    print("\nFinal JSON Output:\n")
    print(json.dumps(result_json, indent=4))