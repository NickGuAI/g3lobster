"""Google Chat interaction event handler (webhook)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat-events"])

_CHAT_SERVICE_ACCOUNT = "chat@system.gserviceaccount.com"
_CHAT_CERTS_URL = (
    "https://www.googleapis.com/service_accounts/v1/metadata/x509/" + _CHAT_SERVICE_ACCOUNT
)


def _resolve_expected_audience(request: Request) -> str:
    configured = str(
        getattr(getattr(request.app.state.config, "chat", object()), "interaction_audience", "") or ""
    ).strip()
    if configured:
        return configured
    return str(request.url.replace(query=""))


def _extract_bearer_token(authorization_header: str | None) -> str:
    if not authorization_header:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def _verify_google_chat_bearer(token: str, audience: str) -> None:
    try:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import id_token
    except ImportError as import_error:
        raise HTTPException(
            status_code=500,
            detail="google-auth is required for chat event verification",
        ) from import_error

    request = GoogleAuthRequest()

    try:
        id_token.verify_oauth2_token(token, request, audience=audience)
        return
    except Exception as oauth_error:
        logger.debug("OAuth2 token verification failed, trying Chat certs: %s", oauth_error)

    try:
        claims: Dict[str, Any] = id_token.verify_token(
            token,
            request,
            audience=audience,
            certs_url=_CHAT_CERTS_URL,
        )
    except Exception as chat_error:
        raise HTTPException(
            status_code=401,
            detail="Invalid Google Chat bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from chat_error

    issuer = str(claims.get("iss", "")).strip()
    email = str(claims.get("email", "")).strip()
    if issuer != _CHAT_SERVICE_ACCOUNT or email != _CHAT_SERVICE_ACCOUNT:
        raise HTTPException(
            status_code=401,
            detail="Invalid Google Chat token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _authenticate_request(request: Request) -> None:
    token = _extract_bearer_token(request.headers.get("authorization"))
    audience = _resolve_expected_audience(request)
    _verify_google_chat_bearer(token, audience)


@router.post("/events")
async def handle_chat_event(request: Request) -> JSONResponse:
    """Acknowledge Google Chat interaction events quickly.

    Google Chat expects a synchronous HTTP response for mentions and other
    interaction events. Returning a 200 response suppresses "not responding".
    Actual message processing remains in the polling bridge.
    """
    _authenticate_request(request)

    body = await request.json()
    event_type = body.get("type", "UNKNOWN")
    logger.info("Chat event received: type=%s", event_type)

    if event_type == "ADDED_TO_SPACE":
        space_name = body.get("space", {}).get("displayName", "this space")
        return JSONResponse({"text": f"Hello! I've joined {space_name}."})

    if event_type in {"MESSAGE", "REMOVED_FROM_SPACE"}:
        return JSONResponse({})

    return JSONResponse({})
