"""Tests for the journal module: CRUD, salience, association graph, backward compat, search."""

from __future__ import annotations

from datetime import date, timedelta

from g3lobster.memory.journal import (
    AssociationEdge,
    AssociationGraph,
    JournalEntry,
    JournalStore,
    SalienceLevel,
    auto_associate,
)
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine


# ---------------------------------------------------------------------------
# SalienceLevel
# ---------------------------------------------------------------------------


def test_salience_level_from_value():
    assert SalienceLevel.from_value("critical") == SalienceLevel.CRITICAL
    assert SalienceLevel.from_value("HIGH") == SalienceLevel.HIGH
    assert SalienceLevel.from_value(None) == SalienceLevel.NORMAL
    assert SalienceLevel.from_value("bogus") == SalienceLevel.NORMAL


def test_salience_search_weight():
    assert SalienceLevel.CRITICAL.search_weight == 5.0
    assert SalienceLevel.NOISE.search_weight == 0.1


# ---------------------------------------------------------------------------
# JournalEntry
# ---------------------------------------------------------------------------


def test_journal_entry_roundtrip():
    entry = JournalEntry(
        content="Test entry",
        salience=SalienceLevel.HIGH,
        tags=["alpha", "beta"],
        source_session="sess-1",
    )
    data = entry.as_dict()
    assert data["salience"] == "high"
    assert data["tags"] == ["alpha", "beta"]

    restored = JournalEntry.from_dict(data)
    assert restored.content == "Test entry"
    assert restored.salience == SalienceLevel.HIGH
    assert restored.id == entry.id


# ---------------------------------------------------------------------------
# JournalStore CRUD
# ---------------------------------------------------------------------------


def test_journal_store_append_and_read(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))

    entry = JournalEntry(content="First note", tags=["work"])
    store.append(entry)

    entries = store.read_day(date.today())
    assert len(entries) == 1
    assert entries[0].content == "First note"
    assert entries[0].id == entry.id


def test_journal_store_get_entry(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    e1 = JournalEntry(content="A")
    e2 = JournalEntry(content="B")
    store.append(e1)
    store.append(e2)

    found = store.get_entry(e2.id)
    assert found is not None
    assert found.content == "B"

    assert store.get_entry("nonexistent") is None


def test_journal_store_query_salience(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    store.append(JournalEntry(content="Critical item", salience=SalienceLevel.CRITICAL))
    store.append(JournalEntry(content="Normal item", salience=SalienceLevel.NORMAL))
    store.append(JournalEntry(content="Noise item", salience=SalienceLevel.NOISE))

    # Query for critical + high only
    results = store.query(salience_min=SalienceLevel.HIGH)
    assert len(results) == 1
    assert results[0].content == "Critical item"


def test_journal_store_query_tags(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    store.append(JournalEntry(content="Tagged", tags=["deploy"]))
    store.append(JournalEntry(content="Untagged"))

    results = store.query(tags=["deploy"])
    assert len(results) == 1
    assert results[0].content == "Tagged"


def test_journal_store_query_limit(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    for i in range(10):
        store.append(JournalEntry(content=f"Note {i}"))

    results = store.query(limit=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# AssociationGraph
# ---------------------------------------------------------------------------


def test_association_graph_add_and_get(tmp_path):
    graph = AssociationGraph(str(tmp_path / "associations.jsonl"))

    edge = AssociationEdge(source_id="a", target_id="b", relation_type="shared_tags", weight=2.0)
    graph.add_edge(edge)

    edges = graph.get_associations("a")
    assert len(edges) == 1
    assert edges[0].target_id == "b"
    assert edges[0].weight == 2.0

    # Also found via target
    edges_b = graph.get_associations("b")
    assert len(edges_b) == 1


def test_association_graph_neighbors(tmp_path):
    graph = AssociationGraph(str(tmp_path / "associations.jsonl"))
    graph.add_edge(AssociationEdge(source_id="x", target_id="y"))
    graph.add_edge(AssociationEdge(source_id="x", target_id="z"))
    graph.add_edge(AssociationEdge(source_id="w", target_id="x"))

    neighbors = graph.get_neighbors("x")
    assert set(neighbors) == {"y", "z", "w"}


# ---------------------------------------------------------------------------
# auto_associate
# ---------------------------------------------------------------------------


def test_auto_associate_shared_tags(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    graph = AssociationGraph(str(tmp_path / "associations.jsonl"))

    e1 = JournalEntry(content="First", tags=["deploy", "prod"])
    e2 = JournalEntry(content="Second", tags=["deploy", "staging"])
    store.append(e1)
    store.append(e2)

    auto_associate(store, graph, e2)

    edges = graph.get_associations(e2.id)
    assert len(edges) >= 1
    assert any(e.target_id == e1.id for e in edges)


# ---------------------------------------------------------------------------
# MemoryManager journal integration
# ---------------------------------------------------------------------------


def test_memory_manager_journal_entry(tmp_path):
    manager = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)

    entry = JournalEntry(content="Important insight", salience=SalienceLevel.HIGH, tags=["insight"])
    saved = manager.append_journal_entry(entry)

    assert saved.id == entry.id

    # Check it appears in the daily .md note
    md_path = manager.daily_note_path()
    assert md_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "Important insight" in md_content
    assert "[high]" in md_content

    # Check it's queryable
    results = manager.query_journal(tags=["insight"])
    assert len(results) == 1
    assert results[0].content == "Important insight"


def test_memory_manager_query_journal_salience_filter(tmp_path):
    manager = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)

    manager.append_journal_entry(JournalEntry(content="A", salience=SalienceLevel.CRITICAL))
    manager.append_journal_entry(JournalEntry(content="B", salience=SalienceLevel.NORMAL))
    manager.append_journal_entry(JournalEntry(content="C", salience=SalienceLevel.NOISE))

    results = manager.query_journal(salience_min=SalienceLevel.NORMAL)
    contents = {r.content for r in results}
    assert "A" in contents
    assert "B" in contents
    assert "C" not in contents


# ---------------------------------------------------------------------------
# Backward compatibility: existing daily .md files still load
# ---------------------------------------------------------------------------


def test_existing_daily_notes_still_work(tmp_path):
    manager = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)

    # Write plain daily note (old format)
    manager.append_daily_note("Old style note")

    # Verify it's still readable
    md_content = manager.daily_note_path().read_text(encoding="utf-8")
    assert "Old style note" in md_content

    # New journal entries coexist
    manager.append_journal_entry(JournalEntry(content="New structured note"))
    md_content = manager.daily_note_path().read_text(encoding="utf-8")
    assert "Old style note" in md_content
    assert "New structured note" in md_content


# ---------------------------------------------------------------------------
# Salience-weighted search
# ---------------------------------------------------------------------------


def test_search_engine_finds_journal_entries(tmp_path):
    """MemorySearchEngine should find entries in JSONL journal files."""
    data_dir = tmp_path / "data"
    agent_id = "test-agent"
    memory_dir = data_dir / "agents" / agent_id / ".memory"
    daily_dir = memory_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    # Write a MEMORY.md so the agent dir looks valid
    (memory_dir / "MEMORY.md").write_text("# MEMORY\n", encoding="utf-8")

    store = JournalStore(str(daily_dir))
    store.append(JournalEntry(content="deploy production pipeline", salience=SalienceLevel.CRITICAL, tags=["deploy"]))
    store.append(JournalEntry(content="random chitchat noise", salience=SalienceLevel.NOISE))

    engine = MemorySearchEngine(data_dir=str(data_dir))
    hits = engine.search("deploy", agent_ids=[agent_id], memory_types=["daily"])

    deploy_hits = [h for h in hits if "deploy" in h.snippet.lower()]
    assert len(deploy_hits) >= 1


# ---------------------------------------------------------------------------
# Compaction writes journal entries
# ---------------------------------------------------------------------------


def test_compaction_writes_journal_entries(tmp_path):
    """Compaction should produce structured JournalEntry records."""
    manager = MemoryManager(
        data_dir=str(tmp_path / "data"),
        compact_threshold=4,
    )

    manager.append_message("s1", "user", "I always prefer Python over JavaScript")
    manager.append_message("s1", "assistant", "Noted, I will use Python.")
    manager.append_message("s1", "user", "Build the parser now")
    manager.append_message("s1", "assistant", "Here is the parser implementation...")

    # Compaction should have triggered; check journal store
    entries = manager.journal_store.read_day(date.today())
    assert len(entries) > 0
    # User preference should be classified as HIGH
    high_entries = [e for e in entries if e.salience == SalienceLevel.HIGH]
    assert len(high_entries) >= 1
    assert any("compaction" in e.tags for e in entries)
