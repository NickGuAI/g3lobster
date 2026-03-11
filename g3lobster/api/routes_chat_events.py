"""Google Chat interaction event handler (webhook)."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat-events"])


@router.post("/chat/events")
async def handle_chat_event(request: Request) -> JSONResponse:
    """Handle Google Chat interaction events.

    Google Chat sends these when a user @mentions the bot or
    interacts with it.  We acknowledge immediately to suppress
    the "not responding" message, then let the poll loop handle
    the actual processing.
    """
    body = await request.json()
    event_type = body.get("type", "UNKNOWN")
    logger.info("Chat event received: type=%s", event_type)

    if event_type == "ADDED_TO_SPACE":
        space_name = body.get("space", {}).get("displayName", "unknown")
        return JSONResponse({"text": f"Hello! I've joined {space_name}."})

    if event_type == "MESSAGE":
        # Acknowledge the message -- the poll loop will process it.
        # Returning empty JSON suppresses "not responding".
        return JSONResponse({})

    if event_type == "CARD_CLICKED":
        return _handle_card_clicked(body)

    if event_type == "REMOVED_FROM_SPACE":
        logger.info("Bot removed from space")
        return JSONResponse({})

    # Default: acknowledge unknown events.
    return JSONResponse({})


def _handle_card_clicked(body: dict) -> JSONResponse:
    """Handle a card button click from /quick action card.

    Extracts the prompt from the button parameters and returns it
    as a text response, which Google Chat will display as a message
    from the bot. The poll loop will then pick up any @-mention
    messages naturally.
    """
    action = body.get("action", {}) or body.get("common", {}).get("invokedFunction", {})
    parameters = action.get("parameters", [])

    param_map = {p["key"]: p["value"] for p in parameters if "key" in p and "value" in p}
    prompt = param_map.get("prompt", "")
    action_name = param_map.get("action", "unknown")

    logger.info("Card clicked: action=%s, prompt=%s", action_name, prompt[:50])

    if prompt:
        return JSONResponse({"text": prompt})

    return JSONResponse({"text": "Action received."})
