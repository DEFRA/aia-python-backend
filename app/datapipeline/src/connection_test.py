import os
import sys
import json
import requests
import msal
import boto3
import psycopg2

from dotenv import load_dotenv

load_dotenv()

# -------------------------------

# CONFIGURATION (FILL THESE)

# -------------------------------
# AWS credentials:
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")


# SharePoint app registration details:
TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID")
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")
SHAREPOINT_SITE_ID = ""

# PostgreSQL details:
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")


# SharePoint site you were granted access to

# Example:

# https://defra.sharepoint.com/teams/Team3182

SHAREPOINT_HOSTNAME = "defra.sharepoint.com"
SITE_PATH = "/teams/Team3182"

# ------------------------------

# AUTH SETTINGS

# ------------------------------

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/.default"]

SITE_LOOKUP_URL = (
    f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOSTNAME}:{SITE_PATH}"
)

# ------------------------------

# TOKEN ACQUISITION

# ------------------------------


def get_access_token():
    print("000000000000000")
    app = msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )

    print("1111111111111")
    result = app.acquire_token_for_client(scopes=SCOPES)
    print("22222222222222")

    if "access_token" not in result:
        print("❌ Failed to acquire access token")
        print(json.dumps(result, indent=2))
        sys.exit(1)

    print("✅ Access token acquired")
    return result["access_token"]


# -----------------------------
# Connect to AWS (boto3 session)
# -----------------------------
def get_boto3_session():
    return boto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        aws_session_token=AWS_SESSION_TOKEN,
    )


# AWS Connectivity test:


def test_aws_connection(session):
    try:
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        print("Connected to AWS")
        print("Account:", identity["Account"])
        print("ARN:", identity["Arn"])
        return True
    except Exception as e:
        print("Failed to connect to AWS:", str(e))
        return False


# ------------------------------

# SHAREPOINT CONNECTIVITY TEST

# ------------------------------


def test_sharepoint_connectivity(access_token):
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    print("🌐 Resolving SharePoint site via Microsoft Graph...")
    response = requests.get(SITE_LOOKUP_URL, headers=headers)
    if response.status_code == 200:
        site = response.json()
        print("✅ Successfully connected to SharePoint")
        print("------------------------------------------------")
        print("Site ID      :", site.get("id"))
        print("Display Name :", site.get("displayName"))
        print("Web URL      :", site.get("webUrl"))
        print("------------------------------------------------")
        return site["id"]
    else:
        print("❌ SharePoint access failed")
        print("Status Code:", response.status_code)
        print("Response:")
        print(json.dumps(response.json(), indent=2))
        sys.exit(1)


# ------------------------------

# RDS POSTGRESQL CONNECTIVITY TEST


# ------------------------------
def test_postgres_connection():
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            connect_timeout=2,  # seconds
        )
        conn.close()
        return True
    except Exception as e:
        print("Connection failed:", e)
        return False


# ------------------------------

# MAIN

# ------------------------------

if __name__ == "__main__":
    # AWS session
    session = get_boto3_session()
    if test_aws_connection(session):
        print("AWS session is valid and authenticated")
    else:
        print("AWS session is NOT valid")

    print("🔐 Authenticating with Azure AD...")
    token = get_access_token()
    print("🔎 Testing SharePoint connectivity...")
    site_id = test_sharepoint_connectivity(token)
    print("🔌 Testing PostgreSQL connectivity...")
    if test_postgres_connection():
        print("Successfully connected to PostgreSQL database")
    else:
        print("Failed to connect to PostgreSQL database")

    print("🎉 Connectivity test completed successfully")
