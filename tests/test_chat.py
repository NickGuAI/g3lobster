from __future__ import annotations

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

    def list(self, parent, pageSize, orderBy):
        return FakeCall({"messages": []})

    def create(self, parent, body):
        self.created.append({"parent": parent, "body": body})
        return FakeCall({"name": "spaces/test/messages/1"})


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

    assert len(service.messages_api.created) == 2
    assert service.messages_api.created[0]["body"]["text"] == "🦀 _Luna is thinking..._"
    assert service.messages_api.created[1]["body"]["text"] == "🦀 Luna: reply"


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
