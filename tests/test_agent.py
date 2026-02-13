from __future__ import annotations

import pytest

from g3lobster.pool.agent import GeminiAgent
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import Task, TaskStatus


class FakeProcess:
    def __init__(self, response: str):
        self.response = response
        self.alive = False
        self.prompts = []
        self.spawned_with = []

    async def spawn(self, mcp_server_names=None):
        self.alive = True
        self.spawned_with.append(mcp_server_names)

    async def ask(self, prompt: str, timeout: float = 120.0) -> str:
        self.prompts.append(prompt)
        return self.response

    async def kill(self):
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive


@pytest.mark.asyncio
async def test_agent_assign_updates_task_and_memory(memory_manager, mcp_manager, context_builder) -> None:
    process = FakeProcess("✦ final answer")

    def process_factory():
        return process

    agent = GeminiAgent(
        agent_id="agent-0",
        process_factory=process_factory,
        mcp_manager=mcp_manager,
        memory_manager=memory_manager,
        context_builder=context_builder,
        default_mcp_servers=["*"],
    )

    await agent.start()
    assert process.spawned_with == [["*"]]
    task = Task(prompt="Ping", session_id="thread-1")

    result = await agent.assign(task)

    assert result.status == TaskStatus.COMPLETED
    assert result.result == "final answer"
    assert agent.state == AgentState.IDLE

    entries = memory_manager.read_session("thread-1")
    assert len(entries) == 2
    assert entries[0]["message"]["role"] == "user"
    assert entries[1]["message"]["role"] == "assistant"
