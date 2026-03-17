from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from g3lobster.board.store import TaskItem
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


class _FakeBoardStore:
    def __init__(self, items):
        self.items = list(items)

    def list_items(
        self,
        type_filter=None,
        status_filter=None,
        agent_id=None,
        priority_filter=None,
        created_by=None,
        limit=None,
    ):
        rows = [item for item in self.items if not agent_id or item.agent_id == agent_id]
        if limit is not None:
            rows = rows[: int(limit)]
        return rows


class _CaptureEventBus:
    def __init__(self):
        self.events = []

    def publish(self, channel, event):
        self.events.append((channel, event))


@pytest.mark.asyncio
async def test_agent_heartbeat_review_emits_suggestions(memory_manager, mcp_manager, context_builder) -> None:
    stale_at = (datetime.now(tz=timezone.utc) - timedelta(hours=7)).isoformat()
    done_at = (datetime.now(tz=timezone.utc) - timedelta(minutes=20)).isoformat()
    tasks = [
        TaskItem(
            id="t-stale",
            title="Investigate auth bug",
            type="bug",
            status="in_progress",
            priority="high",
            agent_id="agent-0",
            created_by="agent",
            updated_at=stale_at,
            created_at=stale_at,
        ),
        TaskItem(
            id="t-done",
            title="Morning brief complete",
            type="chore",
            status="done",
            priority="normal",
            agent_id="agent-0",
            created_by="agent",
            updated_at=done_at,
            created_at=done_at,
        ),
    ]
    board_store = _FakeBoardStore(tasks)
    event_bus = _CaptureEventBus()

    memory_manager.write_memory("# MEMORY\n\nGoal: keep response queue clear.\n")
    memory_manager.write_procedures("# PROCEDURES\n\n## Escalate blockers\nTrigger: blocked\n")

    agent = GeminiAgent(
        agent_id="agent-0",
        process_factory=lambda: FakeProcess("ok"),
        mcp_manager=mcp_manager,
        memory_manager=memory_manager,
        context_builder=context_builder,
        board_store=board_store,
        event_bus=event_bus,
        heartbeat_interval_s=30,
    )

    event = await agent.run_heartbeat_review()
    assert event is not None
    assert event["type"] == "heartbeat_review"
    kinds = [item["kind"] for item in event["suggestions"]]
    assert "stale" in kinds
    assert "next_step" in kinds

    channels = [channel for channel, _payload in event_bus.events]
    assert "agent-0" in channels
    assert "__board__" in channels
