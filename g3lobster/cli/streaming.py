"""Stream-JSON event parser for Gemini CLI incremental output."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)


class StreamEventType(str, Enum):
    """Event types emitted by Gemini CLI stream-json output."""
    TURN_START = "turn_start"
    TEXT_DELTA = "text_delta"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TURN_END = "turn_end"
    RESULT = "result"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class StreamEvent:
    """A single parsed event from Gemini CLI stream-json output."""
    event_type: StreamEventType
    data: Dict[str, Any] = field(default_factory=dict)
    raw_line: str = ""

    @property
    def text(self) -> str:
        """Extract text content from text_delta or result events."""
        if self.event_type == StreamEventType.TEXT_DELTA:
            return self.data.get("text", "")
        if self.event_type == StreamEventType.RESULT:
            return self.data.get("result", "")
        return ""

    @property
    def is_terminal(self) -> bool:
        """Whether this event signals the end of the stream."""
        return self.event_type in {StreamEventType.RESULT, StreamEventType.ERROR}


def parse_stream_event(line: str) -> StreamEvent:
    """Parse a single stream-json line into a StreamEvent."""
    line = line.strip()
    if not line:
        return StreamEvent(event_type=StreamEventType.UNKNOWN, raw_line=line)

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("Non-JSON stream line: %s", line[:200])
        return StreamEvent(event_type=StreamEventType.UNKNOWN, data={}, raw_line=line)

    raw_type = data.get("type", "")
    try:
        event_type = StreamEventType(raw_type)
    except ValueError:
        event_type = StreamEventType.UNKNOWN

    return StreamEvent(event_type=event_type, data=data, raw_line=line)


async def stream_events(process_stdout: asyncio.StreamReader) -> AsyncIterator[StreamEvent]:
    """Async generator that yields StreamEvent objects from a subprocess stdout.

    Reads lines from the process stdout, parses each as a stream-json event,
    and yields non-unknown events.
    """
    while True:
        line_bytes = await process_stdout.readline()
        if not line_bytes:
            break
        line = line_bytes.decode("utf-8", errors="replace").rstrip()
        if not line:
            continue
        event = parse_stream_event(line)
        yield event


def accumulate_text(events: List[StreamEvent]) -> str:
    """Accumulate all text deltas from a list of events into a single string."""
    parts: List[str] = []
    for event in events:
        if event.event_type == StreamEventType.TEXT_DELTA:
            parts.append(event.text)
        elif event.event_type == StreamEventType.RESULT and event.text:
            parts.append(event.text)
    return "".join(parts)
