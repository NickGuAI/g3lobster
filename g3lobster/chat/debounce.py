"""Message debouncer for rapid-fire Google Chat messages.

Collects messages from the same (space, user, thread) tuple within a
configurable window and flushes them as a single merged prompt.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Key: (space_id, user_id, thread_id)
DebounceKey = Tuple[str, str, str]

# Maximum buffered messages per key to prevent unbounded memory growth.
_MAX_BUFFER_SIZE = 50


@dataclass
class _PendingBurst:
    """Accumulates messages for a single debounce key."""

    texts: List[str] = field(default_factory=list)
    first_message: Dict[str, Any] = field(default_factory=dict)
    persona_id: str = ""
    thread_id: str = ""
    timer: asyncio.TimerHandle | None = None


class MessageDebouncer:
    """Buffers rapid-fire messages and flushes them as a merged prompt.

    Parameters
    ----------
    window_s:
        Debounce window in seconds.  Messages arriving within this window
        after the *last* message are merged.
    flush_callback:
        Async callable invoked on flush with ``(merged_text, first_message,
        persona_id, thread_id)``.
    """

    def __init__(
        self,
        window_s: float = 2.0,
        flush_callback: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._window_s = window_s
        self._flush_callback = flush_callback
        self._pending: Dict[DebounceKey, _PendingBurst] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.get_running_loop()
        return self._loop

    def add(
        self,
        key: DebounceKey,
        message: Dict[str, Any],
        text: str,
        persona_id: str,
        thread_id: str,
    ) -> None:
        """Buffer a message.  Resets the flush timer on every call."""
        burst = self._pending.get(key)
        if burst is None:
            burst = _PendingBurst(
                first_message=message,
                persona_id=persona_id,
                thread_id=thread_id,
            )
            self._pending[key] = burst

        # Enforce bounded buffer.
        if len(burst.texts) < _MAX_BUFFER_SIZE:
            burst.texts.append(text)

        # Reset timer on each new message.
        if burst.timer is not None:
            burst.timer.cancel()

        loop = self._get_loop()
        burst.timer = loop.call_later(self._window_s, self._schedule_flush, key)

    def _schedule_flush(self, key: DebounceKey) -> None:
        """Schedule the async flush from within the event loop callback."""
        loop = self._get_loop()
        loop.create_task(self._flush(key))

    async def _flush(self, key: DebounceKey) -> None:
        burst = self._pending.pop(key, None)
        if burst is None:
            return

        merged_text = "\n".join(burst.texts)
        count = len(burst.texts)
        logger.info(
            "Debounce flush: key=%s messages=%d merged_len=%d",
            key,
            count,
            len(merged_text),
        )

        if self._flush_callback is not None:
            try:
                await self._flush_callback(
                    merged_text,
                    burst.first_message,
                    burst.persona_id,
                    burst.thread_id,
                )
            except Exception:
                logger.exception("Debounce flush callback error for key=%s", key)

    def cancel(self, key: DebounceKey) -> None:
        """Cancel a pending burst for *key* without flushing."""
        burst = self._pending.pop(key, None)
        if burst and burst.timer is not None:
            burst.timer.cancel()

    def cancel_all(self) -> None:
        """Cancel all pending bursts.  Call on bridge shutdown."""
        for burst in self._pending.values():
            if burst.timer is not None:
                burst.timer.cancel()
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return len(self._pending)
