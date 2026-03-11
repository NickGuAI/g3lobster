from __future__ import annotations

import asyncio

import pytest

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.chat.bridge import ChatBridge
from g3lobster.tasks.types import TaskStatus


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

    async def assign(self, task):
        task.status = TaskStatus.COMPLETED
        task.result = "reply"
        return task

    async def assign_stream(self, task):
        from g3lobster.cli.streaming import StreamEvent, StreamEventType

        result_task = await self.assign(task)
        yield StreamEvent(
            event_type=StreamEventType.MESSAGE,
            data={"role": "assistant", "content": result_task.result or "", "delta": True},
        )
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


class FakeErrorRuntime:
    def __init__(self, persona):
        self.persona = persona

    async def assign_stream(self, task):
        from g3lobster.cli.streaming import StreamEvent, StreamEventType

        task.status = TaskStatus.FAILED
        task.error = "model overloaded"
        yield StreamEvent(
            event_type=StreamEventType.ERROR,
            data={"severity": "error", "message": "model overloaded"},
        )


class FakeToolRuntime:
    def __init__(self, persona):
        self.persona = persona

    async def assign_stream(self, task):
        from g3lobster.cli.streaming import StreamEvent, StreamEventType

        task.status = TaskStatus.COMPLETED
        task.result = "Here is the answer."
        yield StreamEvent(
            event_type=StreamEventType.TOOL_USE,
            data={"tool_name": "web_search"},
        )
        yield StreamEvent(
            event_type=StreamEventType.TOOL_USE,
            data={"tool_name": "read_file"},
        )
        yield StreamEvent(
            event_type=StreamEventType.MESSAGE,
            data={"role": "assistant", "content": "Here is the answer.", "delta": True},
        )
        yield StreamEvent(
            event_type=StreamEventType.RESULT,
            data={"status": "success"},
        )


@pytest.mark.asyncio
async def test_chat_bridge_routes_to_named_agent_by_bot_user_id(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
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
        debounce_window_ms=0,
    )

    message = {
        "text": "Hello there",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": "users/999"}},
            }
        ],
    }

    await bridge.handle_message(message)
    await asyncio.sleep(0.05)  # allow debounce flush

    assert len(service.messages_api.created) == 1
    assert service.messages_api.created[0]["body"]["text"] == "🦀 _Luna is thinking..._"
    assert len(service.messages_api.updated) == 1
    assert service.messages_api.updated[0]["body"]["text"] == "🦀 Luna: reply"


@pytest.mark.asyncio
async def test_chat_bridge_session_key_is_space_and_user(tmp_path) -> None:
    """Messages from the same user in different threads get isolated session keys."""
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
            soul="",
            model="gemini",
            mcp_servers=["*"],
            bot_user_id="users/999",
        ),
    )

    service = FakeService()
    captured_session_ids: list[str] = []

    class CapturingRuntime(FakeRuntimeAgent):
        async def assign(self, task):
            captured_session_ids.append(task.session_id)
            task.status = TaskStatus.COMPLETED
            task.result = "reply"
            return task

    registry = FakeRegistry(data_dir, persona)
    registry.runtime = CapturingRuntime(persona)

    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=0,
    )

    base_message = {
        "text": "Hello there",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": "users/999"}},
            }
        ],
    }

    msg1 = {**base_message, "text": "Hello from thread A", "thread": {"name": "spaces/test/threads/aaa"}}
    msg2 = {**base_message, "text": "Hello from thread B", "thread": {"name": "spaces/test/threads/bbb"}}

    await bridge.handle_message(msg1)
    await asyncio.sleep(0.05)  # allow debounce flush
    await bridge.handle_message(msg2)
    await asyncio.sleep(0.05)  # allow debounce flush

    assert len(captured_session_ids) == 2
    assert captured_session_ids[0] != captured_session_ids[1]
    assert "spaces/test" in captured_session_ids[0]
    assert "users/123" in captured_session_ids[0]
    assert "threads_aaa" in captured_session_ids[0]
    assert "threads_bbb" in captured_session_ids[1]


@pytest.mark.asyncio
async def test_chat_bridge_ignores_unlinked_mentions(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
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
        debounce_window_ms=0,
    )

    message = {
        "text": "Hello there",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": "users/777"}},
            }
        ],
    }

    await bridge.handle_message(message)
    await asyncio.sleep(0.05)  # allow debounce flush

    assert service.messages_api.created == []


@pytest.mark.asyncio
async def test_debug_mode_shows_error_detail_in_chat(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
            soul="",
            model="gemini",
            mcp_servers=["*"],
            bot_user_id="users/999",
        ),
    )

    service = FakeService()
    registry = FakeRegistry(data_dir, persona)
    registry.runtime = FakeErrorRuntime(persona)

    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=0,
        debug_mode=True,
    )

    message = {
        "text": "Do something",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": "users/999"}},
            }
        ],
    }

    await bridge.handle_message(message)
    await asyncio.sleep(0.05)  # allow debounce flush

    assert len(service.messages_api.updated) == 1
    updated_text = service.messages_api.updated[0]["body"]["text"]
    assert "error" in updated_text
    assert "model overloaded" in updated_text
    assert "```" in updated_text


@pytest.mark.asyncio
async def test_debug_off_hides_error_code_block(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
            soul="",
            model="gemini",
            mcp_servers=["*"],
            bot_user_id="users/999",
        ),
    )

    service = FakeService()
    registry = FakeRegistry(data_dir, persona)
    registry.runtime = FakeErrorRuntime(persona)

    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=0,
        debug_mode=False,
    )

    message = {
        "text": "Do something else",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": "users/999"}},
            }
        ],
    }

    await bridge.handle_message(message)
    await asyncio.sleep(0.05)  # allow debounce flush

    assert len(service.messages_api.updated) == 1
    updated_text = service.messages_api.updated[0]["body"]["text"]
    assert "error" in updated_text
    assert "```" not in updated_text


@pytest.mark.asyncio
async def test_chat_bridge_updates_original_message_for_tool_use(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
            soul="",
            model="gemini",
            mcp_servers=["*"],
            bot_user_id="users/999",
        ),
    )

    service = FakeService()
    registry = FakeRegistry(data_dir, persona)
    registry.runtime = FakeToolRuntime(persona)

    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=0,
    )

    message = {
        "text": "Search for something",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": "users/999"}},
            }
        ],
    }

    await bridge.handle_message(message)
    await asyncio.sleep(0.05)  # allow debounce flush

    assert len(service.messages_api.created) == 1
    assert service.messages_api.created[0]["body"]["text"] == "🦀 _Luna is thinking..._"
    assert len(service.messages_api.updated) == 3
    assert service.messages_api.updated[0] == {
        "name": "spaces/test/messages/1",
        "updateMask": "text",
        "body": {"text": "🦀 _Luna is doing web_search..._"},
    }
    assert service.messages_api.updated[1] == {
        "name": "spaces/test/messages/1",
        "updateMask": "text",
        "body": {"text": "🦀 _Luna is doing read_file..._"},
    }
    assert service.messages_api.updated[2] == {
        "name": "spaces/test/messages/1",
        "updateMask": "text",
        "body": {"text": "🦀 Luna: Here is the answer."},
    }


@pytest.mark.asyncio
async def test_chat_bridge_uses_task_error_when_stream_ends_silently(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
            soul="",
            model="gemini",
            mcp_servers=["*"],
            bot_user_id="users/999",
        ),
    )

    service = FakeService()

    class SilentFailureRuntime(FakeRuntimeAgent):
        async def assign_stream(self, task):
            task.status = TaskStatus.FAILED
            task.error = "stream blew up"
            if False:
                yield None

    registry = FakeRegistry(data_dir, persona)
    registry.runtime = SilentFailureRuntime(persona)

    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=0,
    )

    message = {
        "text": "Hello there",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
        "annotations": [
            {
                "type": "USER_MENTION",
                "userMention": {"user": {"type": "BOT", "name": "users/999"}},
            }
        ],
    }

    await bridge.handle_message(message)
    await asyncio.sleep(0.05)  # allow debounce flush

    assert len(service.messages_api.created) == 1
    assert len(service.messages_api.updated) == 1
    assert service.messages_api.updated[0]["body"]["text"] == "🦀 Luna: error: stream blew up"
