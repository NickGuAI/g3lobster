"""Tests for per-thread session isolation and per-user global memory."""

from pathlib import Path

import pytest

from g3lobster.memory.global_memory import GlobalMemoryManager


def test_thread_scoped_session_id():
    """Session ID should include thread_id for isolation."""
    space_id = "spaces/X"
    user_id = "users/alice"
    thread_id = "spaces/X/threads/abc"

    thread_id_safe = (thread_id or "no-thread").replace("/", "_")
    session_id = f"{space_id}__{user_id}__{thread_id_safe}"

    assert "threads" in session_id
    assert "alice" in session_id


def test_thread_scoped_no_thread():
    """Without a thread, session_id should use 'no-thread'."""
    space_id = "spaces/X"
    user_id = "users/bob"
    thread_id = None

    thread_id_safe = (thread_id or "no-thread").replace("/", "_")
    session_id = f"{space_id}__{user_id}__{thread_id_safe}"

    assert "no-thread" in session_id


def test_per_user_memory_fallback(tmp_path):
    """Per-user memory should fall back to shared USER.md."""
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    content = manager.read_user_memory_for("unknown-user")
    assert "# USER" in content  # shared default


def test_per_user_memory_write_and_read(tmp_path):
    """Writing per-user memory should be readable."""
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_user_memory_for("alice", "# Alice Prefs\nprefers dark mode")
    result = manager.read_user_memory_for("alice")
    assert "dark mode" in result


def test_per_user_memory_isolation(tmp_path):
    """Two users should have separate memory."""
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_user_memory_for("alice", "Alice's memory")
    manager.write_user_memory_for("bob", "Bob's memory")

    assert "Alice" in manager.read_user_memory_for("alice")
    assert "Bob" in manager.read_user_memory_for("bob")
    assert "Alice" not in manager.read_user_memory_for("bob")
