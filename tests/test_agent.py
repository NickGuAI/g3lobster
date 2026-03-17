from __future__ import annotations

import asyncio

import pytest

from g3lobster.pool.agent import HEARTBEAT_MIN_INTERVAL_S, GeminiAgent
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


@pytest.fixture
def fast_heartbeat_floor(monkeypatch):
    monkeypatch.setattr("g3lobster.pool.agent.HEARTBEAT_MIN_INTERVAL_S", 0.01)


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


def test_agent_clamps_heartbeat_interval_to_safe_min(memory_manager, mcp_manager, context_builder) -> None:
    agent = GeminiAgent(
        agent_id="agent-0",
        process_factory=lambda: FakeProcess("done"),
        mcp_manager=mcp_manager,
        memory_manager=memory_manager,
        context_builder=context_builder,
        heartbeat_enabled=True,
        heartbeat_interval_s=1.0,
    )
    assert agent.heartbeat_interval_s == HEARTBEAT_MIN_INTERVAL_S


@pytest.mark.asyncio
async def test_agent_heartbeat_loop_publishes_reviews(
    memory_manager, mcp_manager, context_builder, fast_heartbeat_floor
) -> None:
    process = FakeProcess("done")
    published = []

    async def provider():
        return {
            "type": "heartbeat_review",
            "summary": "Heartbeat check complete.",
            "suggestions": [],
            "stats": {"pending": 0, "in_progress": 0, "blocked": 0, "overdue": 0},
        }

    agent = GeminiAgent(
        agent_id="agent-0",
        process_factory=lambda: process,
        mcp_manager=mcp_manager,
        memory_manager=memory_manager,
        context_builder=context_builder,
        heartbeat_enabled=True,
        heartbeat_interval_s=0.05,
        heartbeat_review_provider=provider,
        heartbeat_event_publisher=lambda agent_id, event: published.append((agent_id, event)),
    )
    await agent.start()
    await asyncio.sleep(0.14)
    await agent.stop()

    assert published
    first_agent_id, first_event = published[0]
    assert first_agent_id == "agent-0"
    assert first_event["type"] == "heartbeat_review"
    assert "timestamp" in first_event


@pytest.mark.asyncio
async def test_agent_heartbeat_skips_when_busy(
    memory_manager, mcp_manager, context_builder, fast_heartbeat_floor
) -> None:
    process = FakeProcess("done")
    published = []
    provider_calls = 0

    def provider():
        nonlocal provider_calls
        provider_calls += 1
        return {
            "type": "heartbeat_review",
            "summary": "ok",
            "suggestions": [],
            "stats": {},
        }

    agent = GeminiAgent(
        agent_id="agent-0",
        process_factory=lambda: process,
        mcp_manager=mcp_manager,
        memory_manager=memory_manager,
        context_builder=context_builder,
        heartbeat_enabled=True,
        heartbeat_interval_s=0.05,
        heartbeat_review_provider=provider,
        heartbeat_event_publisher=lambda _agent_id, event: published.append(event),
    )
    await agent.start()
    agent.state = AgentState.BUSY
    await asyncio.sleep(0.11)

    assert provider_calls == 0
    assert published == []

    agent.state = AgentState.IDLE
    await asyncio.sleep(0.07)
    await agent.stop()

    assert provider_calls > 0
    assert published


@pytest.mark.asyncio
async def test_agent_heartbeat_loop_stops_with_agent(
    memory_manager, mcp_manager, context_builder, fast_heartbeat_floor
) -> None:
    process = FakeProcess("done")
    published = []

    agent = GeminiAgent(
        agent_id="agent-0",
        process_factory=lambda: process,
        mcp_manager=mcp_manager,
        memory_manager=memory_manager,
        context_builder=context_builder,
        heartbeat_enabled=True,
        heartbeat_interval_s=0.05,
        heartbeat_review_provider=lambda: {
            "type": "heartbeat_review",
            "summary": "ok",
            "suggestions": [],
            "stats": {},
        },
        heartbeat_event_publisher=lambda _agent_id, event: published.append(event),
    )
    await agent.start()
    await asyncio.sleep(0.08)
    await agent.stop()

    published_count_after_stop = len(published)
    await asyncio.sleep(0.08)
    assert len(published) == published_count_after_stop
    assert agent.state == AgentState.STOPPED
