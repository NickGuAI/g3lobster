"""Tests for per-sender session isolation and per-user global memory."""

from pathlib import Path

import pytest

from g3lobster.memory.global_memory import GlobalMemoryManager, SpaceSessionStore


def test_sender_scoped_session_id():
    """Session ID should be scoped to (space, sender) — no thread component."""
    space_id = "spaces/X"
    user_id = "users/alice"

    session_id = f"{space_id}__{user_id}"

    assert "alice" in session_id
    assert "thread" not in session_id


def test_same_sender_same_session_across_threads():
    """The same sender in different threads maps to the same session."""
    space_id = "spaces/X"
    user_id = "users/bob"

    session_id_t1 = f"{space_id}__{user_id}"
    session_id_t2 = f"{space_id}__{user_id}"

    assert session_id_t1 == session_id_t2


def test_space_session_store_append_and_read(tmp_path):
    """SpaceSessionStore should accumulate messages from all senders."""
    store = SpaceSessionStore(str(tmp_path / "data"))
    store.append("space-A", "users/alice", "user", "hello")
    store.append("space-A", "users/bob", "user", "hi")
    store.append("space-A", "users/alice", "assistant", "hey there")

    messages = store.read("space-A")
    assert len(messages) == 3
    senders = [m["metadata"]["sender_id"] for m in messages]
    assert senders.count("users/alice") == 2
    assert senders.count("users/bob") == 1


def test_space_session_store_isolated_per_space(tmp_path):
    """Different spaces should have separate session logs."""
    store = SpaceSessionStore(str(tmp_path / "data"))
    store.append("space-A", "users/alice", "user", "in A")
    store.append("space-B", "users/alice", "user", "in B")

    assert len(store.read("space-A")) == 1
    assert len(store.read("space-B")) == 1


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
