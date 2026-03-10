from __future__ import annotations

from datetime import date
from pathlib import Path

from g3lobster.agents.persona import AgentPersona, save_persona
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine
from g3lobster.tasks.types import Task, TaskStatus, TaskStore


def test_task_store_ring_buffer_per_agent() -> None:
    store = TaskStore(max_tasks_per_agent=2)

    t1 = Task(prompt="one", agent_id="alpha")
    t1.status = TaskStatus.COMPLETED
    t2 = Task(prompt="two", agent_id="alpha")
    t2.status = TaskStatus.FAILED
    t3 = Task(prompt="three", agent_id="alpha")
    t3.status = TaskStatus.CANCELED

    store.add(t1)
    store.add(t2)
    store.add(t3)

    items = store.list("alpha")
    assert [item.id for item in items] == [t3.id, t2.id]
    assert store.get("alpha", t1.id) is None


def test_memory_search_engine_scopes_and_types(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    save_persona(str(data_dir), AgentPersona(id="alpha", name="Alpha"))
    save_persona(str(data_dir), AgentPersona(id="beta", name="Beta"))

    alpha_memory = MemoryManager(data_dir=str(data_dir / "agents" / "alpha"))
    beta_memory = MemoryManager(data_dir=str(data_dir / "agents" / "beta"))

    alpha_memory.write_memory("# MEMORY\n\nalpha remembers ocean-token.\n")
    alpha_memory.write_procedures(
        "# PROCEDURES\n\n## Release\nTrigger: ocean-token\n\nSteps:\n1. run tests\n2. deploy\n"
    )
    alpha_memory.append_daily_note("Daily log mentions ocean-token.", day=date(2026, 3, 9))
    alpha_memory.append_message("thread-1", "user", "session includes ocean-token")

    beta_memory.write_memory("# MEMORY\n\nbeta also has ocean-token.\n")

    knowledge_dir = data_dir / ".memory" / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    (knowledge_dir / "notes.md").write_text("Shared knowledge ocean-token reference", encoding="utf-8")

    engine = MemorySearchEngine(str(data_dir))
    hits = engine.search("ocean-token", limit=50)

    assert any(hit.memory_type == "memory" and hit.agent_id == "alpha" for hit in hits)
    assert any(hit.memory_type == "procedures" and hit.agent_id == "alpha" for hit in hits)
    assert any(hit.memory_type == "daily" and hit.agent_id == "alpha" for hit in hits)
    assert any(hit.memory_type == "session" and hit.agent_id == "alpha" for hit in hits)
    assert any(hit.memory_type == "knowledge" and hit.agent_id == "_global" for hit in hits)
    assert any(hit.agent_id == "beta" for hit in hits)

    alpha_only = engine.search("ocean-token", agent_ids=["alpha"], memory_types=["memory"], limit=20)
    assert alpha_only
    assert {hit.agent_id for hit in alpha_only} == {"alpha"}
    assert {hit.memory_type for hit in alpha_only} == {"memory"}
