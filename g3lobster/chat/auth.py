"""Google Chat OAuth helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

SCOPES = [
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.spaces",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    "https://www.googleapis.com/auth/chat.users.spacesettings",
]


def _base_dir(data_dir: Optional[str] = None) -> Path:
    return Path(data_dir or Path.home() / ".gemini_chat_bridge")


def credentials_path(data_dir: Optional[str] = None) -> Path:
    return _base_dir(data_dir) / "credentials.json"


def token_path(data_dir: Optional[str] = None) -> Path:
    return _base_dir(data_dir) / "token.json"


def oauth_state_path(data_dir: Optional[str] = None) -> Path:
    return _base_dir(data_dir) / "oauth_state.json"


def credentials_exist(data_dir: Optional[str] = None) -> bool:
    return credentials_path(data_dir).exists()


def token_exists(data_dir: Optional[str] = None) -> bool:
    return token_path(data_dir).exists()


def save_credentials_json(payload: dict, data_dir: Optional[str] = None) -> Path:
    path = credentials_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _load_saved_credentials(data_dir: Optional[str] = None):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = token_path(data_dir)
    if not token.exists():
        raise RuntimeError("OAuth token missing. Complete setup auth first.")

    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")

    if not creds or not creds.valid:
        raise RuntimeError("OAuth token is invalid. Re-run setup auth.")

    return creds


def create_authorization_url(data_dir: Optional[str] = None) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path = credentials_path(data_dir)
    if not creds_path.exists():
        raise FileNotFoundError(f"Credentials not found at {creds_path}")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(creds_path),
        SCOPES,
        redirect_uri="http://localhost",
    )
    auth_url, state = flow.authorization_url(prompt="consent")

    state_file = oauth_state_path(data_dir)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"state": state}), encoding="utf-8")
    return auth_url


def complete_authorization(data_dir: Optional[str], code: str) -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path = credentials_path(data_dir)
    if not creds_path.exists():
        raise FileNotFoundError(f"Credentials not found at {creds_path}")

    state = None
    state_file = oauth_state_path(data_dir)
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8")).get("state")
        except json.JSONDecodeError:
            state = None

    flow = InstalledAppFlow.from_client_secrets_file(
        str(creds_path),
        SCOPES,
        redirect_uri="http://localhost",
        state=state,
    )
    flow.fetch_token(code=code.strip())

    token = token_path(data_dir)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(flow.credentials.to_json(), encoding="utf-8")

    if state_file.exists():
        state_file.unlink()

    return token


def get_authenticated_service(data_dir: Optional[str] = None):
    """Authenticate and return a Google Chat API service client."""
    from googleapiclient.discovery import build

    creds = _load_saved_credentials(data_dir=data_dir)
    return build("chat", "v1", credentials=creds, cache_discovery=False)
