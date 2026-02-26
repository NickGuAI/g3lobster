"""Tests for the delegation API routes and delegate_task integration."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.agents.registry import AgentRegistry
from g3lobster.agents.subagent_registry import RunStatus
from g3lobster.api.server import create_app
from g3lobster.config import AppConfig
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
        task.result = f"Completed: {task.prompt}"
        task.completed_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.state = AgentState.IDLE
        return task


def _build_test_app(tmp_path: Path):
    data_dir = tmp_path / "data"

    config = AppConfig()
    config.agents.data_dir = str(data_dir)
    config.agents.compact_threshold = 8
    config.agents.compact_keep_ratio = 0.25
    config.agents.compact_chunk_size = 4
    config.agents.procedure_min_frequency = 3
    config.agents.context_messages = 6
    config.agents.health_check_interval_s = 3600
    config.agents.stuck_timeout_s = 120
    config.chat.enabled = False

    registry = AgentRegistry(
        data_dir=str(data_dir),
        compact_threshold=config.agents.compact_threshold,
        compact_keep_ratio=config.agents.compact_keep_ratio,
        compact_chunk_size=config.agents.compact_chunk_size,
        procedure_min_frequency=config.agents.procedure_min_frequency,
        memory_max_sections=config.agents.memory_max_sections,
        context_messages=config.agents.context_messages,
        health_check_interval_s=config.agents.health_check_interval_s,
        stuck_timeout_s=config.agents.stuck_timeout_s,
        agent_factory=lambda persona, _memory, _context: FakeAgent(persona.id),
    )

    app = create_app(
        registry=registry,
        chat_bridge=None,
        config=config,
        global_memory_manager=GlobalMemoryManager(str(data_dir)),
    )
    return app, registry, data_dir


def _create_agent_persona(data_dir: Path, agent_id: str, name: str, soul: str = ""):
    persona = AgentPersona(id=agent_id, name=name, soul=soul)
    save_persona(str(data_dir), persona)
    return persona


def test_create_delegation_run_via_api(tmp_path):
    app, registry, data_dir = _build_test_app(tmp_path)

    _create_agent_persona(data_dir, "athena", "Athena", soul="Research agent")
    _create_agent_persona(data_dir, "hephaestus", "Hephaestus", soul="Code agent")

    with TestClient(app) as client:
        # Start parent agent
        client.post("/agents/athena/start")

        response = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "build a dashboard",
                "parent_session_id": "session-1",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"]
        assert data["status"] == "completed"
        assert "build a dashboard" in data["result"]
        assert data["error"] is None


def test_get_run_status(tmp_path):
    app, registry, data_dir = _build_test_app(tmp_path)

    _create_agent_persona(data_dir, "athena", "Athena")
    _create_agent_persona(data_dir, "hephaestus", "Hephaestus")

    with TestClient(app) as client:
        client.post("/agents/athena/start")

        create_resp = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "test task",
                "parent_session_id": "session-1",
            },
        )
        run_id = create_resp.json()["run_id"]

        get_resp = client.get(f"/delegation/runs/{run_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["run_id"] == run_id
        assert data["status"] == "completed"


def test_run_not_found(tmp_path):
    app, _registry, _data_dir = _build_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/delegation/runs/nonexistent-id")
        assert response.status_code == 404


def test_list_runs_filtered_by_parent(tmp_path):
    app, registry, data_dir = _build_test_app(tmp_path)

    _create_agent_persona(data_dir, "athena", "Athena")
    _create_agent_persona(data_dir, "hermes", "Hermes")
    _create_agent_persona(data_dir, "hephaestus", "Hephaestus")

    with TestClient(app) as client:
        client.post("/agents/athena/start")
        client.post("/agents/hermes/start")

        client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "athena task",
                "parent_session_id": "session-1",
            },
        )
        client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "hermes",
                "child_agent_id": "hephaestus",
                "task": "hermes task",
                "parent_session_id": "session-2",
            },
        )

        # List all runs
        all_resp = client.get("/delegation/runs")
        assert all_resp.status_code == 200
        assert len(all_resp.json()) == 2

        # Filter by parent
        athena_resp = client.get("/delegation/runs?parent_agent_id=athena")
        assert athena_resp.status_code == 200
        athena_runs = athena_resp.json()
        assert len(athena_runs) == 1
        assert athena_runs[0]["parent"] == "athena"


def test_circular_delegation_rejected(tmp_path):
    app, registry, data_dir = _build_test_app(tmp_path)

    _create_agent_persona(data_dir, "athena", "Athena")

    with TestClient(app) as client:
        client.post("/agents/athena/start")

        response = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "athena",
                "task": "delegate to self",
                "parent_session_id": "session-1",
            },
        )
        assert response.status_code == 422
        assert "Circular" in response.json()["detail"]


def test_missing_required_fields(tmp_path):
    app, _registry, _data_dir = _build_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/delegation/run",
            json={"parent_agent_id": "athena"},
        )
        assert response.status_code == 422


def test_delegate_task_auto_starts_child(tmp_path):
    app, registry, data_dir = _build_test_app(tmp_path)

    _create_agent_persona(data_dir, "athena", "Athena")

    with TestClient(app) as client:
        # Create hephaestus after lifespan start_all so it's not auto-started
        _create_agent_persona(data_dir, "hephaestus", "Hephaestus")

        # Stop hephaestus in case it was started by start_all
        client.post("/agents/hephaestus/stop")

        # Child should not be running
        assert registry.get_agent("hephaestus") is None

        response = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "auto-start test",
                "parent_session_id": "session-1",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"

        # Child should now be running
        assert registry.get_agent("hephaestus") is not None


def test_delegate_task_child_not_found(tmp_path):
    app, registry, data_dir = _build_test_app(tmp_path)

    _create_agent_persona(data_dir, "athena", "Athena")
    # Don't create hephaestus persona

    with TestClient(app) as client:
        client.post("/agents/athena/start")

        response = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "missing child",
                "parent_session_id": "session-1",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "Failed to start" in data["error"]
