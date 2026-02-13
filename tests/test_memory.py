from __future__ import annotations

from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.manager import MemoryManager


def test_memory_manager_and_context_builder_with_persona_preamble(tmp_path) -> None:
    memory = MemoryManager(data_dir=str(tmp_path / "data"), summarize_threshold=2)
    memory.write_memory("# MEMORY\n\nUser prefers concise replies.")

    memory.append_message("thread-a", "user", "hello")
    memory.append_message("thread-a", "assistant", "hi")

    # summarize_threshold=2 should trigger a markdown summary section
    content = memory.read_memory()
    assert "Session thread-a" in content

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=4,
        system_preamble="You are Iris. Be direct.",
    )
    prompt = builder.build("thread-a", "What next?")

    assert "# Agent Persona" in prompt
    assert "You are Iris. Be direct." in prompt
    assert prompt.index("# Agent Persona") < prompt.index("# Persistent Memory")
    assert "User prefers concise replies." in prompt
    assert "user: hello" in prompt
    assert "assistant: hi" in prompt
    assert "What next?" in prompt
