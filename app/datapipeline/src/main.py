import os
import sys
import json
import uuid
import requests
import PyPDF2
import msal
from datetime import datetime, timezone
from dotenv import load_dotenv
from anthropic import Anthropic
from anthropic import AnthropicBedrock


# ---------------------------------------------------------
# 1. Load environment variables and configuration
# ---------------------------------------------------------
load_dotenv()

# AWS config:
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")

# LLM / Bedrock config:
REGION = os.getenv("AWS_DEFAULT_REGION")
MODEL_ID = os.getenv("MODEL_ID")

# SharePoint config:
TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID")
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")
#SHAREPOINT_SITE = "https://defra.sharepoint.com/teams/Team3182"
#SITE_PATH = "/teams/Team3182"
SHAREPOINT_SITE = "https://defra.sharepoint.com/teams/Team3221/SitePages/Strategic-Architecture-Principles.aspx"
SITE_PATH = "/teams/Team3221"
SHAREPOINT_HOSTNAME = "defra.sharepoint.com"


AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/.default"]
SITE_LOOKUP_URL = (
    f"https://graph.microsoft.com/v1.0/sites/"
    f"{SHAREPOINT_HOSTNAME}:{SITE_PATH}"
)

# ---------------------------------------------------------
# 2. Acquire SharePoint Access Token using MSAL
# ---------------------------------------------------------
def get_sharepoint_token():
    
    app = msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )
 
    result = app.acquire_token_for_client(scopes=SCOPES) 
   
    if "access_token" not in result:
        print("❌ Failed to acquire access token")
        print(json.dumps(result, indent=2))
        sys.exit(1)
 
    print("Access token acquired...")
    return result["access_token"]

# ---------------------------------------------------------
# 3. Read SharePoint site content (example: site title)
# ---------------------------------------------------------
def read_sharepoint_content():
    access_token = get_sharepoint_token()  

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    } 
    print("Accessing SharePoint site via Microsoft Graph...")
    response = requests.get(SITE_LOOKUP_URL, headers=headers) 


    if response.status_code != 200:
        raise requests.exceptions.RequestException(f"SharePoint API error: {response.text}")

    data = response.json()

    # Combine title + description as sample content
    title = data.get("Title", "")
    description = data.get("Description", "")

    return f"Site Title: {title}\nDescription: {description}"


# ---------------------------------------------------------
# 4. Generate evaluation questions using Claude
# ---------------------------------------------------------
def generate_questions_with_claude(url):
    client = AnthropicBedrock()

    # ---------------------------------------------
    # Prompt engineering - crafting a clear, specific prompt for question generation
    # ---------------------------------------------
    prompt = f"""
    You are an AI that generates evaluation questions for validating documents 
    against a reference policy. The reference policy is located at:

    {SHAREPOINT_SITE}

    Your task:
    1. Read and interpret the policy content.
    2. Generate evaluation questions that help validate whether another document 
    adheres to this policy.
    3. For each question, include:
    - A UUID
    - A question
    - A reference (page/section)
    - A short source excerpt
    - A timestamp (UTC)
    4. Output ONLY JSON in the following format:

    {{
    "uuid": "<root-uuid>",
    "url": "{SHAREPOINT_SITE}",
    "category": "Security",
    "details": [
        {{
        "uuid": "<uuid>",
        "question": "<question>",
        "reference": "<reference>",
        "source_excerpt": "<excerpt>",
        "timestamp": "<timestamp>"
        }}
    ]
    }}
    Ensure the questions are specific, policy‑aligned, and useful for validating 
    completeness, correctness, and compliance.
    """
    # ---------------------------------------------
    # Calling the Bedrock model with the crafted prompt
    # ---------------------------------------------
    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    evaluation_questions = response.content[0].text

    # ---------------------------------------------
    # Printing the generated questions in JSON format
    # ---------------------------------------------
    print("\n=== GENERATED QUESTIONS JSON ===\n")
    print(evaluation_questions )

# ---------------------------------------------------------
# 5. Generate a new UUID for each question
# ---------------------------------------------------------
def new_uuid():
    return str(uuid.uuid4())

# ---------------------------------------------------------
# 6. Generate timestamp
# ---------------------------------------------------------
def timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# ---------------------------------------------------------
# 7. Main execution
# ---------------------------------------------------------
if __name__ == "__main__":
    print("Reading SharePoint content...")
    sharepoint_text = read_sharepoint_content()
    
    print("Generating evaluation...")
    generate_questions_with_claude(SHAREPOINT_SITE)



