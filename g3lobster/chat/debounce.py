"""Message debouncer for Google Chat rapid-fire messages.

Collects messages from the same (space, user, thread) tuple within a
configurable time window, then flushes them as a single merged prompt.

Slash commands bypass the debouncer entirely -- they are detected and
handled before messages reach ``add()``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Key: (space_id, user_id, thread_id)
DebounceKey = Tuple[str, str, str]

# Maximum buffered messages per key to prevent unbounded memory growth.
_MAX_BUFFER_SIZE = 50


@dataclass
class _PendingBurst:
    """Accumulated messages waiting for the debounce timer to fire."""

    texts: List[str] = field(default_factory=list)
    first_message: Optional[Dict[str, Any]] = None
    persona: Any = None
    thread_id: Optional[str] = None
    target_id: Optional[str] = None
    timer: Optional[asyncio.TimerHandle] = None


class MessageDebouncer:
    """Debounce rapid-fire messages keyed by (space_id, user_id, thread_id).

    Parameters
    ----------
    window_s:
        Debounce window in seconds. Messages arriving within this window
        of the most recent message are merged.
    flush_callback:
        Async callable invoked on timer expiry with the merged prompt and
        first message's metadata:
        ``flush_callback(merged_text, message, persona, thread_id, target_id)``
    max_buffer_per_key:
        Maximum number of messages to buffer per key (prevents unbounded
        memory growth). Once reached, the buffer flushes immediately.
    """

    def __init__(
        self,
        window_s: float = 2.0,
        flush_callback: Optional[
            Callable[..., Coroutine[Any, Any, None]]
        ] = None,
        max_buffer_per_key: int = _MAX_BUFFER_SIZE,
    ) -> None:
        if window_s < 0:
            raise ValueError("window_s must be >= 0")
        self._window_s = window_s
        self._flush_callback = flush_callback
        self._max_buffer = max_buffer_per_key
        self._pending: Dict[DebounceKey, _PendingBurst] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def window_s(self) -> float:
        return self._window_s

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.get_running_loop()
        return self._loop

    def add(
        self,
        key: DebounceKey,
        text: str,
        message: Dict[str, Any],
        persona: Any,
        thread_id: Optional[str],
        target_id: str,
    ) -> None:
        """Buffer a message. Resets the debounce timer for *key*."""
        burst = self._pending.get(key)
        if burst is None:
            burst = _PendingBurst()
            self._pending[key] = burst

        # Keep first message metadata for the flush callback
        if burst.first_message is None:
            burst.first_message = message
            burst.persona = persona
            burst.thread_id = thread_id
            burst.target_id = target_id

        # Cap buffer size to prevent unbounded growth.
        if len(burst.texts) < self._max_buffer:
            burst.texts.append(text)

        # Cancel existing timer and reset
        if burst.timer is not None:
            burst.timer.cancel()
            burst.timer = None

        # Flush immediately if buffer is full
        if len(burst.texts) >= self._max_buffer:
            self._schedule_flush(key, delay=0)
            return

        self._schedule_flush(key, delay=self._window_s)

    def _schedule_flush(self, key: DebounceKey, delay: float) -> None:
        loop = self._get_loop()
        burst = self._pending.get(key)
        if burst is None:
            return
        burst.timer = loop.call_later(delay, self._fire, key)

    def _fire(self, key: DebounceKey) -> None:
        """Timer callback -- schedules the async flush as a task."""
        burst = self._pending.pop(key, None)
        if burst is None:
            return
        burst.timer = None

        loop = self._get_loop()
        loop.create_task(self._flush(key, burst))

    async def _flush(self, key: DebounceKey, burst: _PendingBurst) -> None:
        merged = "\n".join(burst.texts)
        count = len(burst.texts)
        if count > 1:
            logger.info(
                "Debounce: merged %d messages for %s", count, key,
            )
        if self._flush_callback is not None:
            try:
                await self._flush_callback(
                    merged,
                    burst.first_message,
                    burst.persona,
                    burst.thread_id,
                    burst.target_id,
                )
            except Exception:
                logger.exception("Debounce flush callback error for %s", key)

    def cancel(self, key: DebounceKey) -> None:
        """Cancel a pending debounce for *key* without flushing."""
        burst = self._pending.pop(key, None)
        if burst is not None and burst.timer is not None:
            burst.timer.cancel()

    def cancel_all(self) -> None:
        """Cancel all pending debounce timers (for shutdown)."""
        for burst in self._pending.values():
            if burst.timer is not None:
                burst.timer.cancel()
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        """Number of keys currently waiting for flush."""
        return len(self._pending)
