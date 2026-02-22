"""Google Chat interaction event handler (webhook)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat-events"])

GOOGLE_CHAT_SERVICE_ACCOUNT = "chat@system.gserviceaccount.com"
GOOGLE_CHAT_SERVICE_ACCOUNT_CERTS_URL = (
    "https://www.googleapis.com/service_accounts/v1/metadata/x509/chat@system.gserviceaccount.com"
)


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization bearer token")
    return token.strip()


def _resolve_audience(request: Request) -> str:
    configured = getattr(request.app.state.config.chat, "event_auth_audience", None)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return str(request.url)


def _verify_google_chat_bearer_token(token: str, audience: str) -> Dict[str, Any]:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import id_token

    auth_request = GoogleAuthRequest()
    claims: Dict[str, Any]

    try:
        claims = id_token.verify_oauth2_token(token, auth_request, audience=audience)
    except Exception:
        try:
            claims = id_token.verify_token(
                token,
                auth_request,
                audience=audience,
                certs_url=GOOGLE_CHAT_SERVICE_ACCOUNT_CERTS_URL,
            )
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Invalid Google Chat bearer token") from exc

    if claims.get("email") == GOOGLE_CHAT_SERVICE_ACCOUNT:
        return claims
    if claims.get("iss") == GOOGLE_CHAT_SERVICE_ACCOUNT:
        return claims

    raise HTTPException(status_code=401, detail="Bearer token is not from Google Chat")


@router.post("/chat/events")
async def handle_chat_event(request: Request) -> JSONResponse:
    """Acknowledge Google Chat interaction events immediately."""
    token = _extract_bearer_token(request)
    audience = _resolve_audience(request)
    _ = _verify_google_chat_bearer_token(token, audience)

    body = await request.json()
    event_type = body.get("type", "UNKNOWN")
    logger.info("Chat event received: type=%s", event_type)

    if event_type == "ADDED_TO_SPACE":
        space_name = body.get("space", {}).get("displayName", "unknown")
        return JSONResponse({"text": f"Hello! I've joined {space_name}."})

    if event_type == "MESSAGE":
        return JSONResponse({})

    if event_type == "REMOVED_FROM_SPACE":
        logger.info("Bot removed from space")
        return JSONResponse({})

    return JSONResponse({})
