"""Observability routes for agent events."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["events"])


def _resolve_emitter(request: Request):
    emitter = getattr(request.app.state, "emitter", None)
    if emitter is None:
        raise HTTPException(status_code=503, detail="Event emitter is not configured")
    return emitter


@router.get("/events")
async def stream_events(
    request: Request,
    agent_id: Optional[str] = None,
    stream: Optional[str] = None,
):
    """Stream events over Server-Sent Events (SSE)."""
    emitter = _resolve_emitter(request)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        loop = asyncio.get_running_loop()

        def listener(event) -> None:
            if agent_id and event.agent_id != agent_id:
                return
            if stream and event.stream != stream:
                return

            def _enqueue() -> None:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Best-effort stream; drop overflow instead of blocking emitters.
                    return

            loop.call_soon_threadsafe(_enqueue)

        unsubscribe = emitter.on_event(listener)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                payload = json.dumps(asdict(event), ensure_ascii=False, default=str)
                yield f"data: {payload}\n\n"
        finally:
            unsubscribe()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/events/recent")
async def get_recent_events(
    request: Request,
    agent_id: Optional[str] = None,
    stream: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    """Return in-memory recent events."""
    emitter = _resolve_emitter(request)
    events = emitter.recent_events(agent_id=agent_id, stream=stream, limit=limit)
    return [asdict(event) for event in events]


@router.get("/agents/{agent_id}/events/history")
async def get_agent_event_history(
    request: Request,
    agent_id: str,
    limit: int = Query(default=100, ge=1, le=5000),
):
    """Return persisted event history for a specific agent."""
    emitter = _resolve_emitter(request)
    events_dir = getattr(emitter, "events_dir", None)
    if not events_dir:
        return []

    events_file = Path(events_dir) / agent_id / "events.jsonl"
    if not events_file.exists():
        return []

    lines = events_file.read_text(encoding="utf-8").splitlines()
    payloads = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads
