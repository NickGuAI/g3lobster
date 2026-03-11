"""Tests for g3lobster.chat.debounce.MessageDebouncer."""

from __future__ import annotations

import asyncio

import pytest

from g3lobster.chat.debounce import MessageDebouncer


def _make_message(user_id: str = "users/1", thread: str = "threads/a") -> dict:
    return {
        "sender": {"type": "HUMAN", "name": user_id},
        "thread": {"name": thread},
        "text": "hello",
    }


KEY = ("spaces/test", "users/1", "threads/a")


@pytest.mark.asyncio
async def test_single_message_passthrough() -> None:
    """A single message should flush after the debounce window."""
    flushed: list[tuple[str, dict, str, str]] = []

    async def on_flush(text, msg, persona_id, thread_id):
        flushed.append((text, msg, persona_id, thread_id))

    debouncer = MessageDebouncer(window_s=0.05, flush_callback=on_flush)
    msg = _make_message()
    debouncer.add(KEY, msg, "hello", "luna", "threads/a")

    # Wait for flush
    await asyncio.sleep(0.15)

    assert len(flushed) == 1
    assert flushed[0][0] == "hello"
    assert flushed[0][2] == "luna"
    assert flushed[0][3] == "threads/a"


@pytest.mark.asyncio
async def test_multi_message_merge() -> None:
    """Multiple rapid messages should merge into a single flush."""
    flushed: list[tuple[str, dict, str, str]] = []

    async def on_flush(text, msg, persona_id, thread_id):
        flushed.append((text, msg, persona_id, thread_id))

    debouncer = MessageDebouncer(window_s=0.1, flush_callback=on_flush)
    msg = _make_message()

    debouncer.add(KEY, msg, "line 1", "luna", "threads/a")
    debouncer.add(KEY, msg, "line 2", "luna", "threads/a")
    debouncer.add(KEY, msg, "line 3", "luna", "threads/a")

    # Wait for flush
    await asyncio.sleep(0.25)

    assert len(flushed) == 1
    assert flushed[0][0] == "line 1\nline 2\nline 3"


@pytest.mark.asyncio
async def test_timer_reset_on_new_message() -> None:
    """Adding a message resets the timer — flush only happens after the last message + window."""
    flushed: list[str] = []

    async def on_flush(text, msg, persona_id, thread_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.1, flush_callback=on_flush)
    msg = _make_message()

    debouncer.add(KEY, msg, "first", "luna", "threads/a")
    await asyncio.sleep(0.06)  # within window — no flush yet
    assert len(flushed) == 0

    debouncer.add(KEY, msg, "second", "luna", "threads/a")
    await asyncio.sleep(0.06)  # timer reset — still within new window
    assert len(flushed) == 0

    await asyncio.sleep(0.15)  # now past the window
    assert len(flushed) == 1
    assert flushed[0] == "first\nsecond"


@pytest.mark.asyncio
async def test_different_keys_flush_independently() -> None:
    """Messages from different keys flush independently."""
    flushed: list[tuple[str, str]] = []

    async def on_flush(text, msg, persona_id, thread_id):
        flushed.append((text, thread_id))

    debouncer = MessageDebouncer(window_s=0.05, flush_callback=on_flush)
    msg = _make_message()

    key_a = ("spaces/test", "users/1", "threads/a")
    key_b = ("spaces/test", "users/2", "threads/b")

    debouncer.add(key_a, msg, "msg A", "luna", "threads/a")
    debouncer.add(key_b, msg, "msg B", "luna", "threads/b")

    await asyncio.sleep(0.15)

    assert len(flushed) == 2
    texts = {f[0] for f in flushed}
    assert texts == {"msg A", "msg B"}


@pytest.mark.asyncio
async def test_cancel_prevents_flush() -> None:
    """Cancelling a key should prevent the flush callback."""
    flushed: list[str] = []

    async def on_flush(text, msg, persona_id, thread_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.05, flush_callback=on_flush)
    msg = _make_message()

    debouncer.add(KEY, msg, "will cancel", "luna", "threads/a")
    debouncer.cancel(KEY)

    await asyncio.sleep(0.15)
    assert len(flushed) == 0


@pytest.mark.asyncio
async def test_cancel_all_prevents_all_flushes() -> None:
    """cancel_all should prevent all pending flushes."""
    flushed: list[str] = []

    async def on_flush(text, msg, persona_id, thread_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.05, flush_callback=on_flush)
    msg = _make_message()

    key_a = ("spaces/test", "users/1", "threads/a")
    key_b = ("spaces/test", "users/2", "threads/b")

    debouncer.add(key_a, msg, "msg A", "luna", "threads/a")
    debouncer.add(key_b, msg, "msg B", "luna", "threads/b")
    debouncer.cancel_all()

    await asyncio.sleep(0.15)
    assert len(flushed) == 0
    assert debouncer.pending_count == 0


@pytest.mark.asyncio
async def test_configurable_window() -> None:
    """The debounce window should be configurable."""
    flushed: list[str] = []

    async def on_flush(text, msg, persona_id, thread_id):
        flushed.append(text)

    # Very short window
    debouncer = MessageDebouncer(window_s=0.02, flush_callback=on_flush)
    msg = _make_message()
    debouncer.add(KEY, msg, "quick", "luna", "threads/a")

    await asyncio.sleep(0.08)
    assert len(flushed) == 1
    assert flushed[0] == "quick"
