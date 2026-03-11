"""Tests for the decision log system."""

from __future__ import annotations

import json
from pathlib import Path

from g3lobster.cli.parser import split_reasoning, strip_reasoning
from g3lobster.memory.compactor import CompactionEngine
from g3lobster.memory.decisions import DecisionLog, looks_like_decision
from g3lobster.memory.sessions import SessionStore


# --- split_reasoning tests ---


def test_split_reasoning_with_separator() -> None:
    text = "thinking about options ✦ The answer is 42."
    reasoning, response = split_reasoning(text)
    assert reasoning == "thinking about options"
    assert response == "The answer is 42."


def test_split_reasoning_without_separator() -> None:
    reasoning, response = split_reasoning("just a plain response")
    assert reasoning == ""
    assert response == "just a plain response"


def test_split_reasoning_empty() -> None:
    reasoning, response = split_reasoning("")
    assert reasoning == ""
    assert response == ""


def test_strip_reasoning_still_works() -> None:
    assert strip_reasoning("thinking ✦ answer") == "answer"
    assert strip_reasoning("just text") == "just text"
    assert strip_reasoning("") == ""


# --- looks_like_decision tests ---


def test_looks_like_decision_positive() -> None:
    assert looks_like_decision("I decided to use Python for this project")
    assert looks_like_decision("Let's go with the second approach")
    assert looks_like_decision("We chose option A because it's better")
    assert looks_like_decision("The approach is to use microservices")
    assert looks_like_decision("Decision: use Redis for caching")


def test_looks_like_decision_negative() -> None:
    assert not looks_like_decision("Hello, how are you?")
    assert not looks_like_decision("The weather is nice today")
    assert not looks_like_decision("")


# --- DecisionLog CRUD tests ---


def test_decision_log_append_and_list(tmp_path: Path) -> None:
    log = DecisionLog(str(tmp_path / "memory"))
    entry = log.append(
        session_id="sess-1",
        decision="Use PostgreSQL over MySQL",
        context="Database discussion",
        reasoning="Better JSON support and extensibility",
        tags=["database", "architecture"],
    )

    assert entry["session_id"] == "sess-1"
    assert entry["decision"] == "Use PostgreSQL over MySQL"
    assert entry["reasoning"] == "Better JSON support and extensibility"
    assert entry["tags"] == ["database", "architecture"]
    assert "timestamp" in entry

    items = log.list(limit=10)
    assert len(items) == 1
    assert items[0]["decision"] == "Use PostgreSQL over MySQL"


def test_decision_log_query(tmp_path: Path) -> None:
    log = DecisionLog(str(tmp_path / "memory"))
    log.append(session_id="s1", decision="Use Redis for caching", tags=["infra"])
    log.append(session_id="s2", decision="Use PostgreSQL for storage", tags=["database"])
    log.append(session_id="s3", decision="Deploy to AWS", tags=["infra"])

    # Query by keyword
    results = log.query("Redis", limit=10)
    assert len(results) == 1
    assert "Redis" in results[0]["decision"]

    # Query by tag
    results = log.query("infra", limit=10)
    assert len(results) == 2

    # Query with multiple keywords
    results = log.query("PostgreSQL storage", limit=10)
    assert len(results) == 1


def test_decision_log_query_empty(tmp_path: Path) -> None:
    log = DecisionLog(str(tmp_path / "memory"))
    results = log.query("anything")
    assert results == []


def test_decision_log_list_most_recent_first(tmp_path: Path) -> None:
    log = DecisionLog(str(tmp_path / "memory"))
    log.append(session_id="s1", decision="First decision")
    log.append(session_id="s2", decision="Second decision")
    log.append(session_id="s3", decision="Third decision")

    items = log.list(limit=2)
    assert len(items) == 2
    assert items[0]["decision"] == "Third decision"
    assert items[1]["decision"] == "Second decision"


def test_decision_log_jsonl_format(tmp_path: Path) -> None:
    log = DecisionLog(str(tmp_path / "memory"))
    log.append(session_id="s1", decision="Test decision")

    path = tmp_path / "memory" / "decisions.jsonl"
    assert path.exists()

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["decision"] == "Test decision"
    assert parsed["session_id"] == "s1"


def test_decision_log_empty_query_returns_recent(tmp_path: Path) -> None:
    log = DecisionLog(str(tmp_path / "memory"))
    log.append(session_id="s1", decision="A decision")

    results = log.query("", limit=10)
    assert len(results) == 1


# --- Decision extraction during compaction ---


def test_compaction_extracts_decisions(tmp_path: Path) -> None:
    """Verify that _extract_decisions logs decision-like messages."""
    decision_log = DecisionLog(str(tmp_path / "memory"))
    session_store = SessionStore(str(tmp_path / "sessions"))

    class _ProcedureStore:
        def upsert_procedures(self, _procedures) -> None:
            pass

    compactor = CompactionEngine(
        session_store=session_store,
        procedure_store=_ProcedureStore(),
        compact_threshold=100,
        decision_log=decision_log,
    )

    messages = [
        {
            "type": "message",
            "message": {"role": "assistant", "content": "I decided to use Redis for caching."},
        },
        {
            "type": "message",
            "message": {"role": "user", "content": "Sounds good!"},
        },
        {
            "type": "message",
            "message": {"role": "assistant", "content": "Let's go with the microservice approach."},
        },
    ]

    compactor._extract_decisions("test-session", messages)

    decisions = decision_log.list(limit=10)
    # Should have extracted the two decision-like messages (most recent first)
    assert len(decisions) == 2
    assert "microservice" in decisions[0]["decision"]
    assert "Redis" in decisions[1]["decision"]


# --- MemoryManager decision integration ---


def test_memory_manager_decision_methods(tmp_path: Path) -> None:
    from g3lobster.memory.manager import MemoryManager

    manager = MemoryManager(data_dir=str(tmp_path / "agent"))
    entry = manager.append_decision(
        session_id="s1",
        decision="Use FastAPI",
        reasoning="Async support",
        tags=["framework"],
    )
    assert entry["decision"] == "Use FastAPI"

    results = manager.query_decisions("FastAPI")
    assert len(results) == 1

    items = manager.list_decisions()
    assert len(items) == 1
