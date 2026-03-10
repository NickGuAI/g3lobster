"""Streaming event types and parser for Gemini CLI stream-json output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, Optional


class StreamEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_USE = "tool_use"
    TURN_END = "turn_end"
    RESULT = "result"
    ERROR = "error"


@dataclass
class StreamEvent:
    """A single event parsed from Gemini CLI stream-json output."""

    type: StreamEventType
    text: str = ""
    tool_name: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


def parse_stream_event(line: str) -> Optional[StreamEvent]:
    """Parse a single line of stream-json output into a StreamEvent.

    Returns None for lines that are not valid JSON or not recognized events.
    """
    stripped = line.strip()
    if not stripped:
        return None

    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    if not isinstance(obj, dict):
        return None

    # Gemini CLI stream-json emits objects with varying shapes.
    # Detect event type from the object structure.

    # Tool use events
    if "toolCall" in obj or "tool_call" in obj:
        tool_call = obj.get("toolCall") or obj.get("tool_call") or {}
        tool_name = tool_call.get("name") or tool_call.get("tool", "")
        return StreamEvent(
            type=StreamEventType.TOOL_USE,
            tool_name=str(tool_name),
            data=obj,
        )

    # Text delta events
    if "text" in obj and isinstance(obj["text"], str):
        return StreamEvent(
            type=StreamEventType.TEXT_DELTA,
            text=obj["text"],
            data=obj,
        )

    # Turn completion / result
    if "turnComplete" in obj or "turn_complete" in obj:
        return StreamEvent(type=StreamEventType.TURN_END, data=obj)

    if "result" in obj:
        result_text = obj.get("result", "")
        if isinstance(result_text, dict):
            result_text = result_text.get("text", "")
        return StreamEvent(
            type=StreamEventType.RESULT,
            text=str(result_text),
            data=obj,
        )

    # Error events
    if "error" in obj:
        return StreamEvent(
            type=StreamEventType.ERROR,
            text=str(obj["error"]),
            data=obj,
        )

    # Unrecognized JSON line — treat as text delta if it has content
    return None


async def stream_events(lines: AsyncIterator[bytes]) -> AsyncIterator[StreamEvent]:
    """Convert an async iterator of raw stdout lines into StreamEvents."""
    full_text_parts = []
    async for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace")
        event = parse_stream_event(line)
        if event is not None:
            if event.type == StreamEventType.TEXT_DELTA:
                full_text_parts.append(event.text)
            yield event

    # If no RESULT event was emitted, yield the accumulated text as result.
    if full_text_parts:
        yield StreamEvent(
            type=StreamEventType.RESULT,
            text="".join(full_text_parts),
        )
