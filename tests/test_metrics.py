"""Tests for per-agent activity metrics."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from g3lobster.api.routes_metrics import _compute_metrics, _cache
from g3lobster.memory.manager import MemoryManager


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    yield
    _cache.clear()


def _make_manager(tmp_path: Path) -> MemoryManager:
    data_dir = str(tmp_path / "agent-data")
    return MemoryManager(
        data_dir=data_dir,
        compact_threshold=40,
        compact_keep_ratio=0.25,
        compact_chunk_size=10,
        procedure_min_frequency=3,
        memory_max_sections=50,
        gemini_command="echo",
        gemini_args=[],
        gemini_timeout_s=5.0,
        gemini_cwd=".",
    )


def test_compute_metrics_empty(tmp_path):
    manager = _make_manager(tmp_path)
    result = _compute_metrics("test-agent", manager)
    assert result["agent_id"] == "test-agent"
    assert result["sessions"]["total"] == 0
    assert result["messages"]["total"] == 0
    assert result["response_time"]["samples"] == 0


def test_compute_metrics_with_sessions(tmp_path):
    manager = _make_manager(tmp_path)
    now = datetime.now(timezone.utc)
    ts1 = now.isoformat()
    ts2 = now.isoformat()

    manager.sessions.append_message("s1", "user", "hello", metadata=None)
    manager.sessions.append_message("s1", "assistant", "hi there", metadata=None)
    manager.sessions.append_message("s1", "user", "how are you?", metadata=None)
    manager.sessions.append_message("s1", "assistant", "good!", metadata=None)

    result = _compute_metrics("test-agent", manager)
    assert result["sessions"]["total"] == 1
    assert result["messages"]["total"] == 4
    assert result["messages"]["user"] == 2
    assert result["messages"]["assistant"] == 2
    assert result["sessions"]["active_today"] == 1


def test_compute_metrics_memory_stats(tmp_path):
    manager = _make_manager(tmp_path)
    memory_dir = Path(manager.data_dir) / ".memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("# Memory\nSome notes here", encoding="utf-8")
    (memory_dir / "PROCEDURES.md").write_text("## Procedure: greet\nSteps\n## Procedure: search\nSteps", encoding="utf-8")
    (memory_dir / "CANDIDATES.json").write_text('[{"name": "c1"}, {"name": "c2"}]', encoding="utf-8")

    daily_dir = memory_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "2026-03-01.md").write_text("note", encoding="utf-8")
    (daily_dir / "2026-03-02.md").write_text("note", encoding="utf-8")

    result = _compute_metrics("test-agent", manager)
    assert result["memory"]["memory_md_bytes"] > 0
    assert result["memory"]["procedures_count"] == 2
    assert result["memory"]["candidate_count"] == 2
    assert result["memory"]["daily_notes"] == 2
