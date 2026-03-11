"""Tests for MessageDebouncer (g3lobster.chat.debounce)."""

from __future__ import annotations

import asyncio

import pytest

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.chat.bridge import ChatBridge
from g3lobster.chat.debounce import MessageDebouncer
from g3lobster.tasks.types import TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeCall:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeMessagesAPI:
    def __init__(self):
        self.created = []
        self.updated = []

    def list(self, parent, pageSize, orderBy):
        return FakeCall({"messages": []})

    def create(self, parent, body):
        self.created.append({"parent": parent, "body": body})
        return FakeCall({"name": "spaces/test/messages/1"})

    def update(self, name, updateMask, body):
        self.updated.append({"name": name, "updateMask": updateMask, "body": body})
        return FakeCall({"name": name})


class FakeSpacesAPI:
    def __init__(self, messages_api):
        self._messages_api = messages_api

    def messages(self):
        return self._messages_api

    def setup(self, body):
        return FakeCall({"name": "spaces/test"})


class FakeService:
    def __init__(self):
        self.messages_api = FakeMessagesAPI()
        self.spaces_api = FakeSpacesAPI(self.messages_api)

    def spaces(self):
        return self.spaces_api


class FakeRuntimeAgent:
    def __init__(self, persona):
        self.persona = persona
        self.prompts: list[str] = []

    async def assign(self, task):
        self.prompts.append(task.prompt)
        task.status = TaskStatus.COMPLETED
        task.result = "reply"
        return task

    async def assign_stream(self, task):
        from g3lobster.cli.streaming import StreamEvent, StreamEventType

        await self.assign(task)
        yield StreamEvent(
            event_type=StreamEventType.RESULT,
            data={"status": "success"},
        )


class FakeRegistry:
    def __init__(self, data_dir, persona):
        self.data_dir = data_dir
        self.runtime = FakeRuntimeAgent(persona)

    def get_agent(self, agent_id):
        if agent_id == self.runtime.persona.id:
            return self.runtime
        return None

    def list_enabled_personas(self):
        return [self.runtime.persona]

    async def start_agent(self, agent_id):
        return agent_id == self.runtime.persona.id


def _make_message(text: str, thread: str = "spaces/test/threads/abc") -> dict:
    return {
        "text": text,
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": thread},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": "users/999"}},
            }
        ],
    }


# ---------------------------------------------------------------------------
# Unit tests for MessageDebouncer class
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_message_passthrough():
    """A single message should be flushed after the debounce window."""
    flushed = []

    async def on_flush(text, msg, persona, thread_id, target_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.05, flush_callback=on_flush)
    key = ("space", "user", "thread")
    debouncer.add(key, "hello", {}, None, "thread", "agent")

    assert debouncer.pending_count == 1
    await asyncio.sleep(0.15)
    assert len(flushed) == 1
    assert flushed[0] == "hello"
    assert debouncer.pending_count == 0


@pytest.mark.asyncio
async def test_multi_message_merge():
    """Multiple messages within the window should be merged with newlines."""
    flushed = []

    async def on_flush(text, msg, persona, thread_id, target_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.1, flush_callback=on_flush)
    key = ("space", "user", "thread")
    debouncer.add(key, "line 1", {"first": True}, None, "thread", "agent")
    debouncer.add(key, "line 2", {"second": True}, None, "thread", "agent")
    debouncer.add(key, "line 3", {"third": True}, None, "thread", "agent")

    assert debouncer.pending_count == 1  # single key
    await asyncio.sleep(0.25)
    assert len(flushed) == 1
    assert flushed[0] == "line 1\nline 2\nline 3"


@pytest.mark.asyncio
async def test_timer_reset_on_new_message():
    """Each new message resets the debounce timer."""
    flushed = []

    async def on_flush(text, msg, persona, thread_id, target_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.1, flush_callback=on_flush)
    key = ("space", "user", "thread")
    debouncer.add(key, "msg1", {}, None, "thread", "agent")
    await asyncio.sleep(0.06)  # 60ms -- not yet fired
    debouncer.add(key, "msg2", {}, None, "thread", "agent")  # resets timer
    await asyncio.sleep(0.06)  # 60ms from msg2 -- still not fired
    assert len(flushed) == 0
    await asyncio.sleep(0.1)  # now it should have fired
    assert len(flushed) == 1
    assert flushed[0] == "msg1\nmsg2"


@pytest.mark.asyncio
async def test_configurable_window():
    """Debounce window should be configurable."""
    flushed = []

    async def on_flush(text, msg, persona, thread_id, target_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.3, flush_callback=on_flush)
    key = ("space", "user", "thread")
    debouncer.add(key, "msg", {}, None, "thread", "agent")
    await asyncio.sleep(0.15)
    assert len(flushed) == 0  # should not have flushed yet at 150ms with 300ms window
    await asyncio.sleep(0.25)
    assert len(flushed) == 1


@pytest.mark.asyncio
async def test_cancel_prevents_flush():
    """Cancelling a key should prevent its flush."""
    flushed = []

    async def on_flush(text, msg, persona, thread_id, target_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.05, flush_callback=on_flush)
    key = ("space", "user", "thread")
    debouncer.add(key, "will be cancelled", {}, None, "thread", "agent")
    debouncer.cancel(key)
    await asyncio.sleep(0.15)
    assert len(flushed) == 0


@pytest.mark.asyncio
async def test_cancel_all_clears_everything():
    """cancel_all should cancel all pending timers."""
    flushed = []

    async def on_flush(text, msg, persona, thread_id, target_id):
        flushed.append(text)

    debouncer = MessageDebouncer(window_s=0.05, flush_callback=on_flush)
    debouncer.add(("s", "u1", "t"), "a", {}, None, "t", "agent")
    debouncer.add(("s", "u2", "t"), "b", {}, None, "t", "agent")
    assert debouncer.pending_count == 2
    debouncer.cancel_all()
    assert debouncer.pending_count == 0
    await asyncio.sleep(0.15)
    assert len(flushed) == 0


@pytest.mark.asyncio
async def test_different_keys_flush_independently():
    """Different keys flush independently."""
    flushed = {}

    async def on_flush(text, msg, persona, thread_id, target_id):
        flushed[thread_id] = text

    debouncer = MessageDebouncer(window_s=0.05, flush_callback=on_flush)
    debouncer.add(("s", "u", "t1"), "msg-t1", {}, None, "t1", "agent")
    debouncer.add(("s", "u", "t2"), "msg-t2", {}, None, "t2", "agent")

    await asyncio.sleep(0.15)
    assert flushed == {"t1": "msg-t1", "t2": "msg-t2"}


# ---------------------------------------------------------------------------
# Integration tests -- debouncer wired through ChatBridge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_debounce_merges_rapid_messages(tmp_path) -> None:
    """Rapid messages through the bridge should be merged into one agent call."""
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="\U0001f980",
            soul="",
            model="gemini",
            mcp_servers=["*"],
            bot_user_id="users/999",
        ),
    )

    service = FakeService()
    registry = FakeRegistry(data_dir, persona)

    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=100,  # 100ms for fast tests
    )

    await bridge.handle_message(_make_message("hello"))
    await bridge.handle_message(_make_message("how are you"))
    await bridge.handle_message(_make_message("what's new"))

    # Nothing dispatched yet -- still debouncing
    assert len(service.messages_api.created) == 0

    # Wait for debounce to flush
    await asyncio.sleep(0.25)

    # Should have sent exactly one "thinking" message and one update
    assert len(service.messages_api.created) == 1
    assert len(registry.runtime.prompts) == 1
    assert registry.runtime.prompts[0] == "hello\nhow are you\nwhat's new"


@pytest.mark.asyncio
async def test_bridge_slash_command_bypasses_debounce(tmp_path) -> None:
    """Slash commands should be handled immediately without debouncing."""
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="\U0001f980",
            soul="",
            model="gemini",
            mcp_servers=["*"],
            bot_user_id="users/999",
        ),
    )

    service = FakeService()
    registry = FakeRegistry(data_dir, persona)

    # Need a cron store for command handling
    from unittest.mock import MagicMock
    cron_store = MagicMock()
    cron_store.list_tasks.return_value = []

    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=500,  # long window to prove bypass
        cron_store=cron_store,
    )

    await bridge.handle_message(_make_message("/help"))

    # Command should be handled immediately -- no debounce wait
    assert len(service.messages_api.created) == 1
    assert "Available commands" in service.messages_api.created[0]["body"]["text"]

    # Debouncer should have nothing pending
    assert bridge._debouncer.pending_count == 0
