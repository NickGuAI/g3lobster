from __future__ import annotations

import asyncio
import time

import pytest

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.agents.registry import AgentRegistry
from g3lobster.cli.streaming import StreamEvent, StreamEventType
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


class BlockingStreamAgent(FakeAgent):
    def __init__(self, agent_id: str, release_event: asyncio.Event):
        super().__init__(agent_id)
        self._release_event = release_event
        self.assign_started = asyncio.Event()
        self.calls: list[str] = []

    async def assign(self, task):
        self.calls.append(f"assign:{task.prompt}")
        self.current_task = task
        self.busy_since = time.time()
        self.state = AgentState.BUSY
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self.assign_started.set()
        await self._release_event.wait()
        task.status = TaskStatus.COMPLETED
        task.result = f"done:{task.prompt}"
        task.completed_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.state = AgentState.IDLE
        return task

    async def assign_stream(self, task):
        self.calls.append(f"stream:{task.prompt}")
        self.current_task = task
        self.busy_since = time.time()
        self.state = AgentState.BUSY
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        yield StreamEvent(
            event_type=StreamEventType.RESULT,
            data={"status": "success", "response": f"stream:{task.prompt}"},
        )
        task.status = TaskStatus.COMPLETED
        task.result = f"stream:{task.prompt}"
        task.completed_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.state = AgentState.IDLE


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


@pytest.mark.asyncio
async def test_registered_agent_assign_stream_uses_queue(tmp_path) -> None:
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

    release_event = asyncio.Event()
    registry = AgentRegistry(
        data_dir=data_dir,
        context_messages=6,
        health_check_interval_s=3600,
        stuck_timeout_s=60,
        agent_factory=lambda persona, _memory, _context: BlockingStreamAgent(
            persona.id, release_event
        ),
    )

    await registry.start_all()
    runtime = registry.get_agent("alpha")
    assert runtime is not None

    try:
        first = asyncio.create_task(runtime.assign(Task(prompt="first", session_id="s1")))
        await runtime.agent.assign_started.wait()

        streamed_events = []

        async def _consume_stream() -> None:
            async for event in runtime.assign_stream(Task(prompt="second", session_id="s2")):
                streamed_events.append(event)

        second = asyncio.create_task(_consume_stream())
        await asyncio.sleep(0.01)

        # Streaming work must queue behind the in-flight task.
        assert runtime.pending_assignments == 1

        release_event.set()
        await asyncio.wait_for(first, timeout=1.0)
        await asyncio.wait_for(second, timeout=1.0)

        assert runtime.agent.calls == ["assign:first", "stream:second"]
        assert streamed_events
        assert streamed_events[-1].event_type == StreamEventType.RESULT
    finally:
        await registry.stop_all()
