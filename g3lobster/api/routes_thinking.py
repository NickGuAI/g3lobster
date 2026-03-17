"""SSE endpoint for live agent thinking events."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["thinking"])

HEARTBEAT_INTERVAL_S = 15


@router.get("/{agent_id}/stream")
async def stream_thinking(agent_id: str, request: Request) -> StreamingResponse:
    """SSE endpoint that streams live thinking events for an agent."""
    event_bus = request.app.state.event_bus
    registry = getattr(request.app.state, "registry", None)
    if registry is not None:
        setattr(registry, "event_bus", event_bus)

    async def event_generator():
        async with event_bus.subscribe(agent_id) as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL_S)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
                except asyncio.CancelledError:
                    break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
