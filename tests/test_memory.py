from __future__ import annotations

from g3lobster.memory.context import ContextBuilder, ContextLayer, _estimate_tokens
from g3lobster.memory.manager import MemoryManager


def test_memory_manager_and_context_builder_with_persona_preamble(tmp_path) -> None:
    memory = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=4)
    memory.write_memory("# MEMORY\n\nUser prefers concise replies.")

    memory.append_message("thread-a", "user", "hello")
    memory.append_message("thread-a", "assistant", "hi")
    memory.append_message("thread-a", "user", "remember I always prefer short answers")
    memory.append_message("thread-a", "assistant", "noted")

    # compact_threshold=4 should trigger compaction and flush highlights
    content = memory.read_memory()
    assert "Compaction thread-a" in content

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=4,
        system_preamble="You are Iris. Be direct.",
    )
    prompt = builder.build("thread-a", "What next?")

    assert "# Agent Persona" in prompt
    assert "You are Iris. Be direct." in prompt
    assert prompt.index("# Agent Persona") < prompt.index("# Agent Memory")
    assert "# User Preferences" in prompt
    assert "User prefers concise replies." in prompt
    assert "What next?" in prompt


def test_tagged_memory_append_and_read(tmp_path) -> None:
    memory = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=4)

    memory.append_tagged_memory("project-x", "Need smoke tests before deploy.")
    memory.append_tagged_memory("project-x", "Run migrations during maintenance window.")
    memory.append_tagged_memory("ops", "Pager rotation starts Monday.")

    project_entries = memory.get_memories_by_tag("project-x")
    assert len(project_entries) == 2
    assert "smoke tests" in project_entries[0]
    assert "migrations" in project_entries[1]

    ops_entries = memory.get_memories_by_tag("ops")
    assert ops_entries == ["Pager rotation starts Monday."]


def test_context_builder_drops_low_priority_layers(tmp_path) -> None:
    """With a tiny budget, required layers survive but droppable layers are dropped."""
    memory = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=40)
    memory.write_memory("A" * 400)  # ~100 tokens

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=4,
        system_preamble="You are a test agent.",
        token_budget=200,  # very tight budget
        debug=True,
    )
    prompt = builder.build("sess-1", "hello")

    # Required layers (preamble, persona, prompt) must survive
    assert "# G3Lobster Agent Environment" in prompt
    assert "# Agent Persona" in prompt
    assert "hello" in prompt

    # Some droppable layers should have been dropped
    info = builder.last_build_info
    assert info is not None
    assert len(info.dropped) > 0
    dropped_names = {d["name"] for d in info.dropped}
    # The lowest priority layers (compaction=9, procedures=8) should be first to drop
    assert "compaction" in dropped_names or "procedures" in dropped_names


def test_context_builder_debug_info(tmp_path) -> None:
    """Debug mode populates last_build_info with layer details."""
    memory = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=40)

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=4,
        debug=True,
    )
    builder.build("sess-1", "test prompt")

    info = builder.last_build_info
    assert info is not None
    assert info.budget == 1_000_000
    assert info.total_tokens > 0
    included_names = {entry["name"] for entry in info.included}
    assert "preamble" in included_names
    assert "prompt" in included_names
    for entry in info.included:
        assert "tokens" in entry
        assert "priority" in entry


def test_context_builder_default_budget_includes_all(tmp_path) -> None:
    """Default budget (1M tokens) should include all layers — backward compat."""
    memory = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=40)
    memory.write_memory("Some memory content here.")

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=4,
        system_preamble="Be helpful.",
        debug=True,
    )
    prompt = builder.build("sess-1", "what's up?")

    info = builder.last_build_info
    assert info is not None
    assert len(info.dropped) == 0

    # All sections present
    assert "# Agent Persona" in prompt
    assert "# Agent Memory" in prompt
    assert "# User Preferences" in prompt
    assert "# Known Procedures" in prompt
    assert "# Compaction Summary" in prompt
    assert "# Recent Conversation" in prompt
    assert "# New User Prompt" in prompt


def test_context_layer_token_estimation() -> None:
    """ContextLayer.tokens uses len//4 estimation."""
    layer = ContextLayer(name="test", priority=0, content="A" * 100)
    assert layer.tokens == 25
    assert _estimate_tokens("B" * 200) == 50
