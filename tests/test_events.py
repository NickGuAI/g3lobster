from __future__ import annotations

import json
from pathlib import Path

from g3lobster.infra.events import AgentEventEmitter


def test_emit_increments_seq_per_run_id() -> None:
    emitter = AgentEventEmitter()

    first = emitter.emit("alpha", "run-1", "gemini", "gemini.task.assigned", {"task_id": "t1"})
    second = emitter.emit("alpha", "run-1", "gemini", "gemini.task.completed", {"task_id": "t1"})
    other = emitter.emit("alpha", "run-2", "gemini", "gemini.task.assigned", {"task_id": "t2"})

    assert first.seq == 1
    assert second.seq == 2
    assert other.seq == 1


def test_listener_receives_events() -> None:
    emitter = AgentEventEmitter()
    seen = []
    emitter.on_event(seen.append)

    emitted = emitter.emit("alpha", "run-1", "lifecycle", "agent.started", {"model": "gemini"})

    assert len(seen) == 1
    assert seen[0].event_id == emitted.event_id


def test_unsubscribe_stops_delivery() -> None:
    emitter = AgentEventEmitter()
    seen = []
    unsubscribe = emitter.on_event(seen.append)

    emitter.emit("alpha", "run-1", "lifecycle", "agent.started", {})
    unsubscribe()
    emitter.emit("alpha", "run-1", "lifecycle", "agent.stopped", {})

    assert len(seen) == 1
    assert seen[0].event_type == "agent.started"


def test_recent_events_ring_buffer() -> None:
    emitter = AgentEventEmitter(max_recent=3)

    for idx in range(5):
        emitter.emit("alpha", "run-1", "gemini", "gemini.task.completed", {"idx": idx})

    recent = emitter.recent_events(limit=10)
    assert [item.data["idx"] for item in recent] == [2, 3, 4]


def test_persist_to_jsonl(tmp_path: Path) -> None:
    events_dir = tmp_path / "agents"
    emitter = AgentEventEmitter(events_dir=events_dir)

    emitted = emitter.emit("alpha", "run-1", "lifecycle", "agent.started", {"model": "gemini"})

    path = events_dir / "alpha" / "events.jsonl"
    assert path.exists()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_id"] == emitted.event_id
    assert payload["event_type"] == "agent.started"


def test_filter_by_agent_id() -> None:
    emitter = AgentEventEmitter()
    emitter.emit("alpha", "run-a", "lifecycle", "agent.started", {})
    emitter.emit("beta", "run-b", "lifecycle", "agent.started", {})

    recent = emitter.recent_events(agent_id="beta", limit=10)

    assert len(recent) == 1
    assert recent[0].agent_id == "beta"


def test_filter_by_stream() -> None:
    emitter = AgentEventEmitter()
    emitter.emit("alpha", "run-a", "lifecycle", "agent.started", {})
    emitter.emit("alpha", "run-a", "gemini", "gemini.task.assigned", {})

    recent = emitter.recent_events(stream="gemini", limit=10)

    assert len(recent) == 1
    assert recent[0].stream == "gemini"
