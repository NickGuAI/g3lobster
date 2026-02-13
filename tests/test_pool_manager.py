from __future__ import annotations

import asyncio
import time

import pytest

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.agents.registry import AgentRegistry
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import Task, TaskStatus


class FakeAgent:
    def __init__(self, agent_id: str):
        self.id = agent_id
        self.state = AgentState.STARTING
        self.started_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.mcp_servers = ["*"]

    async def start(self, mcp_servers=None):
        self.mcp_servers = list(mcp_servers or ["*"])
        self.state = AgentState.IDLE

    async def stop(self):
        self.state = AgentState.STOPPED

    def is_alive(self) -> bool:
        return self.state != AgentState.STOPPED

    async def assign(self, task):
        self.current_task = task
        self.busy_since = time.time()
        self.state = AgentState.BUSY
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        await asyncio.sleep(0.01)
        task.status = TaskStatus.COMPLETED
        task.result = f"done:{task.prompt}"
        task.completed_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.state = AgentState.IDLE
        return task
@pytest.mark.asyncio
async def test_registry_lifecycle_and_status(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    save_persona(
        data_dir,
        AgentPersona(
            id="alpha",
            name="Alpha",
            emoji="🦞",
            soul="Be concise.",
            model="gemini",
            mcp_servers=["*"],
            enabled=True,
        ),
    )

    registry = AgentRegistry(
        data_dir=data_dir,
        summarize_threshold=4,
        context_messages=6,
        health_check_interval_s=3600,
        stuck_timeout_s=60,
        agent_factory=lambda persona, _memory, _context: FakeAgent(persona.id),
    )

    await registry.start_all()
    runtime = registry.get_agent("alpha")
    assert runtime is not None
    assert runtime.persona.name == "Alpha"
    assert runtime.context_builder.system_preamble == "Be concise."

    task = Task(prompt="hello", session_id="thread-1")
    result = await runtime.assign(task)

    assert result.status == TaskStatus.COMPLETED
    assert result.result == "done:hello"

    status = await registry.status()
    assert status["agents"][0]["id"] == "alpha"
    assert status["agents"][0]["state"] == "idle"

    restarted = await registry.restart_agent("alpha")
    assert restarted is True
    assert registry.get_agent("alpha") is not None

    await registry.stop_all()
    assert registry.get_agent("alpha") is None
