from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from g3lobster.agents.persona import AgentPersona, load_persona, save_persona
from g3lobster.chat.bridge import ChatBridge
from g3lobster.cli.process import GeminiProcess
from g3lobster.config import load_config
from g3lobster.pool.agent import GeminiAgent
from g3lobster.pool.health import HealthInspector
from g3lobster.pool.types import AgentState
from g3lobster.tasks.types import Task, TaskStatus


@pytest.mark.asyncio
async def test_gemini_process_ask_timeout_zero_skips_wait_for(monkeypatch) -> None:
    wait_for_calls: list[float | None] = []
    original_wait_for = asyncio.wait_for

    async def fake_wait_for(awaitable, timeout=None):
        wait_for_calls.append(timeout)
        return await original_wait_for(awaitable, timeout=timeout)

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return (b"ok", b"")

        async def wait(self):
            return 0

        def kill(self):
            self.returncode = -9

    async def fake_exec(*_args, **_kwargs):
        return DummyProcess()

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    proc = GeminiProcess(command="gemini", args=["-y"])
    await proc.spawn()

    assert await proc.ask("hello", timeout=0) == "ok"
    assert wait_for_calls == []


@pytest.mark.asyncio
async def test_gemini_process_ask_timeout_still_enforced(monkeypatch) -> None:
    class SlowProcess:
        def __init__(self):
            self.returncode = None
            self.killed = False

        async def communicate(self):
            await asyncio.sleep(0.1)
            self.returncode = 0
            return (b"ok", b"")

        async def wait(self):
            if self.returncode is None:
                self.returncode = -9
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    slow = SlowProcess()

    async def fake_exec(*_args, **_kwargs):
        return slow

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    proc = GeminiProcess(command="gemini", args=["-y"])
    await proc.spawn()

    with pytest.raises(asyncio.TimeoutError):
        await proc.ask("hello", timeout=0.01)

    assert slow.killed is True


class _BusyAgent:
    def __init__(self, timeout_s):
        self.id = "agent-1"
        self.state = AgentState.BUSY
        self.busy_since = time.time() - 600
        self.current_task = type("TaskStub", (), {"timeout_s": timeout_s})()

    def is_alive(self):
        return True


def test_health_inspector_skips_stuck_when_globally_disabled() -> None:
    inspector = HealthInspector()
    issues = inspector.inspect([_BusyAgent(timeout_s=120.0)], stuck_timeout_s=0)
    assert issues == []


def test_health_inspector_reports_stuck_when_enabled() -> None:
    inspector = HealthInspector()
    issues = inspector.inspect([_BusyAgent(timeout_s=120.0)], stuck_timeout_s=300)
    assert len(issues) == 1
    assert issues[0].issue == "stuck"


class _CapturingProcess:
    def __init__(self):
        self.alive = False
        self.received_timeouts: list[float | None] = []

    async def spawn(self, mcp_server_names=None):
        self.alive = True

    async def ask(self, prompt: str, timeout: float | None = 120.0, session_id: str | None = None) -> str:
        self.received_timeouts.append(timeout)
        return "ok"

    async def kill(self):
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive


@pytest.mark.asyncio
async def test_agent_assign_passes_none_timeout_for_zero_task_timeout(
    memory_manager, mcp_manager, context_builder
) -> None:
    process = _CapturingProcess()

    agent = GeminiAgent(
        agent_id="agent-0",
        process_factory=lambda: process,
        mcp_manager=mcp_manager,
        memory_manager=memory_manager,
        context_builder=context_builder,
    )
    await agent.start()

    task = Task(prompt="Ping", session_id="thread-1", timeout_s=0)
    result = await agent.assign(task)

    assert result.status == TaskStatus.COMPLETED
    assert process.received_timeouts == [None]


class _FakeCall:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeMessagesAPI:
    def __init__(self):
        self.created = []
        self.updated = []

    def list(self, parent, pageSize, orderBy):
        return _FakeCall({"messages": []})

    def create(self, parent, body):
        self.created.append({"parent": parent, "body": body})
        return _FakeCall({"name": "spaces/test/messages/1"})

    def update(self, name, updateMask, body):
        self.updated.append({"name": name, "updateMask": updateMask, "body": body})
        return _FakeCall({"name": name})


class _FakeSpacesAPI:
    def __init__(self, messages_api):
        self._messages_api = messages_api

    def messages(self):
        return self._messages_api

    def setup(self, body):
        return _FakeCall({"name": "spaces/test"})


class _FakeService:
    def __init__(self):
        self.messages_api = _FakeMessagesAPI()
        self.spaces_api = _FakeSpacesAPI(self.messages_api)

    def spaces(self):
        return self.spaces_api


class _CapturingRuntime:
    def __init__(self, persona):
        self.persona = persona
        self.captured_timeout = None

    async def assign_stream(self, task):
        from g3lobster.cli.streaming import StreamEvent, StreamEventType

        self.captured_timeout = task.timeout_s
        task.status = TaskStatus.COMPLETED
        task.result = "ok"
        yield StreamEvent(
            event_type=StreamEventType.MESSAGE,
            data={"role": "assistant", "content": "ok", "delta": True},
        )
        yield StreamEvent(
            event_type=StreamEventType.RESULT,
            data={"status": "success"},
        )


class _CapturingRegistry:
    def __init__(self, runtime, gemini_timeout_s: float):
        self.runtime = runtime
        self.gemini_timeout_s = gemini_timeout_s

    def get_agent(self, agent_id):
        if agent_id == self.runtime.persona.id:
            return self.runtime
        return None

    async def start_agent(self, agent_id):
        return agent_id == self.runtime.persona.id

    def list_enabled_personas(self):
        return [self.runtime.persona]


@pytest.mark.asyncio
async def test_chat_bridge_uses_persona_response_timeout(tmp_path: Path) -> None:
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
            response_timeout_s=0,
        ),
    )

    runtime = _CapturingRuntime(persona)
    registry = _CapturingRegistry(runtime, gemini_timeout_s=120.0)
    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=_FakeService(),
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=0,
    )

    message = {
        "text": "/luna Hello there",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
    }

    await bridge.handle_message(message)
    await asyncio.sleep(0.05)
    assert runtime.captured_timeout == 0.0


@pytest.mark.asyncio
async def test_chat_bridge_falls_back_to_registry_timeout(tmp_path: Path) -> None:
    data_dir = str(tmp_path / "data")
    persona = save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            emoji="🦀",
        ),
    )

    runtime = _CapturingRuntime(persona)
    registry = _CapturingRegistry(runtime, gemini_timeout_s=42.0)
    bridge = ChatBridge(
        registry=registry,
        space_id="spaces/test",
        service=_FakeService(),
        spaces_config=str(tmp_path / "spaces.json"),
        debounce_window_ms=0,
    )

    message = {
        "text": "/luna Hello there",
        "sender": {"type": "HUMAN", "name": "users/123", "displayName": "Ada"},
        "thread": {"name": "spaces/test/threads/abc"},
    }

    await bridge.handle_message(message)
    await asyncio.sleep(0.05)
    assert runtime.captured_timeout == 42.0


def test_persona_response_timeout_roundtrip(tmp_path: Path) -> None:
    data_dir = str(tmp_path / "data")
    save_persona(
        data_dir,
        AgentPersona(
            id="luna",
            name="Luna",
            response_timeout_s=0,
        ),
    )

    loaded = load_persona(data_dir, "luna")
    assert loaded is not None
    assert loaded.response_timeout_s == 0.0


def test_env_override_zero_disables_response_timeout(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "gemini:\n  response_timeout_s: 120\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("G3LOBSTER_GEMINI_RESPONSE_TIMEOUT_S", "0")
    config = load_config(str(config_path))
    assert config.gemini.response_timeout_s == 0.0
