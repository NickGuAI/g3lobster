"""Kanban board routes for unified task board + SSE updates."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter(prefix="/board", tags=["board"])

HEARTBEAT_INTERVAL_S = 15


def _get_store(request: Request):
    store = getattr(request.app.state, "board_store", None)
    if store is None:
        return None
    return store


@router.get("")
async def get_board(request: Request):
    static_dir = Path(__file__).resolve().parent.parent / "static"
    return FileResponse(static_dir / "board.html")


@router.get("/tasks")
async def list_board_tasks(
    request: Request,
    type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    created_by: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    store = _get_store(request)
    if store is None:
        return {"tasks": []}
    items = store.list_items(
        type_filter=type,
        status_filter=status,
        priority_filter=priority,
        agent_id=agent_id,
        created_by=created_by,
        limit=limit,
    )
    return {"tasks": [asdict(item) for item in items]}


@router.get("/stream")
async def stream_board_events(request: Request) -> StreamingResponse:
    """SSE endpoint for global board updates and heartbeat suggestions."""
    event_bus = request.app.state.event_bus

    async def event_generator():
        async with event_bus.subscribe("__board__") as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL_S)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
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
