"""Tests for delegation API routes and delegate_task integration."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from g3lobster.agents.registry import AgentRegistry
from g3lobster.api.server import create_app
from g3lobster.config import AppConfig
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import Task, TaskStatus


class FakeAgent:
    """Mock GeminiAgent for delegation tests."""

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


def _build_delegation_app(tmp_path: Path):
    """Build a test app with two agents pre-created for delegation tests."""
    from g3lobster.agents.persona import AgentPersona, save_persona

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

    # Create two agents on disk
    save_persona(
        str(data_dir),
        AgentPersona(id="athena", name="Athena", emoji="🦉", soul="Research agent"),
    )
    save_persona(
        str(data_dir),
        AgentPersona(id="hephaestus", name="Hephaestus", emoji="🔨", soul="Code agent"),
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"agents": {"data_dir": str(data_dir)}}), encoding="utf-8")

    app = create_app(
        registry=registry,
        chat_bridge=None,
        config=config,
        config_path=str(config_path),
        global_memory_manager=GlobalMemoryManager(str(data_dir)),
    )
    return app, registry


def test_create_delegation_run_via_api(tmp_path):
    app, registry = _build_delegation_app(tmp_path)

    with TestClient(app) as client:
        # Start both agents first
        client.post("/agents/athena/start")
        client.post("/agents/hephaestus/start")

        resp = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "build a dashboard",
                "parent_session_id": "test-session",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "Completed: build a dashboard" in data["result"]
        assert data["error"] is None
        assert "run_id" in data


def test_delegation_auto_starts_child(tmp_path):
    app, registry = _build_delegation_app(tmp_path)

    with TestClient(app) as client:
        # Only start parent agent, child should be auto-started
        client.post("/agents/athena/start")

        resp = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "auto-start test",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"] is not None


def test_delegation_circular_rejected(tmp_path):
    app, registry = _build_delegation_app(tmp_path)

    with TestClient(app) as client:
        client.post("/agents/athena/start")

        resp = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "athena",
                "task": "self-delegation",
            },
        )
        assert resp.status_code == 422
        assert "Circular" in resp.json()["detail"]


def test_get_run_status(tmp_path):
    app, registry = _build_delegation_app(tmp_path)

    with TestClient(app) as client:
        client.post("/agents/athena/start")
        client.post("/agents/hephaestus/start")

        create_resp = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "status check",
            },
        )
        run_id = create_resp.json()["run_id"]

        get_resp = client.get(f"/delegation/runs/{run_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["run_id"] == run_id
        assert data["status"] == "completed"


def test_get_nonexistent_run_returns_404(tmp_path):
    app, registry = _build_delegation_app(tmp_path)

    with TestClient(app) as client:
        resp = client.get("/delegation/runs/nonexistent-id")
        assert resp.status_code == 404


def test_list_runs_filtered_by_parent(tmp_path):
    app, registry = _build_delegation_app(tmp_path)

    with TestClient(app) as client:
        client.post("/agents/athena/start")
        client.post("/agents/hephaestus/start")

        # Create two delegations
        client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "task from athena",
            },
        )
        client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "hephaestus",
                "child_agent_id": "athena",
                "task": "task from hephaestus",
            },
        )

        # List all
        all_resp = client.get("/delegation/runs")
        assert all_resp.status_code == 200
        assert len(all_resp.json()) == 2

        # Filter by parent
        athena_resp = client.get("/delegation/runs?parent_agent_id=athena")
        assert athena_resp.status_code == 200
        athena_runs = athena_resp.json()
        assert len(athena_runs) == 1
        assert athena_runs[0]["parent"] == "athena"


def test_delegation_with_nonexistent_child(tmp_path):
    """Delegation to a non-existent agent should fail gracefully."""
    app, registry = _build_delegation_app(tmp_path)

    with TestClient(app) as client:
        client.post("/agents/athena/start")

        resp = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "nonexistent",
                "task": "impossible task",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert "Failed to start" in data["error"]
