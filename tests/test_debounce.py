"""Tests for g3lobster.chat.debounce.MessageDebouncer."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from g3lobster.chat.debounce import MessageDebouncer


class _FakePersona:
    def __init__(self, name: str = "bot") -> None:
        self.name = name
        self.emoji = "🤖"
        self.id = name


class _FlushRecord:
    """Captures arguments from a single flush callback invocation."""

    def __init__(
        self,
        merged_text: str,
        message: Dict[str, Any],
        persona: Any,
        thread_id: Optional[str],
        target_id: str,
        session_id: str,
    ) -> None:
        self.merged_text = merged_text
        self.message = message
        self.persona = persona
        self.thread_id = thread_id
        self.target_id = target_id
        self.session_id = session_id


class _FlushCollector:
    """Async callback that records every flush invocation."""

    def __init__(self) -> None:
        self.records: List[_FlushRecord] = []
        self.flush_event = asyncio.Event()

    async def __call__(
        self,
        merged_text: str,
        message: Dict[str, Any],
        persona: Any,
        thread_id: Optional[str],
        target_id: str,
        session_id: str,
    ) -> None:
        self.records.append(
            _FlushRecord(merged_text, message, persona, thread_id, target_id, session_id)
        )
        self.flush_event.set()


def _make_msg(text: str = "hello") -> Dict[str, Any]:
    return {"text": text, "sender": {"type": "HUMAN", "name": "users/123"}}


KEY = ("spaces/abc", "users/123", "threads/1")


@pytest.mark.asyncio
async def test_single_message_passthrough():
    """A single message should flush after the debounce window."""
    collector = _FlushCollector()
    debouncer = MessageDebouncer(window_s=0.05, flush_callback=collector)
    persona = _FakePersona()
    msg = _make_msg("hello")

    await debouncer.add(KEY, msg, "hello", persona, "threads/1", "agent-1", "sess-1")

    await asyncio.sleep(0.15)
    assert len(collector.records) == 1
    assert collector.records[0].merged_text == "hello"
    assert collector.records[0].target_id == "agent-1"
    assert collector.records[0].session_id == "sess-1"


@pytest.mark.asyncio
async def test_multi_message_merge():
    """Multiple messages within the window should merge into one flush."""
    collector = _FlushCollector()
    debouncer = MessageDebouncer(window_s=0.1, flush_callback=collector)
    persona = _FakePersona()

    await debouncer.add(KEY, _make_msg("one"), "one", persona, "threads/1", "agent-1", "sess-1")
    await asyncio.sleep(0.02)
    await debouncer.add(KEY, _make_msg("two"), "two", persona, "threads/1", "agent-1", "sess-1")
    await asyncio.sleep(0.02)
    await debouncer.add(KEY, _make_msg("three"), "three", persona, "threads/1", "agent-1", "sess-1")

    # Wait for debounce window to expire after last message
    await asyncio.sleep(0.2)
    assert len(collector.records) == 1
    assert collector.records[0].merged_text == "one\ntwo\nthree"


@pytest.mark.asyncio
async def test_timer_reset_on_new_message():
    """Each new message should reset the debounce timer."""
    collector = _FlushCollector()
    debouncer = MessageDebouncer(window_s=0.1, flush_callback=collector)
    persona = _FakePersona()

    await debouncer.add(KEY, _make_msg("a"), "a", persona, "threads/1", "agent-1", "sess-1")
    await asyncio.sleep(0.07)  # almost at window
    # Timer should reset, so no flush yet
    await debouncer.add(KEY, _make_msg("b"), "b", persona, "threads/1", "agent-1", "sess-1")
    await asyncio.sleep(0.07)
    # Should not have flushed yet (only 0.07s since last message)
    assert len(collector.records) == 0

    # Now wait for it to flush
    await asyncio.sleep(0.1)
    assert len(collector.records) == 1
    assert collector.records[0].merged_text == "a\nb"


@pytest.mark.asyncio
async def test_separate_keys_flush_independently():
    """Different keys should accumulate and flush independently."""
    collector = _FlushCollector()
    debouncer = MessageDebouncer(window_s=0.05, flush_callback=collector)
    persona = _FakePersona()

    key_a = ("spaces/abc", "users/123", "threads/1")
    key_b = ("spaces/abc", "users/456", "threads/2")

    await debouncer.add(key_a, _make_msg("msg-a"), "msg-a", persona, "threads/1", "agent-1", "sess-a")
    await debouncer.add(key_b, _make_msg("msg-b"), "msg-b", persona, "threads/2", "agent-1", "sess-b")

    await asyncio.sleep(0.15)
    assert len(collector.records) == 2
    texts = {r.merged_text for r in collector.records}
    assert texts == {"msg-a", "msg-b"}


@pytest.mark.asyncio
async def test_cancel_prevents_flush():
    """Cancelling a key should prevent its flush."""
    collector = _FlushCollector()
    debouncer = MessageDebouncer(window_s=0.05, flush_callback=collector)
    persona = _FakePersona()

    await debouncer.add(KEY, _make_msg("hello"), "hello", persona, "threads/1", "agent-1", "sess-1")
    debouncer.cancel(KEY)

    await asyncio.sleep(0.15)
    assert len(collector.records) == 0


@pytest.mark.asyncio
async def test_cancel_all_prevents_all_flushes():
    """cancel_all should prevent all pending flushes."""
    collector = _FlushCollector()
    debouncer = MessageDebouncer(window_s=0.05, flush_callback=collector)
    persona = _FakePersona()

    key_a = ("spaces/abc", "users/123", "threads/1")
    key_b = ("spaces/abc", "users/456", "threads/2")
    await debouncer.add(key_a, _make_msg("a"), "a", persona, "threads/1", "agent-1", "sess-a")
    await debouncer.add(key_b, _make_msg("b"), "b", persona, "threads/2", "agent-1", "sess-b")

    debouncer.cancel_all()

    await asyncio.sleep(0.15)
    assert len(collector.records) == 0


@pytest.mark.asyncio
async def test_configurable_window():
    """The debounce window should be configurable."""
    collector = _FlushCollector()
    # Very short window
    debouncer = MessageDebouncer(window_s=0.02, flush_callback=collector)
    persona = _FakePersona()

    await debouncer.add(KEY, _make_msg("fast"), "fast", persona, "threads/1", "agent-1", "sess-1")
    await asyncio.sleep(0.08)
    assert len(collector.records) == 1
    assert collector.records[0].merged_text == "fast"


@pytest.mark.asyncio
async def test_first_message_metadata_preserved():
    """The first message's metadata should be used for the flush callback."""
    collector = _FlushCollector()
    debouncer = MessageDebouncer(window_s=0.05, flush_callback=collector)
    persona_1 = _FakePersona("first")
    persona_2 = _FakePersona("second")

    first_msg = _make_msg("one")
    second_msg = _make_msg("two")

    await debouncer.add(KEY, first_msg, "one", persona_1, "threads/1", "agent-1", "sess-1")
    await debouncer.add(KEY, second_msg, "two", persona_2, "threads/1", "agent-1", "sess-1")

    await asyncio.sleep(0.15)
    assert len(collector.records) == 1
    # First message's metadata should be preserved
    assert collector.records[0].message is first_msg
    assert collector.records[0].persona is persona_1
