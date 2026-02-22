"""Google Chat interaction event handler (webhook)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat-events"])


@router.post("/events")
async def handle_chat_event(request: Request) -> JSONResponse:
    """Acknowledge Google Chat interaction events quickly.

    Google Chat expects a synchronous HTTP response for mentions and other
    interaction events. Returning a 200 response suppresses "not responding".
    Actual message processing remains in the polling bridge.
    """
    body = await request.json()
    event_type = body.get("type", "UNKNOWN")
    logger.info("Chat event received: type=%s", event_type)

    if event_type == "ADDED_TO_SPACE":
        space_name = body.get("space", {}).get("displayName", "this space")
        return JSONResponse({"text": f"Hello! I've joined {space_name}."})

    if event_type in {"MESSAGE", "REMOVED_FROM_SPACE"}:
        return JSONResponse({})

    return JSONResponse({})
