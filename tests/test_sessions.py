from __future__ import annotations

from g3lobster.memory.sessions import SessionStore


def test_sanitize_session_id_rejects_dot_and_dotdot() -> None:
    assert SessionStore._sanitize_session_id(".") == "default"
    assert SessionStore._sanitize_session_id("..") == "default"


def test_dot_session_ids_write_to_default_file(tmp_path) -> None:
    store = SessionStore(str(tmp_path))
    store.append_message(".", "user", "one")
    store.append_message("..", "assistant", "two")

    assert store.list_sessions() == ["default"]
    assert not (tmp_path / ".jsonl").exists()
    assert not (tmp_path / "..jsonl").exists()
