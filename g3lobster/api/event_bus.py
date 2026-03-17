"""Lightweight pub/sub event bus for SSE streaming."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict

logger = logging.getLogger(__name__)


class EventBus:
    """Per-agent pub/sub using asyncio.Queue per subscriber."""

    _latest: "EventBus | None" = None

    def __init__(self) -> None:
        self._subscribers: Dict[str, list[asyncio.Queue]] = {}
        EventBus._latest = self

    @classmethod
    def latest(cls) -> "EventBus | None":
        return cls._latest

    def publish(self, agent_id: str, event: dict) -> None:
        """Publish an event to all subscribers for the given agent."""
        queues = self._subscribers.get(agent_id)
        if not queues:
            return
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("Dropping event for agent %s — subscriber queue full", agent_id)

    def publish_heartbeat_review(
        self,
        agent_id: str,
        *,
        timestamp: str,
        summary: str,
        suggestions: list[dict],
        stats: dict,
    ) -> None:
        self.publish(
            agent_id,
            {
                "type": "heartbeat_review",
                "timestamp": timestamp,
                "summary": summary,
                "suggestions": suggestions,
                "stats": stats,
            },
        )

    @asynccontextmanager
    async def subscribe(self, agent_id: str, max_queue: int = 256) -> AsyncIterator[asyncio.Queue]:
        """Context manager that yields a Queue receiving events for agent_id."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._subscribers.setdefault(agent_id, []).append(queue)
        try:
            yield queue
        finally:
            queues = self._subscribers.get(agent_id)
            if queues:
                try:
                    queues.remove(queue)
                except ValueError:
                    pass
                if not queues:
                    del self._subscribers[agent_id]
