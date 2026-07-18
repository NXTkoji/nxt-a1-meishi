"""One-off script: run locally to mint a new Google OAuth refresh token
covering both People API and Drive API scopes. Reads GOOGLE_CLIENT_ID /
GOOGLE_CLIENT_SECRET from .env, opens a browser for consent, then prints
the refresh token to paste into .env as GOOGLE_REFRESH_TOKEN.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/drive",
]

if not settings.google_client_id or not settings.google_client_secret:
    sys.exit("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env before running this script.")

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    },
    scopes=SCOPES,
)
creds = flow.run_local_server(port=0)
print("Refresh token:", creds.refresh_token)
