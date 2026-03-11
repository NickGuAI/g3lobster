from __future__ import annotations

from pathlib import Path

import pytest

from g3lobster.agents.persona import AgentPersona, load_persona, save_persona
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.migration import migrate_agent_memory_layout
from g3lobster.memory.procedures import Procedure


def test_global_memory_manager_crud_and_knowledge_listing(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_user_memory("# USER\n\nPrefers concise updates.\n")
    manager.write_procedures("# PROCEDURES\n\n")

    knowledge_file = manager.knowledge_dir / "facts.md"
    knowledge_file.write_text("Known info\n", encoding="utf-8")

    assert "Prefers concise updates" in manager.read_user_memory()
    assert manager.list_knowledge() == ["facts.md"]


def test_global_procedures_write_uses_structured_store(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))

    manager.write_procedures(
        "\n".join(
            [
                "# PROCEDURES",
                "",
                "## Deploy App",
                "Trigger: deploy app production",
                "Frequency: 4",
                "Last seen: 2026-02-13",
                "",
                "Steps:",
                "- Check git status",
                "- Run tests",
                "- Deploy",
            ]
        )
        + "\n"
    )

    content = manager.read_procedures()
    assert "## Deploy App" in content
    assert "1. Check git status" in content
    assert "2. Run tests" in content


def test_global_procedures_write_rejects_invalid_markdown(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))

    with pytest.raises(ValueError):
        manager.write_procedures("# PROCEDURES\n\nthis is unstructured text\n")


def test_agent_procedures_write_rejects_invalid_markdown(tmp_path) -> None:
    manager = MemoryManager(data_dir=str(tmp_path / "agent"))

    with pytest.raises(ValueError):
        manager.write_procedures("# PROCEDURES\n\nthis is unstructured text\n")


def test_agent_memory_layout_migration_is_idempotent(tmp_path) -> None:
    agent_dir = tmp_path / "data" / "agents" / "iris"
    old_memory = agent_dir / "memory"
    old_daily = old_memory / "memory"
    old_daily.mkdir(parents=True, exist_ok=True)
    (old_memory / "MEMORY.md").write_text("# MEMORY\n\nlegacy\n", encoding="utf-8")
    (old_daily / "2026-02-13.md").write_text("legacy note\n", encoding="utf-8")

    first = migrate_agent_memory_layout(str(agent_dir))
    second = migrate_agent_memory_layout(str(agent_dir))

    assert first is True
    assert second is False
    assert (agent_dir / ".memory" / "MEMORY.md").exists()
    assert (agent_dir / ".memory" / "daily" / "2026-02-13.md").exists()
    assert (agent_dir / "memory.v1").exists()


def test_agent_memory_layout_migration_handles_rename_failure(tmp_path, monkeypatch, caplog) -> None:
    agent_dir = tmp_path / "data" / "agents" / "iris"
    old_memory = (agent_dir / "memory").resolve()
    old_memory.mkdir(parents=True, exist_ok=True)
    (old_memory / "MEMORY.md").write_text("# MEMORY\n\nlegacy\n", encoding="utf-8")

    original_rename = Path.rename

    def _rename(self: Path, target) -> Path:
        if self == old_memory:
            raise OSError("locked")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", _rename)
    caplog.set_level("ERROR")

    changed = migrate_agent_memory_layout(str(agent_dir))

    assert changed is True
    assert (agent_dir / ".memory" / "MEMORY.md").exists()
    assert (agent_dir / "memory").exists()
    assert not (agent_dir / "memory.v1").exists()
    assert "Could not archive legacy memory directory" in caplog.text


def test_context_builder_merges_global_memory_and_procedures(tmp_path) -> None:
    global_manager = GlobalMemoryManager(str(tmp_path / "data"))
    global_manager.write_user_memory("# USER\n\nUser prefers direct answers.\n")
    global_manager.procedures.save_procedures(
        [
            Procedure(
                title="Deploy App",
                trigger="deploy app production",
                steps=["Check git status", "Run tests", "Deploy", "Verify health"],
                weight=4.0,
                status="permanent",
            )
        ]
    )

    memory = MemoryManager(data_dir=str(tmp_path / "agent"), compact_threshold=50)
    memory.write_memory("# MEMORY\n\nProject: lobsters\n")
    memory.append_message("thread-a", "user", "hello")
    memory.append_message("thread-a", "assistant", "hi")

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=6,
        system_preamble="You are Atlas.",
        global_memory_manager=global_manager,
    )
    prompt = builder.build("thread-a", "Please deploy the app to production")

    assert "# User Preferences" in prompt
    assert "User prefers direct answers." in prompt
    assert "# Agent Memory" in prompt
    assert "# Known Procedures" in prompt
    assert "Deploy App" in prompt


def test_save_persona_migrates_legacy_memory_before_creating_defaults(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    runtime_dir = tmp_path / "data" / "agents" / "iris"
    old_memory = runtime_dir / "memory"
    old_daily = old_memory / "memory"
    old_daily.mkdir(parents=True, exist_ok=True)
    (old_memory / "MEMORY.md").write_text("# MEMORY\n\nlegacy notes\n", encoding="utf-8")
    (old_memory / "PROCEDURES.md").write_text("# PROCEDURES\n\nlegacy procedure\n", encoding="utf-8")

    save_persona(
        data_dir,
        AgentPersona(
            id="iris",
            name="Iris",
            soul="Help the user",
        ),
    )

    assert "legacy notes" in (runtime_dir / ".memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "legacy procedure" in (runtime_dir / ".memory" / "PROCEDURES.md").read_text(encoding="utf-8")
    assert (runtime_dir / "memory.v1").exists()


def test_load_persona_performs_legacy_memory_migration(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    runtime_dir = tmp_path / "data" / "agents" / "iris"
    old_memory = runtime_dir / "memory"
    old_daily = old_memory / "memory"
    old_daily.mkdir(parents=True, exist_ok=True)

    (runtime_dir / "agent.json").write_text(
        '{\n  "id": "iris",\n  "name": "Iris",\n  "enabled": true\n}\n',
        encoding="utf-8",
    )
    (runtime_dir / "SOUL.md").write_text("Legacy soul\n", encoding="utf-8")
    (old_memory / "MEMORY.md").write_text("# MEMORY\n\nlegacy from load\n", encoding="utf-8")

    persona = load_persona(data_dir, "iris")

    assert persona is not None
    assert "legacy from load" in (runtime_dir / ".memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert (runtime_dir / "memory.v1").exists()


def test_memory_manager_init_does_not_trigger_additional_migration(tmp_path, monkeypatch) -> None:
    from g3lobster.agents import persona as persona_module

    calls = {"count": 0}
    original = persona_module.migrate_agent_memory_layout

    def _counted_migrate(path: str) -> bool:
        calls["count"] += 1
        return original(path)

    monkeypatch.setattr(persona_module, "migrate_agent_memory_layout", _counted_migrate)

    data_dir = str(tmp_path / "data")
    runtime_dir = tmp_path / "data" / "agents" / "iris"

    save_persona(
        data_dir,
        AgentPersona(
            id="iris",
            name="Iris",
            soul="Help the user",
        ),
    )
    MemoryManager(data_dir=str(runtime_dir))

    assert calls["count"] == 1


def test_write_knowledge_creates_file_with_frontmatter(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    filename = manager.write_knowledge(
        title="API Migration Delayed",
        content="The API migration is delayed until Q2.",
        source_agent="research",
        topic="api-migration",
    )
    assert filename == "api-migration-delayed.md"
    content = (manager.knowledge_dir / filename).read_text(encoding="utf-8")
    assert "source: research" in content
    assert "topic: api-migration" in content
    assert "created:" in content
    assert "# API Migration Delayed" in content
    assert "The API migration is delayed until Q2." in content


def test_read_knowledge_file_returns_content(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_knowledge("Test", "Hello world", "agent-a", "testing")
    content = manager.read_knowledge_file("test.md")
    assert "Hello world" in content


def test_read_knowledge_file_missing_returns_empty(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    assert manager.read_knowledge_file("nonexistent.md") == ""


def test_read_all_knowledge_returns_all_files(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_knowledge("Fact A", "Content A", "agent-a", "topicA")
    manager.write_knowledge("Fact B", "Content B", "agent-b", "topicB")
    all_k = manager.read_all_knowledge()
    assert len(all_k) == 2
    assert "Content A" in all_k["fact-a.md"]
    assert "Content B" in all_k["fact-b.md"]


def test_list_knowledge_metadata_returns_parsed_frontmatter(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_knowledge("My Fact", "some content", "iris", "scheduling")
    metadata = manager.list_knowledge_metadata()
    assert len(metadata) == 1
    assert metadata[0]["source"] == "iris"
    assert metadata[0]["topic"] == "scheduling"
    assert metadata[0]["path"] == "my-fact.md"


def test_context_builder_includes_relevant_knowledge(tmp_path) -> None:
    global_manager = GlobalMemoryManager(str(tmp_path / "data"))
    global_manager.write_user_memory("# USER\n\n")
    global_manager.write_knowledge(
        title="API Migration Delayed",
        content="The API migration is delayed until Q2 due to resource constraints.",
        source_agent="research",
        topic="api-migration",
    )

    memory = MemoryManager(data_dir=str(tmp_path / "agent"), compact_threshold=50)
    memory.write_memory("# MEMORY\n\n")

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=6,
        global_memory_manager=global_manager,
    )
    prompt = builder.build("sess-1", "Prepare for tomorrow's standup about the API migration")

    assert "# Cross-Agent Knowledge" in prompt
    assert "API Migration Delayed" in prompt
    assert "Source: research" in prompt


def test_context_builder_excludes_irrelevant_knowledge(tmp_path) -> None:
    global_manager = GlobalMemoryManager(str(tmp_path / "data"))
    global_manager.write_user_memory("# USER\n\n")
    global_manager.write_knowledge(
        title="Grocery List",
        content="Buy milk, eggs, and bread from the store.",
        source_agent="personal",
        topic="shopping",
    )

    memory = MemoryManager(data_dir=str(tmp_path / "agent"), compact_threshold=50)
    memory.write_memory("# MEMORY\n\n")

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=6,
        global_memory_manager=global_manager,
    )
    prompt = builder.build("sess-1", "Deploy the application to production servers")

    assert "# Cross-Agent Knowledge" not in prompt
    assert "Grocery List" not in prompt


def test_cross_agent_knowledge_demo_scenario(tmp_path) -> None:
    """Tell Agent A a fact, then Agent B should pick it up via ContextBuilder."""
    global_manager = GlobalMemoryManager(str(tmp_path / "data"))
    global_manager.write_user_memory("# USER\n\n")

    # Agent A learns something and writes to global knowledge
    global_manager.write_knowledge(
        title="API Migration Delayed to Q2",
        content="The API migration has been delayed until Q2 due to team resource constraints.",
        source_agent="research-agent",
        topic="api-migration",
    )

    # Agent B builds context for a related query
    agent_b_memory = MemoryManager(data_dir=str(tmp_path / "agent-b"), compact_threshold=50)
    agent_b_memory.write_memory("# MEMORY\n\nI handle meeting prep.\n")

    builder = ContextBuilder(
        memory_manager=agent_b_memory,
        message_limit=6,
        global_memory_manager=global_manager,
    )
    prompt = builder.build("sess-b", "Prepare for tomorrow's standup — any updates on the API migration?")

    assert "API Migration Delayed" in prompt
    assert "delayed until Q2" in prompt
    assert "research-agent" in prompt
