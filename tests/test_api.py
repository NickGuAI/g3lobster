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
from g3lobster.tasks.types import Task, TaskStatus
from g3lobster.tmux.spawner import SubAgentRunInfo, SubAgentStatus


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
    def __init__(
        self,
        space_id=None,
        service=None,
        last_message_time=None,
        seen_content=None,
        agent_filter=None,
    ):
        self.space_id = space_id
        self.service = service
        self._last_message_time = last_message_time
        self._seen_content = seen_content or set()
        self.agent_filter = set(agent_filter or set())
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

    def set_agent_filter(self, agent_ids):
        self.agent_filter = set(agent_ids or set())

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

    def bridge_factory(space_id, service=None, last_message_time=None, seen_content=None, agent_filter=None):
        bridge = FakeChatBridge(
            space_id=space_id,
            service=service,
            last_message_time=last_message_time,
            seen_content=seen_content,
            agent_filter=agent_filter,
        )
        bridge_instances.append(bridge)
        return bridge

    bridge_manager = BridgeManager(
        registry=registry,
        bridge_factory=bridge_factory,
        legacy_space_id=config.chat.space_id,
    )

    app = create_app(
        registry=registry,
        bridge_manager=bridge_manager,
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
        assert create.json()["space_id"] == "spaces/test-space"
        assert create.json()["bridge_enabled"] is True
        assert create.json()["bridge_running"] is False

        listing = client.get("/agents")
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()] == [agent_id]
        assert listing.json()[0]["space_id"] == "spaces/test-space"
        assert listing.json()[0]["bridge_enabled"] is True

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
        assert create_agent.json()["space_id"] == "spaces/new"
        assert create_agent.json()["bridge_enabled"] is True

        started_agent = client.post(f"/agents/{agent_id}/start")
        assert started_agent.status_code == 200

        start_bridge = client.post(f"/setup/start?agent_id={agent_id}")
        assert start_bridge.status_code == 200
        assert start_bridge.json() == {"started": True}

        assert bridge_instances
        assert bridge_instances[-1].started == 1

        status = client.get("/setup/status")
        assert status.status_code == 200
        assert status.json()["bridge_running"] is True
        iris_bridge = next(item for item in status.json()["agent_bridges"] if item["agent_id"] == agent_id)
        assert iris_bridge["space_id"] == "spaces/new"
        assert iris_bridge["is_running"] is True

        stop_bridge = client.post(f"/setup/stop?agent_id={agent_id}")
        assert stop_bridge.status_code == 200
        assert stop_bridge.json() == {"stopped": True}

        status_after_stop = client.get("/setup/status")
        assert status_after_stop.status_code == 200
        assert status_after_stop.json()["bridge_running"] is False

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["chat"]["enabled"] is False
    assert saved["chat"]["space_id"] == "spaces/new"
    assert saved["chat"]["space_name"] == "Ops"


def test_task_routes_list_detail_cancel(tmp_path):
    app, _bridge_instances, _config_path = _build_test_app(tmp_path)

    with TestClient(app) as client:
        create = client.post(
            "/agents",
            json={
                "name": "Delta",
                "emoji": "🦀",
                "soul": "Task router test.",
                "model": "gemini",
                "mcp_servers": ["*"],
            },
        )
        assert create.status_code == 200
        agent_id = create.json()["id"]

        start = client.post(f"/agents/{agent_id}/start")
        assert start.status_code == 200

        runtime = app.state.registry.get_agent(agent_id)
        assert runtime is not None

        completed_task = Task(prompt="done", session_id="thread-complete", agent_id=agent_id)
        completed_task.status = TaskStatus.COMPLETED
        completed_task.completed_at = time.time()
        app.state.registry.task_store.add(completed_task)

        running_task = Task(prompt="long", session_id="thread-run", agent_id=agent_id)
        running_task.status = TaskStatus.RUNNING
        running_task.started_at = time.time()
        runtime.agent.current_task = running_task

        async def _cancel(task_id: str):
            if runtime.agent.current_task and runtime.agent.current_task.id == task_id:
                runtime.agent.current_task.status = TaskStatus.CANCELED
                runtime.agent.current_task.completed_at = time.time()
                runtime.agent.current_task.error = "Canceled by API request"
                runtime.agent.current_task.add_event("canceled", {"reason": "test"})
                task = runtime.agent.current_task
                runtime.agent.current_task = None
                return task
            return None

        runtime.agent.cancel_task = _cancel

        listing = client.get(f"/agents/{agent_id}/tasks")
        assert listing.status_code == 200
        task_ids = [item["id"] for item in listing.json()["tasks"]]
        assert running_task.id in task_ids
        assert completed_task.id in task_ids

        detail = client.get(f"/agents/{agent_id}/tasks/{completed_task.id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "completed"

        cancel = client.post(f"/agents/{agent_id}/tasks/{running_task.id}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "canceled"


def test_subagent_routes(tmp_path, monkeypatch):
    app, _bridge_instances, _config_path = _build_test_app(tmp_path)

    with TestClient(app) as client:
        create = client.post(
            "/agents",
            json={
                "name": "Sigma",
                "emoji": "🛠️",
                "soul": "Subagent route test.",
                "model": "gemini",
                "mcp_servers": ["*"],
            },
        )
        assert create.status_code == 200
        agent_id = create.json()["id"]
        assert client.post(f"/agents/{agent_id}/start").status_code == 200

        run = SubAgentRunInfo(
            session_name="g3l-sigma-abc12345",
            agent_id=agent_id,
            prompt="Summarize logs",
            mcp_server_names=["*"],
            status=SubAgentStatus.RUNNING,
            timeout_s=60.0,
        )

        async def _spawn_subagent(**_kwargs):
            return run

        async def _list_subagents(**_kwargs):
            return [run]

        async def _kill_subagent(**_kwargs):
            run.status = SubAgentStatus.CANCELED
            run.completed_at = time.time()
            run.error = "Killed by API request"
            return run

        monkeypatch.setattr(app.state.registry, "spawn_subagent", _spawn_subagent)
        monkeypatch.setattr(app.state.registry, "list_subagents", _list_subagents)
        monkeypatch.setattr(app.state.registry, "kill_subagent", _kill_subagent)

        spawned = client.post(
            f"/agents/{agent_id}/subagents",
            json={"prompt": "Summarize logs", "timeout_s": 60},
        )
        assert spawned.status_code == 200
        assert spawned.json()["session_name"] == run.session_name

        listed = client.get(f"/agents/{agent_id}/subagents")
        assert listed.status_code == 200
        assert listed.json()[0]["status"] == "running"

        killed = client.delete(f"/agents/{agent_id}/subagents/{run.session_name}")
        assert killed.status_code == 200
        assert killed.json()["status"] == "canceled"


def test_memory_search_and_tag_routes(tmp_path):
    app, _bridge_instances, _config_path = _build_test_app(tmp_path)

    with TestClient(app) as client:
        create_alpha = client.post(
            "/agents",
            json={
                "name": "Alpha",
                "emoji": "🦀",
                "soul": "Memory search alpha.",
                "model": "gemini",
                "mcp_servers": ["*"],
            },
        )
        create_beta = client.post(
            "/agents",
            json={
                "name": "Beta",
                "emoji": "🦀",
                "soul": "Memory search beta.",
                "model": "gemini",
                "mcp_servers": ["*"],
            },
        )
        alpha_id = create_alpha.json()["id"]
        beta_id = create_beta.json()["id"]
        assert client.post(f"/agents/{alpha_id}/start").status_code == 200
        assert client.post(f"/agents/{beta_id}/start").status_code == 200

        assert client.put(
            f"/agents/{alpha_id}/memory",
            json={"content": "# MEMORY\n\nalpha-keyword present"},
        ).status_code == 200
        assert client.put(
            f"/agents/{beta_id}/memory",
            json={"content": "# MEMORY\n\nbeta-keyword and alpha-keyword"},
        ).status_code == 200

        tagged = client.post(
            f"/agents/{alpha_id}/memory/tags/release",
            json={"content": "Ship on Friday."},
        )
        assert tagged.status_code == 200

        tagged_read = client.get(f"/agents/{alpha_id}/memory/tags/release")
        assert tagged_read.status_code == 200
        assert tagged_read.json()["entries"] == ["Ship on Friday."]

        runtime = app.state.registry.get_agent(alpha_id)
        assert runtime is not None
        runtime.memory_manager.append_daily_note("Daily alpha-keyword note.")
        runtime.memory_manager.append_message("thread-alpha", "user", "session alpha-keyword")

        single = client.get(
            f"/agents/{alpha_id}/memory/search",
            params={"q": "alpha-keyword", "memory_type": ["memory", "daily", "session"]},
        )
        assert single.status_code == 200
        assert single.json()["results"]
        assert {item["agent_id"] for item in single.json()["results"]} == {alpha_id}

        cross = client.post("/agents/memory/search", json={"query": "alpha-keyword", "limit": 50})
        assert cross.status_code == 200
        found_agents = {item["agent_id"] for item in cross.json()["results"]}
        assert alpha_id in found_agents
        assert beta_id in found_agents
