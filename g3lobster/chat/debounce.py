"""Message debouncer for rapid-fire Google Chat messages.

Collects messages from the same user+thread within a configurable time window
and flushes them as a single merged prompt to the agent.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Key: (space_id, user_id, thread_id)
DebounceKey = Tuple[str, str, str]

# Max messages buffered per key to prevent unbounded memory growth.
_MAX_BUFFER_SIZE = 50


@dataclass
class _PendingBatch:
    """Accumulated messages waiting for the debounce window to expire."""

    texts: List[str] = field(default_factory=list)
    first_message: Optional[Dict[str, Any]] = None
    persona: Any = None
    thread_id: Optional[str] = None
    target_id: Optional[str] = None
    session_id: Optional[str] = None
    timer: Optional[asyncio.TimerHandle] = None


class MessageDebouncer:
    """Buffers rapid-fire messages and flushes them as a single merged prompt.

    Parameters
    ----------
    window_s:
        Debounce window in seconds.  Messages arriving within this window
        after the *last* message are merged.
    flush_callback:
        Async callable invoked when the window expires.  Signature::

            async def callback(
                merged_text: str,
                message: dict,
                persona: Any,
                thread_id: str | None,
                target_id: str,
                session_id: str,
            ) -> None
    """

    def __init__(
        self,
        window_s: float = 2.0,
        flush_callback: Optional[
            Callable[..., Coroutine[Any, Any, None]]
        ] = None,
    ) -> None:
        self._window_s = window_s
        self._flush_callback = flush_callback
        self._pending: Dict[DebounceKey, _PendingBatch] = {}

    async def add(
        self,
        key: DebounceKey,
        message: Dict[str, Any],
        text: str,
        persona: Any,
        thread_id: Optional[str],
        target_id: str,
        session_id: str,
    ) -> None:
        """Buffer a message.  Resets the debounce timer on each call.

        When ``window_s <= 0`` the message is dispatched immediately (no
        buffering), which is useful in tests and when debouncing is disabled.
        """
        if self._window_s <= 0:
            # Bypass: dispatch immediately without buffering.
            if self._flush_callback is not None:
                await self._flush_callback(
                    text, message, persona, thread_id, target_id, session_id,
                )
            return

        batch = self._pending.get(key)
        if batch is None:
            batch = _PendingBatch()
            self._pending[key] = batch

        # Cancel existing timer so the window restarts.
        if batch.timer is not None:
            batch.timer.cancel()

        # Store metadata from the first message in the batch.
        if batch.first_message is None:
            batch.first_message = message
            batch.persona = persona
            batch.thread_id = thread_id
            batch.target_id = target_id
            batch.session_id = session_id

        if len(batch.texts) < _MAX_BUFFER_SIZE:
            batch.texts.append(text)

        loop = asyncio.get_running_loop()
        batch.timer = loop.call_later(
            self._window_s, self._schedule_flush, key
        )

    def _schedule_flush(self, key: DebounceKey) -> None:
        """Schedule the async flush on the running event loop."""
        asyncio.ensure_future(self._flush(key))

    async def _flush(self, key: DebounceKey) -> None:
        batch = self._pending.pop(key, None)
        if batch is None or not batch.texts:
            return

        merged_text = "\n".join(batch.texts)
        logger.info(
            "Debounce flush: key=%s msgs=%d merged_len=%d",
            key,
            len(batch.texts),
            len(merged_text),
        )

        if self._flush_callback is not None:
            try:
                await self._flush_callback(
                    merged_text,
                    batch.first_message,
                    batch.persona,
                    batch.thread_id,
                    batch.target_id,
                    batch.session_id,
                )
            except Exception:
                logger.exception("Debounce flush callback error for key=%s", key)

    def cancel(self, key: DebounceKey) -> None:
        """Cancel a pending batch for the given key."""
        batch = self._pending.pop(key, None)
        if batch and batch.timer is not None:
            batch.timer.cancel()

    def cancel_all(self) -> None:
        """Cancel all pending batches (for shutdown)."""
        for batch in self._pending.values():
            if batch.timer is not None:
                batch.timer.cancel()
        self._pending.clear()
