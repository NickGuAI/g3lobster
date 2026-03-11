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


def test_write_knowledge_creates_file_with_frontmatter(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    rel_path = manager.write_knowledge(
        title="API Migration Delayed",
        content="The API migration is delayed until Q2 due to staffing changes.",
        source_agent="research",
        topic="api-migration",
    )

    assert rel_path == "API_Migration_Delayed.md"
    raw = (manager.knowledge_dir / rel_path).read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    assert "source: research" in raw
    assert "topic: api-migration" in raw
    assert "created:" in raw
    assert "The API migration is delayed until Q2" in raw


def test_read_knowledge_file_returns_content(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_knowledge("test entry", "Some content here.", "agent-a", "testing")

    content = manager.read_knowledge_file("test_entry.md")
    assert content is not None
    assert "Some content here." in content
    assert "source: agent-a" in content


def test_read_knowledge_file_returns_none_for_missing(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    assert manager.read_knowledge_file("nonexistent.md") is None


def test_read_all_knowledge_with_metadata(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_knowledge("fact one", "First fact.", "agent-a", "topic-a")
    manager.write_knowledge("fact two", "Second fact.", "agent-b", "topic-b")

    entries = manager.read_all_knowledge_with_metadata()
    assert len(entries) == 2
    assert entries[0]["key"] == "fact_one"
    assert entries[0]["source"] == "agent-a"
    assert entries[0]["topic"] == "topic-a"
    assert entries[0]["content"] == "First fact."
    assert entries[1]["key"] == "fact_two"
    assert entries[1]["source"] == "agent-b"


def test_list_knowledge_with_metadata(tmp_path) -> None:
    manager = GlobalMemoryManager(str(tmp_path / "data"))
    manager.write_knowledge("my fact", "Content.", "agent-x", "some-topic")

    items = manager.list_knowledge_with_metadata()
    assert len(items) == 1
    assert items[0]["source"] == "agent-x"
    assert items[0]["topic"] == "some-topic"
    assert items[0]["path"] == "my_fact.md"


def test_context_builder_includes_cross_agent_knowledge(tmp_path) -> None:
    """ContextBuilder injects relevant cross-agent knowledge into the prompt."""
    global_manager = GlobalMemoryManager(str(tmp_path / "data"))
    global_manager.write_knowledge(
        "API Migration Status",
        "The API migration is delayed until Q2.",
        "research",
        "api-migration",
    )
    global_manager.write_knowledge(
        "Unrelated Weather",
        "It will rain tomorrow.",
        "weather",
        "weather",
    )

    memory = MemoryManager(data_dir=str(tmp_path / "agent"), compact_threshold=50)
    memory.write_memory("# MEMORY\n\nProject: lobsters\n")
    memory.append_message("thread-a", "user", "hello")

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=6,
        system_preamble="You are Atlas.",
        global_memory_manager=global_manager,
        knowledge_limit=3,
    )
    prompt = builder.build("thread-a", "Prepare for standup about the API migration")

    assert "# Cross-Agent Knowledge" in prompt
    assert "API_Migration_Status" in prompt
    assert "Source: research" in prompt
    assert "delayed until Q2" in prompt


def test_context_builder_filters_irrelevant_knowledge(tmp_path) -> None:
    """Irrelevant knowledge entries are excluded from the prompt."""
    global_manager = GlobalMemoryManager(str(tmp_path / "data"))
    global_manager.write_knowledge(
        "Baking Recipes",
        "Use sourdough starter for best results.",
        "cooking-agent",
        "cooking",
    )

    memory = MemoryManager(data_dir=str(tmp_path / "agent"), compact_threshold=50)
    memory.write_memory("# MEMORY\n\n")
    memory.append_message("thread-a", "user", "hello")

    builder = ContextBuilder(
        memory_manager=memory,
        message_limit=6,
        global_memory_manager=global_manager,
        knowledge_limit=3,
    )
    prompt = builder.build("thread-a", "Deploy the application to production")

    # Baking knowledge should not appear in a deploy-related prompt
    assert "sourdough" not in prompt


def test_cross_agent_knowledge_sharing_end_to_end(tmp_path) -> None:
    """Tell Agent A a fact, then ask Agent B — Agent B should know."""
    global_manager = GlobalMemoryManager(str(tmp_path / "data"))

    # Agent A learns something and writes to global knowledge
    global_manager.write_knowledge(
        "Project Deadline Change",
        "The project deadline has been moved from March to June.",
        source_agent="research-agent",
        topic="project-timeline",
    )

    # Agent B's context builder should pick up the knowledge
    agent_b_memory = MemoryManager(data_dir=str(tmp_path / "agent-b"), compact_threshold=50)
    agent_b_memory.write_memory("# MEMORY\n\n")
    agent_b_memory.append_message("session-1", "user", "hi")

    builder = ContextBuilder(
        memory_manager=agent_b_memory,
        message_limit=6,
        system_preamble="You are the meeting prep agent.",
        global_memory_manager=global_manager,
        knowledge_limit=3,
    )
    prompt = builder.build("session-1", "Prepare notes about the project deadline")

    assert "# Cross-Agent Knowledge" in prompt
    assert "moved from March to June" in prompt
    assert "research-agent" in prompt


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
