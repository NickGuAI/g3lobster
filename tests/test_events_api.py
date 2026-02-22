from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from g3lobster.api.routes_events import stream_events
from g3lobster.api.server import create_app
from g3lobster.config import AppConfig
from g3lobster.infra.events import AgentEvent, AgentEventEmitter
from g3lobster.memory.global_memory import GlobalMemoryManager


class DummyRegistry:
    async def start_all(self) -> None:
        return None

    async def stop_all(self) -> None:
        return None


def _build_test_app(tmp_path: Path):
    config = AppConfig()
    config.agents.data_dir = str(tmp_path / "data")
    config_path = tmp_path / "config.yaml"
    events_dir = Path(config.agents.data_dir) / "agents"
    emitter = AgentEventEmitter(events_dir=events_dir)

    app = create_app(
        registry=DummyRegistry(),
        config=config,
        config_path=str(config_path),
        chat_auth_dir=str(tmp_path / "chat_auth"),
        global_memory_manager=GlobalMemoryManager(config.agents.data_dir),
        emitter=emitter,
    )
    return app, emitter


class FakeRequest:
    def __init__(self, app):
        self.app = app

    async def is_disconnected(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_sse_endpoint_streams_events(tmp_path: Path) -> None:
    app, emitter = _build_test_app(tmp_path)
    seeded = AgentEvent(
        event_id="evt-1",
        run_id="run-1",
        agent_id="alpha",
        seq=1,
        stream="lifecycle",
        event_type="agent.started",
        ts=123.4,
        data={"model": "gemini"},
    )

    def fake_on_event(listener):
        listener(seeded)
        return lambda: None

    emitter.on_event = fake_on_event  # type: ignore[method-assign]

    response = await stream_events(FakeRequest(app), agent_id="alpha")
    chunk = await response.body_iterator.__anext__()
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
    payload = json.loads(text[len("data: ") :].strip())
    assert payload["agent_id"] == "alpha"
    assert payload["event_type"] == "agent.started"
    await response.body_iterator.aclose()


def test_recent_events_endpoint(tmp_path: Path) -> None:
    app, emitter = _build_test_app(tmp_path)

    emitter.emit("alpha", "run-1", "gemini", "gemini.task.assigned", {"task_id": "t1"})
    emitter.emit("beta", "run-2", "lifecycle", "agent.started", {})
    emitter.emit("alpha", "run-3", "memory", "memory.session.appended", {"session_id": "s1"})

    with TestClient(app) as client:
        response = client.get(
            "/events/recent",
            params={"agent_id": "alpha", "stream": "gemini", "limit": 10},
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["event_type"] == "gemini.task.assigned"
    assert payload[0]["agent_id"] == "alpha"


def test_agent_event_history_from_disk(tmp_path: Path) -> None:
    app, emitter = _build_test_app(tmp_path)

    emitter.emit("alpha", "run-1", "lifecycle", "agent.started", {})
    emitter.emit("alpha", "run-1", "lifecycle", "agent.stopped", {"reason": "manual"})
    emitter.emit("beta", "run-2", "lifecycle", "agent.started", {})

    with TestClient(app) as client:
        response = client.get("/agents/alpha/events/history", params={"limit": 100})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["agent_id"] == "alpha"
    assert payload[1]["event_type"] == "agent.stopped"


def test_agent_event_history_rejects_path_traversal(tmp_path: Path) -> None:
    app, emitter = _build_test_app(tmp_path)

    # This file sits outside the configured events_dir and must not be reachable.
    outside_file = Path(emitter.events_dir).parent / "events.jsonl"
    outside_file.write_text(
        json.dumps({"agent_id": "leak", "event_type": "stolen"}) + "\n",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        response = client.get("/agents/%2E%2E/events/history", params={"limit": 100})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid agent id"
