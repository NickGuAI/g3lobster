"""Tests for multi-space awareness in memory, context, and persona."""

from __future__ import annotations

from g3lobster.agents.persona import AgentPersona
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.manager import MemoryManager
from g3lobster.tasks.types import Task


# --- Task ---


def test_task_space_id_default_none() -> None:
    task = Task(prompt="hello")
    assert task.space_id is None
    assert "space_id" in task.as_dict()
    assert task.as_dict()["space_id"] is None


def test_task_space_id_set() -> None:
    task = Task(prompt="hello", space_id="spaces/ABC123")
    assert task.space_id == "spaces/ABC123"
    assert task.as_dict()["space_id"] == "spaces/ABC123"


# --- MemoryManager space_id in metadata ---


def test_append_message_injects_space_id(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    mm.append_message("sess1", "user", "hello from eng space", space_id="spaces/ENG")
    mm.append_message("sess1", "assistant", "hi back", space_id="spaces/ENG")
    mm.append_message("sess1", "user", "hello from exec space", space_id="spaces/EXEC")

    messages = mm.read_session_messages("sess1")
    assert len(messages) == 3
    assert messages[0]["metadata"]["space_id"] == "spaces/ENG"
    assert messages[1]["metadata"]["space_id"] == "spaces/ENG"
    assert messages[2]["metadata"]["space_id"] == "spaces/EXEC"


def test_append_message_no_space_id_no_metadata_key(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    mm.append_message("sess1", "user", "no space context")
    messages = mm.read_session_messages("sess1")
    assert len(messages) == 1
    # metadata may be absent or not contain space_id
    meta = messages[0].get("metadata") or {}
    assert "space_id" not in meta


def test_append_message_preserves_existing_metadata(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    mm.append_message("sess1", "user", "test", metadata={"task_id": "t1"}, space_id="spaces/X")
    messages = mm.read_session_messages("sess1")
    meta = messages[0]["metadata"]
    assert meta["task_id"] == "t1"
    assert meta["space_id"] == "spaces/X"


# --- MemoryManager.read_session_messages_for_space ---


def test_read_session_messages_for_space_filters(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    mm.append_message("sess1", "user", "eng msg 1", space_id="spaces/ENG")
    mm.append_message("sess1", "assistant", "eng reply", space_id="spaces/ENG")
    mm.append_message("sess1", "user", "exec msg 1", space_id="spaces/EXEC")
    mm.append_message("sess1", "assistant", "exec reply", space_id="spaces/EXEC")

    eng_msgs = mm.read_session_messages_for_space("sess1", "spaces/ENG")
    assert len(eng_msgs) == 2
    assert all(
        (m.get("metadata") or {}).get("space_id") == "spaces/ENG" for m in eng_msgs
    )

    exec_msgs = mm.read_session_messages_for_space("sess1", "spaces/EXEC")
    assert len(exec_msgs) == 2


def test_read_session_messages_for_space_limit(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    for i in range(5):
        mm.append_message("sess1", "user", f"msg {i}", space_id="spaces/A")

    msgs = mm.read_session_messages_for_space("sess1", "spaces/A", limit=2)
    assert len(msgs) == 2
    # Should be the last 2
    assert "msg 3" in msgs[0]["message"]["content"]
    assert "msg 4" in msgs[1]["message"]["content"]


def test_read_session_messages_for_space_fallback(tmp_path) -> None:
    """When no messages match space_id, fall back to all messages."""
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    mm.append_message("sess1", "user", "legacy msg")  # no space_id
    mm.append_message("sess1", "assistant", "legacy reply")

    msgs = mm.read_session_messages_for_space("sess1", "spaces/UNKNOWN")
    assert len(msgs) == 2  # fallback returns all


# --- ContextBuilder space awareness ---


def test_context_builder_includes_space_section(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    builder = ContextBuilder(
        memory_manager=mm,
        message_limit=10,
        space_id="spaces/ENG",
        space_name="Engineering",
    )
    prompt = builder.build("sess1", "What's the status?")
    assert "# Space Context" in prompt
    assert "Engineering" in prompt
    assert "spaces/ENG" in prompt


def test_context_builder_no_space_section_when_no_space(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    builder = ContextBuilder(memory_manager=mm, message_limit=10)
    prompt = builder.build("sess1", "hello")
    assert "# Space Context" not in prompt


def test_context_builder_space_override_in_build(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    builder = ContextBuilder(memory_manager=mm, message_limit=10)
    # No space_id on builder, but passed to build()
    prompt = builder.build("sess1", "hello", space_id="spaces/OVERRIDE")
    assert "# Space Context" in prompt
    assert "spaces/OVERRIDE" in prompt


def test_context_builder_prefers_same_space_entries(tmp_path) -> None:
    mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    # Add messages with different space_ids
    mm.append_message("sess1", "user", "cross-space old msg", space_id="spaces/OTHER")
    mm.append_message("sess1", "user", "same-space msg 1", space_id="spaces/ENG")
    mm.append_message("sess1", "user", "same-space msg 2", space_id="spaces/ENG")

    builder = ContextBuilder(
        memory_manager=mm,
        message_limit=10,
        space_id="spaces/ENG",
    )
    prompt = builder.build("sess1", "status?")
    # Same-space messages should appear after cross-space (i.e., closer to the prompt)
    cross_idx = prompt.index("cross-space old msg")
    same_idx = prompt.index("same-space msg 1")
    assert cross_idx < same_idx


# --- AgentPersona space_overrides ---


def test_persona_space_overrides_default_empty() -> None:
    persona = AgentPersona(id="test-agent", name="Test")
    assert persona.space_overrides == {}


def test_persona_get_soul_for_space_default() -> None:
    persona = AgentPersona(id="test-agent", name="Test", soul="Be helpful.")
    assert persona.get_soul_for_space() == "Be helpful."
    assert persona.get_soul_for_space("spaces/UNKNOWN") == "Be helpful."


def test_persona_get_soul_for_space_override() -> None:
    persona = AgentPersona(
        id="test-agent",
        name="Test",
        soul="Be helpful and verbose.",
        space_overrides={
            "spaces/EXEC": {"soul": "Be concise and executive-focused."},
        },
    )
    assert persona.get_soul_for_space("spaces/EXEC") == "Be concise and executive-focused."
    assert persona.get_soul_for_space("spaces/ENG") == "Be helpful and verbose."
    assert persona.get_soul_for_space() == "Be helpful and verbose."


def test_persona_to_agent_json_includes_space_overrides() -> None:
    overrides = {"spaces/EXEC": {"soul": "concise"}}
    persona = AgentPersona(id="test-agent", name="Test", space_overrides=overrides)
    data = persona.to_agent_json()
    assert data["space_overrides"] == overrides
