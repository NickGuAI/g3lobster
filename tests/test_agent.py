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
        self.session_ids = []

    async def spawn(self, mcp_server_names=None):
        self.alive = True
        self.spawned_with.append(mcp_server_names)

    async def ask(self, prompt: str, timeout: float = 120.0, session_id: str = None) -> str:
        self.prompts.append(prompt)
        self.session_ids.append(session_id)
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


@pytest.mark.asyncio
async def test_agent_assign_passes_session_id_to_process(memory_manager, mcp_manager, context_builder) -> None:
    """P0/P1: Session ID is propagated from task to process.ask() for env injection."""
    process = FakeProcess("done")

    agent = GeminiAgent(
        agent_id="agent-0",
        process_factory=lambda: process,
        mcp_manager=mcp_manager,
        memory_manager=memory_manager,
        context_builder=context_builder,
    )
    await agent.start()

    task = Task(prompt="Hello", session_id="delegation-abc123")
    await agent.assign(task)

    assert process.session_ids == ["delegation-abc123"]
