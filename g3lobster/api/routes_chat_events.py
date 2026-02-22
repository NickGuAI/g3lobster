"""Google Chat interaction event handler (webhook)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat-events"])


@router.post("/chat/events")
async def handle_chat_event(request: Request) -> JSONResponse:
    """Acknowledge Google Chat interaction events immediately."""
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
