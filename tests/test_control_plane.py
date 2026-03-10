from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.agents.registry import AgentRegistry
from g3lobster.api.server import create_app
from g3lobster.config import AppConfig
from g3lobster.control_plane import ControlPlane, Dispatcher, Orchestrator, TaskRegistry
from g3lobster.control_plane.dispatcher import BackpressureError
from g3lobster.control_plane.task_unit import TaskUnit, TaskUnitStatus
from g3lobster.control_plane.types import Plan, PlanStep
from g3lobster.memory.global_memory import GlobalMemoryManager
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

    async def start(self, mcp_servers=None):
        self.mcp_servers = list(mcp_servers or ["*"])
        self.state = AgentState.IDLE

    async def stop(self):
        self.state = AgentState.STOPPED

    def is_alive(self):
        return self.state != AgentState.STOPPED

    async def assign(self, task):
        self.current_task = task
        self.busy_since = time.time()
        self.state = AgentState.BUSY
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        task.status = TaskStatus.COMPLETED
        task.result = f"done:{task.prompt}:by:{self.id}"
        task.completed_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.state = AgentState.IDLE
        return task


class FakeRuntime:
    def __init__(self, agent_id: str, pending: int = 0, busy: bool = False):
        self.id = agent_id
        self.pending_assignments = pending
        self.current_task = object() if busy else None

    async def assign(self, task, on_started=None):
        if callable(on_started):
            on_started()
        self.current_task = task
        task.status = TaskStatus.RUNNING
        await asyncio.sleep(0)
        task.status = TaskStatus.COMPLETED
        task.result = f"result-from-{self.id}"
        self.current_task = None
        return task


class FakeAgentRegistry:
    def __init__(self, runtimes):
        self._runtimes = {runtime.id: runtime for runtime in runtimes}

    def active_agents(self):
        return list(self._runtimes.values())

    def get_agent(self, agent_id: str):
        return self._runtimes.get(agent_id)

    async def start_agent(self, agent_id: str) -> bool:
        return False


def _build_control_plane_app(tmp_path: Path):
    data_dir = tmp_path / "data"

    config = AppConfig()
    config.agents.data_dir = str(data_dir)
    config.agents.health_check_interval_s = 3600
    config.chat.enabled = False

    registry = AgentRegistry(
        data_dir=str(data_dir),
        context_messages=config.agents.context_messages,
        health_check_interval_s=config.agents.health_check_interval_s,
        stuck_timeout_s=config.agents.stuck_timeout_s,
        queue_depth_limit=config.control_plane.queue_depth,
        agent_factory=lambda persona, _memory, _context: FakeAgent(persona.id),
    )

    task_registry = TaskRegistry(max_tasks=100)
    dispatcher = Dispatcher(
        agent_registry=registry,
        task_registry=task_registry,
        max_queue_depth=config.control_plane.queue_depth,
    )
    orchestrator = Orchestrator(task_registry=task_registry, dispatcher=dispatcher)
    dispatcher.set_on_task_complete(orchestrator.on_task_complete)

    control_plane = ControlPlane(
        task_registry=task_registry,
        dispatcher=dispatcher,
        orchestrator=orchestrator,
        tmux_spawner=None,
    )
    registry.control_plane = control_plane

    app = create_app(
        registry=registry,
        config=config,
        global_memory_manager=GlobalMemoryManager(str(data_dir)),
        control_plane=control_plane,
    )
    return app, data_dir


def test_task_unit_state_machine_transitions() -> None:
    task = TaskUnit(prompt="hello")

    task.transition(TaskUnitStatus.QUEUED, agent_id="alpha")
    assert task.status == TaskUnitStatus.QUEUED
    assert task.agent_id == "alpha"

    task.transition(TaskUnitStatus.WORKING)
    assert task.started_at is not None

    task.transition(TaskUnitStatus.COMPLETED, result_md="ok")
    assert task.status == TaskUnitStatus.COMPLETED
    assert task.completed_at is not None
    assert task.result_md == "ok"


def test_task_unit_rejects_invalid_transition() -> None:
    task = TaskUnit(prompt="hello")
    with pytest.raises(ValueError):
        task.transition(TaskUnitStatus.WORKING)


@pytest.mark.asyncio
async def test_dispatcher_selects_least_busy_agent() -> None:
    registry = TaskRegistry()
    dispatcher = Dispatcher(
        agent_registry=FakeAgentRegistry([
            FakeRuntime("busy", pending=3),
            FakeRuntime("idle", pending=0),
        ]),
        task_registry=registry,
        max_queue_depth=5,
    )

    task = TaskUnit(prompt="route this")
    registry.add(task)

    selected = await dispatcher.dispatch(task)
    assert selected == "idle"

    completed = await registry.wait_for_terminal(task.id, timeout_s=1.0)
    assert completed.status == TaskUnitStatus.COMPLETED
    assert completed.agent_id == "idle"


@pytest.mark.asyncio
async def test_dispatcher_respects_backpressure() -> None:
    registry = TaskRegistry()
    dispatcher = Dispatcher(
        agent_registry=FakeAgentRegistry([
            FakeRuntime("a", pending=0, busy=True),
            FakeRuntime("b", pending=0, busy=True),
        ]),
        task_registry=registry,
        max_queue_depth=1,
    )

    task = TaskUnit(prompt="cannot run")
    registry.add(task)

    with pytest.raises(BackpressureError):
        await dispatcher.dispatch(task)


@pytest.mark.asyncio
async def test_orchestrator_aggregates_plan_results() -> None:
    registry = TaskRegistry()
    dispatcher = Dispatcher(
        agent_registry=FakeAgentRegistry([FakeRuntime("alpha"), FakeRuntime("beta")]),
        task_registry=registry,
        max_queue_depth=5,
    )
    orchestrator = Orchestrator(task_registry=registry, dispatcher=dispatcher)
    dispatcher.set_on_task_complete(orchestrator.on_task_complete)

    parent = TaskUnit(prompt="root")
    registry.add(parent)

    plan = Plan(
        steps=[
            PlanStep(id="research", prompt="research the topic"),
            PlanStep(id="implement", prompt="implement the plan", depends_on=["research"]),
        ]
    )

    await orchestrator.execute_plan(plan, parent)

    done = await registry.wait_for_terminal(parent.id, timeout_s=1.0)
    assert done.status == TaskUnitStatus.COMPLETED
    assert "### research" in (done.result_md or "")
    assert "### implement" in (done.result_md or "")


def test_control_plane_routes_submit_status_and_delegate(tmp_path: Path) -> None:
    app, data_dir = _build_control_plane_app(tmp_path)
    save_persona(str(data_dir), AgentPersona(id="athena", name="Athena", soul="Research"))
    save_persona(str(data_dir), AgentPersona(id="hermes", name="Hermes", soul="Execute"))

    with TestClient(app) as client:
        submit = client.post(
            "/control-plane/tasks",
            json={"prompt": "hello world", "wait": True},
        )
        assert submit.status_code == 200
        submit_data = submit.json()
        assert submit_data["status"] == "completed"
        assert submit_data["agent_id"] in {"athena", "hermes"}

        status = client.get("/control-plane/status")
        assert status.status_code == 200
        status_payload = status.json()
        assert "dispatcher" in status_payload
        assert "tasks" in status_payload
        assert status_payload["tasks"]["by_status"]["completed"] >= 1

        delegate = client.post(
            "/control-plane/delegate",
            json={
                "parent_agent_id": "athena",
                "agent_name": "hermes",
                "prompt": "delegate this",
                "wait": True,
            },
        )
        assert delegate.status_code == 200
        delegate_data = delegate.json()
        assert delegate_data["status"] == "completed"
        assert delegate_data["agent_id"] == "hermes"

        sessions = client.get("/control-plane/sessions")
        assert sessions.status_code == 200
        assert sessions.json() == {"sessions": []}
