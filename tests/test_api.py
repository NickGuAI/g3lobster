from __future__ import annotations

import time
from pathlib import Path

import yaml

from fastapi.testclient import TestClient

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
        task.result = "ok"
        task.completed_at = time.time()
        self.current_task = None
        self.busy_since = None
        self.state = AgentState.IDLE
        return task


class DummyPollTask:
    def __init__(self):
        self._done = False

    def done(self):
        return self._done


class FakeChatBridge:
    def __init__(self, space_id=None, service=None, last_message_time=None, seen_content=None):
        self.space_id = space_id
        self.service = service
        self._last_message_time = last_message_time
        self._seen_content = seen_content or set()
        self._poll_task = None
        self.started = 0
        self.stopped = 0
        self.sent = []

    async def start(self):
        self.started += 1
        self._poll_task = DummyPollTask()

    @property
    def is_running(self):
        return bool(self._poll_task and not self._poll_task.done())

    async def stop(self):
        self.stopped += 1
        if self._poll_task:
            self._poll_task._done = True

    async def send_message(self, text: str, thread_id=None):
        self.sent.append({"text": text, "thread_id": thread_id})


def _write_test_config(path: Path) -> None:
    payload = {
        "agents": {
            "data_dir": "./data",
            "compact_threshold": 8,
            "compact_keep_ratio": 0.25,
            "compact_chunk_size": 4,
            "procedure_min_frequency": 3,
            "memory_max_sections": 50,
            "context_messages": 6,
            "health_check_interval_s": 3600,
            "stuck_timeout_s": 120,
        },
        "mcp": {"config_dir": "./config/mcp"},
        "chat": {
            "enabled": False,
            "space_id": "spaces/test-space",
            "space_name": "Test Space",
            "poll_interval_s": 1.5,
        },
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _build_test_app(tmp_path: Path):
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    chat_auth_dir = tmp_path / "chat_auth"
    _write_test_config(config_path)

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
    config.chat.space_id = "spaces/test-space"
    config.chat.space_name = "Test Space"
    config.chat.poll_interval_s = 1.5

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

    bridge_instances = []

    def bridge_factory(service=None, last_message_time=None, seen_content=None):
        bridge = FakeChatBridge(
            space_id=config.chat.space_id,
            service=service,
            last_message_time=last_message_time,
            seen_content=seen_content,
        )
        bridge_instances.append(bridge)
        return bridge

    app = create_app(
        registry=registry,
        chat_bridge=None,
        chat_bridge_factory=bridge_factory,
        config=config,
        config_path=str(config_path),
        chat_auth_dir=str(chat_auth_dir),
        global_memory_manager=GlobalMemoryManager(str(data_dir)),
    )
    return app, bridge_instances, config_path


def test_agents_routes_crud_and_memory(tmp_path):
    app, _bridge_instances, _config_path = _build_test_app(tmp_path)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        create = client.post(
            "/agents",
            json={
                "name": "Luna",
                "emoji": "🦀",
                "soul": "Stay concise.",
                "model": "gemini",
                "mcp_servers": ["*"],
            },
        )
        assert create.status_code == 200
        agent_id = create.json()["id"]

        listing = client.get("/agents")
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()] == [agent_id]

        detail = client.get(f"/agents/{agent_id}")
        assert detail.status_code == 200
        assert detail.json()["soul"] == "Stay concise."

        updated = client.put(
            f"/agents/{agent_id}",
            json={"name": "Luna Prime", "soul": "Be exact.", "mcp_servers": ["gmail"]},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Luna Prime"
        assert updated.json()["mcp_servers"] == ["gmail"]

        start = client.post(f"/agents/{agent_id}/start")
        assert start.status_code == 200
        assert start.json() == {"started": True}

        write_memory = client.put(
            f"/agents/{agent_id}/memory",
            json={"content": "# MEMORY\n\nRemember this."},
        )
        assert write_memory.status_code == 200

        read_memory = client.get(f"/agents/{agent_id}/memory")
        assert read_memory.status_code == 200
        assert "Remember this" in read_memory.json()["content"]

        write_procedures = client.put(
            f"/agents/{agent_id}/procedures",
            json={
                "content": (
                    "# PROCEDURES\n\n"
                    "## Deploy\n"
                    "Trigger: deploy\n\n"
                    "Steps:\n"
                    "1. Check git status\n"
                    "2. Run tests\n"
                    "3. Deploy\n"
                )
            },
        )
        assert write_procedures.status_code == 200

        bad_procedures = client.put(
            f"/agents/{agent_id}/procedures",
            json={"content": "# PROCEDURES\n\nthis is unstructured text\n"},
        )
        assert bad_procedures.status_code == 422

        read_procedures = client.get(f"/agents/{agent_id}/procedures")
        assert read_procedures.status_code == 200
        assert "Deploy" in read_procedures.json()["content"]

        sessions = client.get(f"/agents/{agent_id}/sessions")
        assert sessions.status_code == 200
        assert sessions.json() == {"sessions": []}

        set_global = client.put(
            "/agents/_global/user-memory",
            json={"content": "# USER\n\nUse terse answers."},
        )
        assert set_global.status_code == 200

        get_global = client.get("/agents/_global/user-memory")
        assert get_global.status_code == 200
        assert "Use terse answers" in get_global.json()["content"]

        legacy_global = client.get("/agents/global/user-memory")
        assert legacy_global.status_code == 404

        set_global_procedures = client.put(
            "/agents/_global/procedures",
            json={"content": "# PROCEDURES\n\n## Deploy\nTrigger: deploy app\n"},
        )
        assert set_global_procedures.status_code == 200

        get_global_knowledge = client.get("/agents/_global/knowledge")
        assert get_global_knowledge.status_code == 200
        assert get_global_knowledge.json() == {"items": []}

        link = client.post(f"/agents/{agent_id}/link-bot", json={"bot_user_id": "users/777"})
        assert link.status_code == 200
        assert link.json() == {"linked": True, "bot_user_id": "users/777"}

        stop = client.post(f"/agents/{agent_id}/stop")
        assert stop.status_code == 200
        assert stop.json() == {"stopped": True}

        delete = client.delete(f"/agents/{agent_id}")
        assert delete.status_code == 200
        assert delete.json() == {"deleted": True}

        ui = client.get("/ui")
        assert ui.status_code == 200
        assert "Google Chat Agent Console" in ui.text


def test_create_agent_rejects_reserved_global_id(tmp_path):
    app, _bridge_instances, _config_path = _build_test_app(tmp_path)

    with TestClient(app) as client:
        create = client.post(
            "/agents",
            json={
                "name": "Global",
                "emoji": "🌐",
                "soul": "Reserved id probe.",
                "model": "gemini",
                "mcp_servers": ["*"],
            },
        )
        assert create.status_code == 422
        assert "reserved" in create.json()["detail"].lower()

        set_global = client.put(
            "/agents/_global/user-memory",
            json={"content": "# USER\n\nUse terse answers."},
        )
        assert set_global.status_code == 200

        get_global = client.get("/agents/_global/user-memory")
        assert get_global.status_code == 200
        assert "Use terse answers" in get_global.json()["content"]


def test_setup_routes_bridge_lifecycle(monkeypatch, tmp_path):
    app, bridge_instances, config_path = _build_test_app(tmp_path)

    monkeypatch.setattr("g3lobster.api.routes_setup.create_authorization_url", lambda _data_dir: "https://example.test/auth")
    monkeypatch.setattr("g3lobster.api.routes_setup.get_authenticated_service", lambda _data_dir: object())

    def _complete_auth(data_dir: str, _code: str):
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        token = path / "token.json"
        token.write_text('{"token": "ok"}', encoding="utf-8")
        return token

    monkeypatch.setattr("g3lobster.api.routes_setup.complete_authorization", _complete_auth)

    with TestClient(app) as client:
        upload = client.post("/setup/credentials", json={"credentials": {"installed": {"client_id": "x"}}})
        assert upload.status_code == 200

        status = client.get("/setup/status")
        assert status.status_code == 200
        assert status.json()["credentials_ok"] is True
        assert status.json()["auth_ok"] is False

        auth = client.get("/setup/test-auth")
        assert auth.status_code == 200
        assert auth.json() == {"authenticated": False, "auth_url": "https://example.test/auth"}

        complete = client.post("/setup/complete-auth", json={"code": "abc"})
        assert complete.status_code == 200
        assert complete.json() == {"authenticated": True}

        space = client.post("/setup/space", json={"space_id": "spaces/new", "space_name": "Ops"})
        assert space.status_code == 200
        assert space.json() == {"configured": True, "space_id": "spaces/new"}

        create_agent = client.post(
            "/agents",
            json={
                "name": "Iris",
                "emoji": "🧭",
                "soul": "Guide setup.",
                "model": "gemini",
                "mcp_servers": ["*"],
            },
        )
        assert create_agent.status_code == 200
        agent_id = create_agent.json()["id"]

        started_agent = client.post(f"/agents/{agent_id}/start")
        assert started_agent.status_code == 200

        start_bridge = client.post("/setup/start")
        assert start_bridge.status_code == 200
        assert start_bridge.json() == {"started": True}

        assert bridge_instances
        assert bridge_instances[-1].started == 1

        status = client.get("/setup/status")
        assert status.status_code == 200
        assert status.json()["bridge_running"] is True

        stop_bridge = client.post("/setup/stop")
        assert stop_bridge.status_code == 200
        assert stop_bridge.json() == {"stopped": True}

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["chat"]["enabled"] is False
    assert saved["chat"]["space_id"] == "spaces/new"
    assert saved["chat"]["space_name"] == "Ops"
