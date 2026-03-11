"""Tests for the GET /agents/{id}/decisions API endpoint."""

from __future__ import annotations

import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from g3lobster.agents.registry import AgentRegistry
from g3lobster.api.server import create_app
from g3lobster.chat.bridge_manager import BridgeManager
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
        task.status = TaskStatus.COMPLETED
        task.result = "ok"
        task.completed_at = time.time()
        self.state = AgentState.IDLE
        return task


class FakeChatBridge:
    def __init__(self, **kwargs):
        self.messages = []
        self.space_id = kwargs.get("space_id")

    async def send_message(self, msg):
        self.messages.append(msg)


def _build_test_app(tmp_path: Path):
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"version": "1.0"}))

    config = AppConfig()
    config.agents.data_dir = str(data_dir)
    config.chat.enabled = False
    config.chat.space_id = "spaces/test-space"

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

    def bridge_factory(**kwargs):
        return FakeChatBridge(**kwargs)

    bridge_manager = BridgeManager(
        registry=registry,
        bridge_factory=lambda space_id, **kw: FakeChatBridge(space_id=space_id, **kw),
        legacy_space_id=config.chat.space_id,
    )

    app = create_app(
        registry=registry,
        bridge_manager=bridge_manager,
        chat_bridge_factory=lambda **kw: FakeChatBridge(**kw),
        config=config,
        config_path=str(config_path),
        chat_auth_dir=str(tmp_path / "auth"),
        global_memory_manager=GlobalMemoryManager(str(data_dir)),
    )
    return app


def test_decisions_endpoint_empty(tmp_path: Path) -> None:
    app = _build_test_app(tmp_path)

    with TestClient(app) as client:
        # Create an agent first
        create = client.post(
            "/agents",
            json={"name": "DecBot", "emoji": "📝", "soul": "Decision logger"},
        )
        assert create.status_code == 200
        agent_id = create.json()["id"]

        # Query decisions (empty)
        resp = client.get(f"/agents/{agent_id}/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["decisions"] == []


def test_decisions_endpoint_with_data(tmp_path: Path) -> None:
    app = _build_test_app(tmp_path)

    with TestClient(app) as client:
        # Create an agent
        create = client.post(
            "/agents",
            json={"name": "DecBot2", "emoji": "📝", "soul": "Decision logger"},
        )
        assert create.status_code == 200
        agent_id = create.json()["id"]

        # Manually write a decision to the agent's decision log
        from g3lobster.agents.persona import agent_dir
        from g3lobster.memory.decisions import DecisionLog

        config = app.state.config
        log = DecisionLog(str(Path(agent_dir(config.agents.data_dir, agent_id)) / ".memory"))
        log.append(session_id="s1", decision="Use Redis for caching", tags=["infra"])
        log.append(session_id="s2", decision="Use PostgreSQL for storage", tags=["db"])

        # Query all decisions
        resp = client.get(f"/agents/{agent_id}/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["decisions"]) == 2

        # Query with keyword filter
        resp = client.get(f"/agents/{agent_id}/decisions", params={"q": "Redis"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["decisions"]) == 1
        assert "Redis" in data["decisions"][0]["decision"]

        # Query with limit
        resp = client.get(f"/agents/{agent_id}/decisions", params={"limit": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["decisions"]) == 1
