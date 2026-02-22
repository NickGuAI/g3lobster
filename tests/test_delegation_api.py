from __future__ import annotations

import time

from fastapi.testclient import TestClient

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.agents.registry import AgentRegistry
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
        task.result = f"ok:{self.id}:{task.prompt}"
        task.completed_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.state = AgentState.IDLE
        return task


def _build_app(tmp_path):
    data_dir = str(tmp_path / "data")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")

    save_persona(
        data_dir,
        AgentPersona(id="athena", name="Athena", soul="Research planner.", enabled=True),
    )
    save_persona(
        data_dir,
        AgentPersona(id="hephaestus", name="Hephaestus", soul="Code builder.", enabled=True),
    )
    save_persona(
        data_dir,
        AgentPersona(id="poseidon", name="Poseidon", soul="Ops specialist.", enabled=True),
    )

    config = AppConfig()
    config.agents.data_dir = data_dir
    config.agents.context_messages = 6
    config.agents.health_check_interval_s = 3600
    config.agents.stuck_timeout_s = 60

    registry = AgentRegistry(
        data_dir=data_dir,
        context_messages=config.agents.context_messages,
        health_check_interval_s=config.agents.health_check_interval_s,
        stuck_timeout_s=config.agents.stuck_timeout_s,
        agent_factory=lambda persona, _memory, _context: FakeAgent(persona.id),
    )

    app = create_app(
        registry=registry,
        chat_bridge=None,
        chat_bridge_factory=None,
        config=config,
        config_path=str(config_path),
        chat_auth_dir=str(tmp_path / "chat_auth"),
        global_memory_manager=GlobalMemoryManager(data_dir),
    )
    return app


def test_create_delegation_run_via_api(tmp_path) -> None:
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "build dashboard",
                "parent_session_id": "thread-1",
                "timeout_s": 45.0,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"]
        assert payload["status"] == "completed"
        assert payload["result"] == "ok:hephaestus:build dashboard"
        assert payload["error"] is None


def test_get_run_status(tmp_path) -> None:
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        create = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "ship release",
                "parent_session_id": "thread-2",
            },
        )
        run_id = create.json()["run_id"]

        detail = client.get(f"/delegation/runs/{run_id}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["run_id"] == run_id
        assert payload["status"] == "completed"
        assert payload["result"] == "ok:hephaestus:ship release"


def test_list_runs_filtered_by_parent(tmp_path) -> None:
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "task one",
                "parent_session_id": "thread-a",
            },
        )
        client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "poseidon",
                "child_agent_id": "hephaestus",
                "task": "task two",
                "parent_session_id": "thread-b",
            },
        )

        all_runs = client.get("/delegation/runs")
        assert all_runs.status_code == 200
        assert len(all_runs.json()) == 2

        filtered = client.get("/delegation/runs", params={"parent_agent_id": "athena"})
        assert filtered.status_code == 200
        items = filtered.json()
        assert len(items) == 1
        assert items[0]["parent"] == "athena"
        assert items[0]["child"] == "hephaestus"
        assert items[0]["status"] == "completed"


def test_create_delegation_rejects_self_target(tmp_path) -> None:
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "athena",
                "task": "loop forever",
                "parent_session_id": "thread-3",
            },
        )
        assert response.status_code == 422
        assert "circular delegation" in response.json()["detail"].lower()


def test_create_delegation_rejects_blank_task(tmp_path) -> None:
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/delegation/run",
            json={
                "parent_agent_id": "athena",
                "child_agent_id": "hephaestus",
                "task": "   ",
                "parent_session_id": "thread-4",
            },
        )
        assert response.status_code == 422
        assert "task is required" in response.json()["detail"].lower()
