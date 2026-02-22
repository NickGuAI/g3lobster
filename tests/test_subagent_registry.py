from __future__ import annotations

import time

import pytest

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.agents.registry import AgentRegistry
from g3lobster.agents.subagent_registry import RunStatus, SubagentRegistry
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import TaskStatus


class FakeAgent:
    def __init__(self, agent_id: str):
        self.id = agent_id
        self.state = AgentState.STARTING
        self.started_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.mcp_servers = ["*"]
        self.last_task = None

    async def start(self, mcp_servers=None):
        self.mcp_servers = list(mcp_servers or ["*"])
        self.state = AgentState.IDLE

    async def stop(self):
        self.state = AgentState.STOPPED

    def is_alive(self):
        return self.state != AgentState.STOPPED

    async def assign(self, task):
        self.last_task = task
        self.state = AgentState.BUSY
        self.current_task = task
        self.busy_since = time.time()
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        task.status = TaskStatus.COMPLETED
        task.result = f"delegated:{task.prompt}"
        task.completed_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.state = AgentState.IDLE
        return task


def test_register_and_complete_run(tmp_path) -> None:
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="build dashboard",
        parent_session_id="thread-1",
    )
    assert run.status == RunStatus.REGISTERED
    assert run.session_id.startswith("delegation-")

    marked = registry.mark_running(run.run_id)
    assert marked is not None
    assert marked.status == RunStatus.RUNNING

    completed = registry.complete_run(run.run_id, "done")
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert completed.result == "done"
    assert completed.completed_at is not None


def test_fail_run(tmp_path) -> None:
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="build dashboard",
        parent_session_id="thread-1",
    )
    failed = registry.fail_run(run.run_id, "boom")
    assert failed is not None
    assert failed.status == RunStatus.FAILED
    assert failed.error == "boom"


def test_timeout_detection(tmp_path) -> None:
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="slow task",
        parent_session_id="thread-1",
        timeout_s=0.1,
    )
    registry.mark_running(run.run_id)
    run.created_at = time.time() - 10

    timed_out = registry.check_timeouts()
    assert len(timed_out) == 1
    assert timed_out[0].run_id == run.run_id
    assert timed_out[0].status == RunStatus.TIMED_OUT
    assert "Timed out" in (timed_out[0].error or "")


def test_disk_persistence_and_reload(tmp_path) -> None:
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="persist me",
        parent_session_id="thread-1",
    )
    registry.mark_running(run.run_id)
    registry.complete_run(run.run_id, "ok")

    reloaded = SubagentRegistry(tmp_path)
    loaded_run = reloaded.get_run(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.COMPLETED
    assert loaded_run.result == "ok"


def test_list_runs_by_parent(tmp_path) -> None:
    registry = SubagentRegistry(tmp_path)
    a = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="a",
        parent_session_id="thread-1",
    )
    b = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="apollo",
        task="b",
        parent_session_id="thread-1",
    )
    registry.register_run(
        parent_agent_id="hestia",
        child_agent_id="apollo",
        task="c",
        parent_session_id="thread-2",
    )

    runs = registry.list_runs(parent_agent_id="athena")
    run_ids = [run.run_id for run in runs]
    assert set(run_ids) == {a.run_id, b.run_id}


def test_rejects_self_delegation(tmp_path) -> None:
    registry = SubagentRegistry(tmp_path)
    with pytest.raises(ValueError, match="Circular delegation"):
        registry.register_run(
            parent_agent_id="athena",
            child_agent_id="athena",
            task="impossible",
            parent_session_id="thread-1",
        )


def test_rejects_blank_task_and_parent_session(tmp_path) -> None:
    registry = SubagentRegistry(tmp_path)
    with pytest.raises(ValueError, match="task is required"):
        registry.register_run(
            parent_agent_id="athena",
            child_agent_id="hephaestus",
            task="   ",
            parent_session_id="thread-1",
        )

    with pytest.raises(ValueError, match="parent_session_id is required"):
        registry.register_run(
            parent_agent_id="athena",
            child_agent_id="hephaestus",
            task="build dashboard",
            parent_session_id="   ",
        )


def test_preserves_subsecond_timeout(tmp_path) -> None:
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="quick check",
        parent_session_id="thread-1",
        timeout_s=0.25,
    )

    assert run.timeout_s == 0.25


@pytest.mark.asyncio
async def test_delegate_task_auto_starts_child(tmp_path) -> None:
    data_dir = str(tmp_path / "data")

    save_persona(
        data_dir,
        AgentPersona(
            id="athena",
            name="Athena",
            soul="Research specialist.",
            enabled=True,
        ),
    )
    save_persona(
        data_dir,
        AgentPersona(
            id="hephaestus",
            name="Hephaestus",
            soul="Build specialist.",
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

    await registry.start_agent("athena")
    assert registry.get_agent("hephaestus") is None

    run = await registry.delegate_task(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task_prompt="build a React dashboard",
        parent_session_id="thread-123",
        timeout_s=30.0,
    )

    assert run.status == RunStatus.COMPLETED
    assert run.result == "delegated:build a React dashboard"
    assert run.parent_session_id == "thread-123"
    assert run.session_id.startswith("delegation-")
    assert registry.get_agent("hephaestus") is not None

    await registry.stop_all()
