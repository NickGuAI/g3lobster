"""Tests for the decision log feature."""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from g3lobster.cli.parser import split_reasoning, strip_reasoning
from g3lobster.memory.decisions import DecisionLog
from g3lobster.memory.manager import MemoryManager


def test_split_reasoning_with_separator():
    text = "thinking about it\n✦\nHere is my answer"
    reasoning, response = split_reasoning(text)
    assert reasoning == "thinking about it"
    assert response == "Here is my answer"


def test_split_reasoning_no_separator():
    text = "Just a plain response"
    reasoning, response = split_reasoning(text)
    assert reasoning == ""
    assert response == "Just a plain response"


def test_split_reasoning_empty():
    reasoning, response = split_reasoning("")
    assert reasoning == ""
    assert response == ""


def test_strip_reasoning_still_works():
    """Ensure backward compatibility of strip_reasoning."""
    assert strip_reasoning("thinking\n✦\nanswer") == "answer"
    assert strip_reasoning("plain text") == "plain text"
    assert strip_reasoning("") == ""


def test_decision_log_append_and_list(tmp_path):
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    log.append("sess-1", "Use PostgreSQL for persistence", reasoning="It's battle-tested")
    log.append("sess-1", "Deploy to Cloud Run", tags=["infra"])

    entries = log.list()
    assert len(entries) == 2
    assert entries[0]["decision"] == "Use PostgreSQL for persistence"
    assert entries[0]["reasoning"] == "It's battle-tested"
    assert entries[1]["decision"] == "Deploy to Cloud Run"
    assert entries[1]["tags"] == ["infra"]


def test_decision_log_query(tmp_path):
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    log.append("s1", "Use PostgreSQL", reasoning="ACID compliance needed")
    log.append("s1", "Use Redis for caching", reasoning="Fast key-value lookups")
    log.append("s1", "Deploy to Cloud Run", tags=["infra"])

    results = log.query("PostgreSQL")
    assert len(results) == 1
    assert results[0]["decision"] == "Use PostgreSQL"

    results = log.query("caching Redis")
    assert len(results) == 1
    assert results[0]["decision"] == "Use Redis for caching"


def test_decision_log_query_empty(tmp_path):
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    log.append("s1", "Some decision")
    results = log.query("")
    assert len(results) == 1


def test_decision_log_list_limit(tmp_path):
    log = DecisionLog(str(tmp_path / "decisions.jsonl"))
    for i in range(10):
        log.append("s1", f"Decision {i}")
    entries = log.list(limit=3)
    assert len(entries) == 3
    assert entries[0]["decision"] == "Decision 7"


def test_compactor_extracts_decisions(tmp_path):
    """CompactionEngine._extract_decisions scans assistant messages for decision patterns."""
    from g3lobster.memory.compactor import CompactionEngine
    from g3lobster.memory.sessions import SessionStore
    from g3lobster.memory.procedures import ProcedureStore

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_store = SessionStore(str(sessions_dir))
    procedures_file = tmp_path / "PROCEDURES.md"
    procedures_file.write_text("# PROCEDURES\n\n")
    procedure_store = ProcedureStore(str(procedures_file))

    decisions_path = tmp_path / "decisions.jsonl"
    decision_log = DecisionLog(str(decisions_path))

    engine = CompactionEngine(
        session_store=session_store,
        procedure_store=procedure_store,
        decision_log=decision_log,
    )

    messages = [
        {"type": "message", "message": {"role": "user", "content": "What db should we use?"}},
        {"type": "message", "message": {"role": "assistant", "content": "I decided to use PostgreSQL because it has strong ACID compliance."}},
        {"type": "message", "message": {"role": "assistant", "content": "Here is the config file."}},
        {"type": "message", "message": {"role": "assistant", "content": "We chose Redis for the cache layer."}},
    ]

    engine._extract_decisions("test-session", messages)

    entries = decision_log.list()
    assert len(entries) == 2
    assert "PostgreSQL" in entries[0]["decision"]
    assert "Redis" in entries[1]["decision"]
    assert all(e["tags"] == ["auto-extracted"] for e in entries)


def test_memory_manager_decision_log(tmp_path):
    """MemoryManager should initialize and expose a DecisionLog."""
    manager = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)
    assert manager.decision_log is not None

    manager.append_decision("sess-1", "Use JSONL format", reasoning="Append-only is simpler")
    results = manager.query_decisions("JSONL")
    assert len(results) == 1
    assert results[0]["decision"] == "Use JSONL format"


def test_decision_api_endpoint(tmp_path):
    """Test GET /agents/{id}/decisions endpoint."""
    from g3lobster.agents.persona import AgentPersona, save_persona
    from g3lobster.agents.registry import AgentRegistry
    from g3lobster.api.server import create_app
    from g3lobster.chat.bridge_manager import BridgeManager
    from g3lobster.config import AppConfig
    from g3lobster.memory.global_memory import GlobalMemoryManager

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    config = AppConfig()
    config.agents.data_dir = str(data_dir)

    persona = AgentPersona(id="test-bot", name="Test", emoji="🤖")
    save_persona(str(data_dir), persona)

    # Write some decisions to the agent's data directory
    agent_data_dir = data_dir / "agents" / "test-bot"
    decisions_path = agent_data_dir / ".memory" / "decisions.jsonl"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    log = DecisionLog(str(decisions_path))
    log.append("s1", "Use JSONL", reasoning="Simple and append-only")
    log.append("s1", "Use FastAPI", reasoning="Async support", tags=["framework"])

    class FakeAgent:
        def __init__(self, agent_id):
            self.id = agent_id

    registry = AgentRegistry(
        data_dir=str(data_dir),
        context_messages=6,
        health_check_interval_s=3600,
        stuck_timeout_s=120,
        agent_factory=lambda persona, _m, _c: FakeAgent(persona.id),
    )
    global_mm = GlobalMemoryManager(str(data_dir))
    bridge_manager = BridgeManager(
        registry=registry,
        bridge_factory=lambda **kw: None,
    )
    app = create_app(registry, config=config, global_memory_manager=global_mm, bridge_manager=bridge_manager)

    client = TestClient(app)

    # List all decisions
    resp = client.get("/agents/test-bot/decisions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["decisions"]) == 2

    # Query decisions
    resp = client.get("/agents/test-bot/decisions?q=JSONL")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["decisions"]) >= 1
    assert body["decisions"][0]["decision"] == "Use JSONL"
