from __future__ import annotations

import pathlib

import pytest

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.agents.subagent_registry import SubagentRegistry
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
    await bridge.handle_message(msg2)

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
        debug_mode=True,
        debounce_window_ms=0,
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
        debug_mode=False,
        debounce_window_ms=0,
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

    assert len(service.messages_api.created) == 1
    assert len(service.messages_api.updated) == 1
    assert service.messages_api.updated[0]["body"]["text"] == "🦀 Luna: error: stream blew up"


class FakeMultiRegistry:
    """Registry that knows about multiple agents."""

    def __init__(self, data_dir, personas_and_runtimes):
        self.data_dir = data_dir
        self._runtimes = {p.id: rt for p, rt in personas_and_runtimes}
        self._personas = [p for p, _ in personas_and_runtimes]
        self.subagent_registry = SubagentRegistry(pathlib.Path(data_dir))

    def get_agent(self, agent_id):
        return self._runtimes.get(agent_id)

    def list_enabled_personas(self):
        return list(self._personas)

    def load_persona(self, agent_id):
        rt = self._runtimes.get(agent_id)
        return rt.persona if rt else None

    async def start_agent(self, agent_id):
        return agent_id in self._runtimes


class FakeStreamingRuntime:
    """Yields multiple MESSAGE events to test streaming throttle logic."""

    def __init__(self, persona):
        self.persona = persona

    async def assign_stream(self, task):
        from g3lobster.cli.streaming import StreamEvent, StreamEventType

        task.status = TaskStatus.COMPLETED
        # Emit several MESSAGE events — all arrive instantly (no real delay)
        for chunk in ["Hello ", "world, ", "this ", "is ", "streaming!"]:
            yield StreamEvent(
                event_type=StreamEventType.MESSAGE,
                data={"role": "assistant", "content": chunk, "delta": True},
            )
        yield StreamEvent(
            event_type=StreamEventType.RESULT,
            data={"status": "success"},
        )


@pytest.mark.asyncio
async def test_unmentioned_message_routes_to_concierge(tmp_path) -> None:
    """When concierge is configured and no @-mention, route to concierge agent."""
    data_dir = str(tmp_path / "data")
    concierge_persona = save_persona(
        data_dir,
        AgentPersona(
            id="concierge",
            name="Concierge",
            emoji="🧭",
            soul="",
            model="gemini",
            mcp_servers=["*"],
        ),
    )
    luna_persona = save_persona(
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

    concierge_runtime = FakeRuntimeAgent(concierge_persona)
    luna_runtime = FakeRuntimeAgent(luna_persona)
    registry = FakeMultiRegistry(data_dir, [
        (concierge_persona, concierge_runtime),
        (luna_persona, luna_runtime),
    ])

    service = FakeService()
    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        concierge_agent_id="concierge",
        debounce_window_ms=0,
    )

    message = {
        "text": "What's on my calendar tomorrow?",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
    }

    # Simulate a delegation run that the concierge would have triggered
    run = registry.subagent_registry.register_run(
        parent_agent_id="concierge",
        child_agent_id="luna",
        task="What's on my calendar tomorrow?",
        parent_session_id="test-session",
    )
    registry.subagent_registry.mark_running(run.run_id)
    registry.subagent_registry.complete_run(run.run_id, "Calendar reply")

    await bridge.handle_message(message)

    assert len(service.messages_api.created) == 1
    # Thinking message still uses concierge persona (it starts before delegation)
    assert "Concierge" in service.messages_api.created[0]["body"]["text"]
    assert len(service.messages_api.updated) == 1
    # Final reply is attributed to the specialist (Luna), not the concierge
    assert "🦀 Luna: reply" in service.messages_api.updated[0]["body"]["text"]


@pytest.mark.asyncio
async def test_unmentioned_message_dropped_when_concierge_disabled(tmp_path) -> None:
    """When concierge is not configured, unmentioned messages are dropped."""
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
        # concierge_agent_id not set (None by default)
    )

    message = {
        "text": "What's on my calendar tomorrow?",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
    }

    await bridge.handle_message(message)

    assert service.messages_api.created == []


@pytest.mark.asyncio
async def test_explicit_mention_still_routes_directly_with_concierge(tmp_path) -> None:
    """Explicit @-mention bypasses concierge and routes to the mentioned agent."""
    data_dir = str(tmp_path / "data")
    concierge_persona = save_persona(
        data_dir,
        AgentPersona(
            id="concierge",
            name="Concierge",
            emoji="🧭",
            soul="",
            model="gemini",
            mcp_servers=["*"],
        ),
    )
    luna_persona = save_persona(
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

    concierge_runtime = FakeRuntimeAgent(concierge_persona)
    luna_runtime = FakeRuntimeAgent(luna_persona)
    registry = FakeMultiRegistry(data_dir, [
        (concierge_persona, concierge_runtime),
        (luna_persona, luna_runtime),
    ])

    service = FakeService()
    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        concierge_agent_id="concierge",
        debounce_window_ms=0,
    )

    message = {
        "text": "Hello Luna",
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

    assert len(service.messages_api.created) == 1
    assert "Luna" in service.messages_api.created[0]["body"]["text"]
    assert "Concierge" not in service.messages_api.created[0]["body"]["text"]
    assert len(service.messages_api.updated) == 1
    assert "🦀 Luna: reply" in service.messages_api.updated[0]["body"]["text"]


def test_save_chat_config_persists_concierge_fields(tmp_path) -> None:
    """save_chat_config must round-trip the concierge_enabled and concierge_agent_id fields."""
    from g3lobster.config import ChatConfig, save_chat_config

    cfg = ChatConfig(
        enabled=True,
        space_id="spaces/abc",
        space_name="Test Space",
        poll_interval_s=3.0,
        concierge_enabled=True,
        concierge_agent_id="my-concierge",
    )
    config_path = str(tmp_path / "config.yaml")
    save_chat_config(cfg, config_path)

    import yaml

    with open(config_path) as f:
        saved = yaml.safe_load(f)

    chat = saved["chat"]
    assert chat["concierge_enabled"] is True
    assert chat["concierge_agent_id"] == "my-concierge"
    assert chat["enabled"] is True
    assert chat["space_id"] == "spaces/abc"


@pytest.mark.asyncio
async def test_streaming_text_updates_with_throttle(tmp_path) -> None:
    """MESSAGE events cause intermediate updates, throttled by interval."""
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
    registry.runtime = FakeStreamingRuntime(persona)

    # Use a very small interval so the first MESSAGE triggers an update
    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        stream_update_interval_s=0.0,
        debounce_window_ms=0,
    )

    message = {
        "text": "Stream me",
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

    # Expect: 1 create (thinking) + N intermediate updates + 1 final update
    assert len(service.messages_api.created) == 1
    assert service.messages_api.created[0]["body"]["text"] == "🦀 _Luna is thinking..._"

    # At least one intermediate streaming update before the final one
    assert len(service.messages_api.updated) >= 2

    # The final update should contain the full accumulated text
    final_text = service.messages_api.updated[-1]["body"]["text"]
    assert final_text == "🦀 Luna: Hello world, this is streaming!"

    # Intermediate updates should show partial accumulated text
    first_update_text = service.messages_api.updated[0]["body"]["text"]
    assert first_update_text.startswith("🦀 Luna: Hello")


@pytest.mark.asyncio
async def test_streaming_throttle_limits_updates(tmp_path) -> None:
    """With a large interval, rapid MESSAGE events produce fewer intermediate updates."""
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
    registry.runtime = FakeStreamingRuntime(persona)

    # Use a very large interval so no intermediate updates fire
    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=service,
        spaces_config=str(tmp_path / "spaces.json"),
        stream_update_interval_s=9999.0,
        debounce_window_ms=0,
    )

    message = {
        "text": "Stream throttled",
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

    # Only the final update — no intermediate streaming updates
    assert len(service.messages_api.updated) == 1
    assert service.messages_api.updated[0]["body"]["text"] == "🦀 Luna: Hello world, this is streaming!"
