"""Tests for salience-classified journal and association graph."""

from __future__ import annotations

from datetime import date, datetime, timezone

from g3lobster.memory.journal import (
    AssociationEdge,
    AssociationGraph,
    JournalEntry,
    JournalStore,
    SalienceLevel,
)
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine


# --- SalienceLevel ---


def test_salience_level_from_value():
    assert SalienceLevel.from_value("critical") == SalienceLevel.CRITICAL
    assert SalienceLevel.from_value("HIGH") == SalienceLevel.HIGH
    assert SalienceLevel.from_value(None) == SalienceLevel.NORMAL
    assert SalienceLevel.from_value("bogus") == SalienceLevel.NORMAL


def test_salience_level_weights():
    assert SalienceLevel.CRITICAL.weight == 5.0
    assert SalienceLevel.HIGH.weight == 3.0
    assert SalienceLevel.NORMAL.weight == 1.0
    assert SalienceLevel.LOW.weight == 0.5
    assert SalienceLevel.NOISE.weight == 0.1


# --- JournalEntry ---


def test_journal_entry_roundtrip():
    entry = JournalEntry(
        content="test content",
        salience=SalienceLevel.HIGH,
        tags=["deploy", "prod"],
        source_session="sess-1",
    )
    data = entry.as_dict()
    restored = JournalEntry.from_dict(data)
    assert restored.content == "test content"
    assert restored.salience == SalienceLevel.HIGH
    assert restored.tags == ["deploy", "prod"]
    assert restored.source_session == "sess-1"
    assert restored.id == entry.id


# --- JournalStore CRUD ---


def test_journal_store_append_and_read(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    entry = JournalEntry(content="first entry", salience=SalienceLevel.HIGH, tags=["test"])
    store.append(entry)

    entries = store.read_day(date.today())
    assert len(entries) == 1
    assert entries[0].content == "first entry"
    assert entries[0].salience == SalienceLevel.HIGH


def test_journal_store_get_entry(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    entry = JournalEntry(content="findable", tags=["lookup"])
    store.append(entry)

    found = store.get_entry(entry.id)
    assert found is not None
    assert found.content == "findable"

    assert store.get_entry("nonexistent") is None


def test_journal_store_query_by_salience(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    store.append(JournalEntry(content="critical item", salience=SalienceLevel.CRITICAL))
    store.append(JournalEntry(content="high item", salience=SalienceLevel.HIGH))
    store.append(JournalEntry(content="normal item", salience=SalienceLevel.NORMAL))
    store.append(JournalEntry(content="noise item", salience=SalienceLevel.NOISE))

    results = store.query(salience_min=SalienceLevel.HIGH)
    assert len(results) == 2
    contents = {e.content for e in results}
    assert "critical item" in contents
    assert "high item" in contents


def test_journal_store_query_by_tags(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    store.append(JournalEntry(content="deploy note", tags=["deploy"]))
    store.append(JournalEntry(content="test note", tags=["testing"]))

    results = store.query(tags=["deploy"])
    assert len(results) == 1
    assert results[0].content == "deploy note"


def test_journal_store_query_limit(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    for i in range(10):
        store.append(JournalEntry(content=f"entry {i}"))

    results = store.query(limit=3)
    assert len(results) == 3


def test_journal_store_list_days(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    store.append(JournalEntry(content="today's entry"))

    days = store.list_days()
    assert date.today() in days


# --- AssociationGraph ---


def test_association_graph_add_and_get(tmp_path):
    graph = AssociationGraph(str(tmp_path / "memory"))
    edge = AssociationEdge(source_id="a", target_id="b", relation_type="related", weight=1.5)
    graph.add_edge(edge)

    edges = graph.get_associations("a")
    assert len(edges) == 1
    assert edges[0].target_id == "b"
    assert edges[0].weight == 1.5

    # Also found from target side.
    edges_b = graph.get_associations("b")
    assert len(edges_b) == 1
    assert edges_b[0].source_id == "a"


def test_association_graph_get_neighbors(tmp_path):
    graph = AssociationGraph(str(tmp_path / "memory"))
    graph.add_edge(AssociationEdge(source_id="a", target_id="b"))
    graph.add_edge(AssociationEdge(source_id="a", target_id="c"))
    graph.add_edge(AssociationEdge(source_id="d", target_id="a"))

    neighbors = graph.get_neighbors("a")
    assert set(neighbors) == {"b", "c", "d"}


def test_association_graph_add_edges_from_entry(tmp_path):
    graph = AssociationGraph(str(tmp_path / "memory"))
    entry = JournalEntry(id="e1", content="test", associations=["e2", "e3"])
    graph.add_edges_from_entry(entry)

    edges = graph.get_associations("e1")
    assert len(edges) == 2
    targets = {e.target_id for e in edges}
    assert targets == {"e2", "e3"}


def test_association_graph_tag_associations(tmp_path):
    graph = AssociationGraph(str(tmp_path / "memory"))
    entry_a = JournalEntry(id="a", content="alpha", tags=["deploy", "prod"])
    entry_b = JournalEntry(id="b", content="beta", tags=["deploy", "staging"])
    entry_c = JournalEntry(id="c", content="gamma", tags=["testing"])

    graph.add_tag_associations(entry_a, [entry_b, entry_c])
    edges = graph.get_associations("a")
    assert len(edges) == 1
    assert edges[0].target_id == "b"
    assert edges[0].relation_type == "shared_tags"


# --- MemoryManager journal integration ---


def test_memory_manager_append_journal_entry(tmp_path):
    manager = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    entry = JournalEntry(content="important note", salience=SalienceLevel.HIGH, tags=["test"])
    saved = manager.append_journal_entry(entry)

    assert saved.id == entry.id

    # Check that the daily .md also has the entry.
    md_path = manager.daily_note_path()
    assert md_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "[high] important note" in md_content

    # Check journal query returns it.
    results = manager.query_journal(salience_min=SalienceLevel.HIGH)
    assert len(results) >= 1
    assert any(e.content == "important note" for e in results)


def test_memory_manager_backward_compat_daily_notes(tmp_path):
    """Existing daily .md files should still work."""
    manager = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=999)
    manager.append_daily_note("plain text note")

    md_path = manager.daily_note_path()
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "plain text note" in content


def test_compaction_creates_journal_entries(tmp_path):
    """Compaction should write structured journal entries."""
    manager = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=4)

    manager.append_message("sess-1", "user", "I always prefer short answers")
    manager.append_message("sess-1", "assistant", "Noted, will keep it brief")
    manager.append_message("sess-1", "user", "tell me about pandas")
    manager.append_message("sess-1", "assistant", "Pandas is a data library")

    # After compaction, journal entries should exist.
    entries = manager.journal_store.read_day(date.today())
    assert len(entries) > 0
    # At least one should have the compaction tag.
    compaction_entries = [e for e in entries if "compaction" in e.tags]
    assert len(compaction_entries) > 0


# --- Salience-weighted search ---


def test_salience_weighted_search(tmp_path):
    """Journal JSONL entries should appear in search results with salience weighting."""
    data_dir = tmp_path / "data" / "agents" / "test-agent"
    memory_dir = data_dir / ".memory"
    daily_dir = memory_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    store = JournalStore(str(daily_dir))
    store.append(JournalEntry(
        content="critical deployment failure observed",
        salience=SalienceLevel.CRITICAL,
        tags=["deploy"],
    ))
    store.append(JournalEntry(
        content="noise deployment log line",
        salience=SalienceLevel.NOISE,
        tags=["deploy"],
    ))

    engine = MemorySearchEngine(data_dir=str(tmp_path / "data"))
    hits = engine.search("deployment", agent_ids=["test-agent"], memory_types=["daily"])

    assert len(hits) == 2
    # Critical should rank higher due to salience weight.
    assert hits[0].salience_weight > hits[1].salience_weight
    assert "critical" in hits[0].snippet.lower()
