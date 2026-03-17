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
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

WORKSPACE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
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


def get_workspace_credentials(data_dir: Optional[str] = None):
    """Load and validate credentials with workspace (Drive/Docs/Sheets) scopes.

    Uses the same token file as Chat auth. If the token lacks workspace
    scopes, the user must re-authenticate with the expanded scope set.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = token_path(data_dir)
    if not token.exists():
        raise RuntimeError("OAuth token missing. Complete setup auth first.")

    creds = Credentials.from_authorized_user_file(str(token), WORKSPACE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")

    if not creds or not creds.valid:
        raise RuntimeError(
            "OAuth token is invalid or missing workspace scopes. "
            "Re-run setup auth with Drive/Docs/Sheets scopes."
        )

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
    state_data = {"state": state}
    if getattr(flow, "code_verifier", None):
        state_data["code_verifier"] = flow.code_verifier
    state_file.write_text(json.dumps(state_data), encoding="utf-8")
    return auth_url


def complete_authorization(data_dir: Optional[str], code: str) -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Accept full redirect URL — extract code query parameter
    if code.strip().startswith(("http://", "https://")):
        import re
        match = re.search(r'(?:[?&]|&amp;)code=([^&#]+)', code.strip())
        if not match:
            raise ValueError(f"URL does not contain a 'code' parameter. Ensure you copied the full redirect URL correctly. Received: {code[:80]}...")
        code = match.group(1)

    creds_path = credentials_path(data_dir)
    if not creds_path.exists():
        raise FileNotFoundError(f"Credentials not found at {creds_path}")

    state = None
    state_file = oauth_state_path(data_dir)
    if state_file.exists():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            state = state_data.get("state")
            code_verifier = state_data.get("code_verifier")
        except json.JSONDecodeError:
            state = None
            code_verifier = None
    else:
        code_verifier = None

    flow = InstalledAppFlow.from_client_secrets_file(
        str(creds_path),
        SCOPES,
        redirect_uri="http://localhost",
        state=state,
    )
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(code=code.strip())

    token = token_path(data_dir)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(flow.credentials.to_json(), encoding="utf-8")

    if state_file.exists():
        state_file.unlink()

    return token


def get_authenticated_service(data_dir: Optional[str] = None, timeout: float = 30.0):
    """Authenticate and return a Google Chat API service client.

    Uses google_auth_httplib2.AuthorizedHttp with an explicit socket timeout
    so that send/update API calls do not hang indefinitely when the Chat API
    is slow or unreachable.
    """
    import httplib2
    import google_auth_httplib2
    from googleapiclient.discovery import build

    creds = _load_saved_credentials(data_dir=data_dir)
    authorized_http = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=timeout)
    )
    return build("chat", "v1", http=authorized_http, cache_discovery=False)


def get_authorized_session(data_dir: Optional[str] = None):
    """Return a requests-based AuthorizedSession for direct Google API calls.

    Preferred over httplib2 for long-lived polling loops because requests
    handles stale SSL connections (record-layer failures) gracefully.
    """
    from google.auth.transport.requests import AuthorizedSession

    creds = _load_saved_credentials(data_dir=data_dir)
    return AuthorizedSession(creds)


def get_calendar_service(data_dir: Optional[str] = None):
    """Authenticate and return a Google Calendar API service client."""
    from googleapiclient.discovery import build

    creds = _load_saved_credentials(data_dir=data_dir)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)
