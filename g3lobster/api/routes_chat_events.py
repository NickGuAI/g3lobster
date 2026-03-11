"""Google Chat interaction event handler (webhook)."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from g3lobster.chat.commands import QUICK_ACTION_PROMPTS

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
        return await _handle_card_clicked(request, body)

    if event_type == "REMOVED_FROM_SPACE":
        logger.info("Bot removed from space")
        return JSONResponse({})

    # Default: acknowledge unknown events.
    return JSONResponse({})


async def _handle_card_clicked(request: Request, body: dict) -> JSONResponse:
    """Handle a CARD_CLICKED interaction event from /quick action buttons."""
    action = body.get("action", {})
    function_name = action.get("actionMethodName") or action.get("function", "")
    params = {p["key"]: p["value"] for p in action.get("parameters", [])}

    # Also check the common.invokedFunction path used by newer Chat API versions
    common = body.get("common", {})
    if not function_name:
        function_name = common.get("invokedFunction", "")
    if not params and common.get("parameters"):
        params = common["parameters"]

    if function_name != "quick_action":
        logger.info("Unknown card function: %s", function_name)
        return JSONResponse({})

    action_key = params.get("action", "")
    prompt = QUICK_ACTION_PROMPTS.get(action_key)
    if not prompt:
        logger.warning("Unknown quick_action key: %s", action_key)
        return JSONResponse({"text": f"Unknown action: {action_key}"})

    # Route the prompt as a new message to the first available agent.
    # The user who clicked is the sender context.
    user = body.get("user", {})
    space = body.get("space", {})
    space_name = space.get("name", "")
    thread_name = body.get("message", {}).get("thread", {}).get("name")

    logger.info(
        "CARD_CLICKED quick_action=%s user=%s space=%s",
        action_key,
        user.get("displayName", "unknown"),
        space_name,
    )

    # Try to find the bridge for this space and submit the prompt as a task.
    registry = getattr(request.app.state, "registry", None)
    bridge_manager = getattr(request.app.state, "bridge_manager", None)

    if registry:
        # Find the first enabled agent and submit the prompt
        personas = registry.list_enabled_personas()
        if personas:
            target_id = personas[0].id
            runtime = registry.get_agent(target_id)
            if not runtime:
                started = await registry.start_agent(target_id)
                if started:
                    runtime = registry.get_agent(target_id)

            if runtime:
                from g3lobster.tasks.types import Task

                user_id = user.get("name", "unknown")
                thread_safe = (thread_name or "no-thread").replace("/", "_")
                session_id = f"{space_name}__{user_id}__{thread_safe}"
                task = Task(prompt=prompt, session_id=session_id)

                # Fire-and-forget: assign the task and let the bridge handle streaming
                asyncio.create_task(_run_quick_action_task(
                    runtime, task, registry, bridge_manager, space_name, thread_name,
                ))

    # Acknowledge immediately so the card interaction doesn't time out.
    return JSONResponse({"text": f"Running: {prompt}"})


async def _run_quick_action_task(
    runtime, task, registry, bridge_manager, space_name: str, thread_name: Optional[str],
) -> None:
    """Run a quick-action task and send the result back to the chat space."""
    try:
        from g3lobster.cli.streaming import StreamEventType, accumulate_text

        persona = runtime.persona
        stream_events = []
        final_result = None
        final_error = None

        async for event in runtime.assign_stream(task):
            stream_events.append(event)
            if event.event_type == StreamEventType.RESULT:
                if event.data.get("status") == "error":
                    error_data = event.data.get("error") or {}
                    if isinstance(error_data, dict):
                        final_error = str(error_data.get("message") or "unknown error")
                elif event.text:
                    final_result = event.text
            elif event.event_type == StreamEventType.ERROR:
                if event.data.get("severity") == "error":
                    final_error = str(event.data.get("message") or event.data.get("error") or "unknown error")

        if not final_result:
            final_result = (task.result or accumulate_text(stream_events)).strip()

        if final_error:
            reply = f"{persona.emoji} {persona.name}: error: {final_error}"
        elif final_result:
            reply = f"{persona.emoji} {persona.name}: {final_result}"
        else:
            reply = f"{persona.emoji} {persona.name}: task finished with no output"

        # Send the reply back to the chat space
        bridge = None
        if bridge_manager:
            bridge = bridge_manager.get_bridge(persona.id)
        if not bridge:
            chat_bridge = getattr(registry, "_chat_bridge", None)
            if chat_bridge:
                bridge = chat_bridge

        if bridge:
            await bridge.send_message(reply, thread_id=thread_name)
        else:
            logging.getLogger(__name__).warning(
                "No bridge available to send quick action result for %s", persona.id,
            )
    except Exception:
        logging.getLogger(__name__).exception("Error running quick action task")
