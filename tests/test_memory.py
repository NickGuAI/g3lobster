from __future__ import annotations

from g3lobster.memory.context import ContextBuilder
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


def test_context_builder_includes_delegation_section(tmp_path) -> None:
    memory = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=20)
    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=2,
        system_preamble="You are Athena.",
        agent_id="athena",
        delegation_agents_provider=lambda: [
            ("athena", "planner"),
            ("hephaestus", "code specialist"),
        ],
    )

    prompt = builder.build("thread-a", "Plan this project")

    assert "## Available Agents for Delegation" in prompt
    assert "delegate_to_agent" in prompt
    assert "- hephaestus: code specialist" in prompt
    assert "- athena:" not in prompt
