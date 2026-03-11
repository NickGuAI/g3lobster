"""Tests for the salience-classified journal and association graph."""

from __future__ import annotations

from datetime import date, datetime, timezone

from g3lobster.memory.journal import (
    AssociationEdge,
    AssociationGraph,
    JournalEntry,
    JournalStore,
    SalienceLevel,
    SALIENCE_WEIGHTS,
)
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine


# --- SalienceLevel ---


def test_salience_level_from_value():
    assert SalienceLevel.from_value("critical") == SalienceLevel.CRITICAL
    assert SalienceLevel.from_value("HIGH") == SalienceLevel.HIGH
    assert SalienceLevel.from_value("Normal") == SalienceLevel.NORMAL
    assert SalienceLevel.from_value(None) == SalienceLevel.NORMAL
    assert SalienceLevel.from_value("unknown") == SalienceLevel.NORMAL


def test_salience_weights():
    assert SALIENCE_WEIGHTS[SalienceLevel.CRITICAL] == 5.0
    assert SALIENCE_WEIGHTS[SalienceLevel.NOISE] == 0.1


# --- JournalEntry ---


def test_journal_entry_round_trip():
    entry = JournalEntry(
        content="Test note",
        salience=SalienceLevel.HIGH,
        tags=["project-x", "important"],
        source_session="sess-1",
    )
    data = entry.as_dict()
    assert data["salience"] == "high"
    assert data["tags"] == ["project-x", "important"]

    restored = JournalEntry.from_dict(data)
    assert restored.content == "Test note"
    assert restored.salience == SalienceLevel.HIGH
    assert restored.id == entry.id


def test_journal_entry_from_dict_defaults():
    entry = JournalEntry.from_dict({})
    assert entry.content == ""
    assert entry.salience == SalienceLevel.NORMAL
    assert entry.tags == []


# --- JournalStore CRUD ---


def test_journal_store_append_and_read(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    today = date.today()

    e1 = JournalEntry(content="First note", salience=SalienceLevel.HIGH, tags=["a"])
    e2 = JournalEntry(content="Second note", salience=SalienceLevel.LOW, tags=["b"])

    store.append(e1, day=today)
    store.append(e2, day=today)

    entries = store.read_day(today)
    assert len(entries) == 2
    assert entries[0].content == "First note"
    assert entries[1].content == "Second note"


def test_journal_store_get_entry(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    today = date.today()

    e1 = JournalEntry(content="Find me", tags=["test"])
    store.append(e1, day=today)

    found = store.get_entry(e1.id, day=today)
    assert found is not None
    assert found.content == "Find me"

    # Search across all days
    found2 = store.get_entry(e1.id)
    assert found2 is not None
    assert found2.id == e1.id

    # Not found
    assert store.get_entry("nonexistent") is None


def test_journal_store_query_by_salience(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    today = date.today()

    store.append(JournalEntry(content="critical", salience=SalienceLevel.CRITICAL), day=today)
    store.append(JournalEntry(content="high", salience=SalienceLevel.HIGH), day=today)
    store.append(JournalEntry(content="normal", salience=SalienceLevel.NORMAL), day=today)
    store.append(JournalEntry(content="low", salience=SalienceLevel.LOW), day=today)
    store.append(JournalEntry(content="noise", salience=SalienceLevel.NOISE), day=today)

    # Query with salience_min=HIGH → should get critical + high only
    results = store.query(salience_min=SalienceLevel.HIGH)
    contents = {e.content for e in results}
    assert contents == {"critical", "high"}


def test_journal_store_query_by_tags(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    today = date.today()

    store.append(JournalEntry(content="a", tags=["project-x"]), day=today)
    store.append(JournalEntry(content="b", tags=["ops"]), day=today)
    store.append(JournalEntry(content="c", tags=["project-x", "ops"]), day=today)

    results = store.query(tags=["project-x"])
    contents = {e.content for e in results}
    assert contents == {"a", "c"}


def test_journal_store_query_date_range(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))

    d1 = date(2026, 3, 1)
    d2 = date(2026, 3, 5)
    d3 = date(2026, 3, 10)

    store.append(JournalEntry(content="early"), day=d1)
    store.append(JournalEntry(content="mid"), day=d2)
    store.append(JournalEntry(content="late"), day=d3)

    results = store.query(start_date=date(2026, 3, 4), end_date=date(2026, 3, 6))
    assert len(results) == 1
    assert results[0].content == "mid"


def test_journal_store_query_limit(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))
    today = date.today()

    for i in range(10):
        store.append(JournalEntry(content=f"note-{i}"), day=today)

    results = store.query(limit=3)
    assert len(results) == 3


def test_journal_store_list_days(tmp_path):
    store = JournalStore(str(tmp_path / "daily"))

    d1 = date(2026, 3, 1)
    d2 = date(2026, 3, 5)
    store.append(JournalEntry(content="a"), day=d1)
    store.append(JournalEntry(content="b"), day=d2)

    days = store.list_days()
    assert days == [d1, d2]


# --- AssociationGraph ---


def test_association_graph_add_and_get(tmp_path):
    graph = AssociationGraph(str(tmp_path / ".memory"))

    edge = AssociationEdge(source_id="e1", target_id="e2", relation_type="shared_tag:ops")
    graph.add_edge(edge)

    edges = graph.get_associations("e1")
    assert len(edges) == 1
    assert edges[0].source_id == "e1"
    assert edges[0].target_id == "e2"

    # Also found from the target side
    edges2 = graph.get_associations("e2")
    assert len(edges2) == 1


def test_association_graph_shared_tags(tmp_path):
    graph = AssociationGraph(str(tmp_path / ".memory"))

    entries = [
        JournalEntry(id="e1", content="a", tags=["ops", "deploy"]),
        JournalEntry(id="e2", content="b", tags=["ops"]),
        JournalEntry(id="e3", content="c", tags=["deploy"]),
        JournalEntry(id="e4", content="d", tags=["unrelated"]),
    ]
    new_edges = graph.add_edges_for_shared_tags(entries)

    # e1-e2 share "ops", e1-e3 share "deploy"
    pairs = {(e.source_id, e.target_id) for e in new_edges}
    assert ("e1", "e2") in pairs or ("e2", "e1") in pairs
    assert ("e1", "e3") in pairs or ("e3", "e1") in pairs

    # e4 should not be linked
    for e in new_edges:
        assert "e4" not in (e.source_id, e.target_id)


def test_association_graph_traversal(tmp_path):
    graph = AssociationGraph(str(tmp_path / ".memory"))

    # e1 → e2 → e3
    graph.add_edge(AssociationEdge(source_id="e1", target_id="e2"))
    graph.add_edge(AssociationEdge(source_id="e2", target_id="e3"))

    # Depth 1 from e1 → only e2
    related = graph.get_related_ids("e1", depth=1)
    assert related == ["e2"]

    # Depth 2 from e1 → e2 + e3
    related = graph.get_related_ids("e1", depth=2)
    assert sorted(related) == ["e2", "e3"]


def test_association_edge_round_trip():
    edge = AssociationEdge(source_id="a", target_id="b", relation_type="test", weight=2.5)
    data = edge.as_dict()
    restored = AssociationEdge.from_dict(data)
    assert restored.source_id == "a"
    assert restored.weight == 2.5


# --- MemoryManager journal integration ---


def test_memory_manager_append_journal_entry(tmp_path):
    mgr = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)
    today = date.today()

    entry = JournalEntry(
        content="Important insight",
        salience=SalienceLevel.HIGH,
        tags=["research"],
    )
    saved = mgr.append_journal_entry(entry, day=today)
    assert saved.id == entry.id

    # Check JSONL file was written
    entries = mgr.journal_store.read_day(today)
    assert len(entries) == 1
    assert entries[0].content == "Important insight"

    # Check backward-compatible .md daily note
    md_path = mgr.daily_note_path(today)
    assert md_path.exists()
    md_content = md_path.read_text()
    assert "[HIGH]" in md_content
    assert "Important insight" in md_content


def test_memory_manager_query_journal(tmp_path):
    mgr = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)
    today = date.today()

    mgr.append_journal_entry(JournalEntry(content="a", salience=SalienceLevel.CRITICAL, tags=["ops"]), day=today)
    mgr.append_journal_entry(JournalEntry(content="b", salience=SalienceLevel.LOW, tags=["misc"]), day=today)

    results = mgr.query_journal(salience_min=SalienceLevel.CRITICAL)
    assert len(results) == 1
    assert results[0].content == "a"


# --- Backward compatibility ---


def test_existing_daily_notes_still_load(tmp_path):
    """Existing .md daily notes should continue to work without .jsonl files."""
    data_dir = tmp_path / "data"
    daily_dir = data_dir / ".memory" / "daily"
    daily_dir.mkdir(parents=True)

    # Write old-style .md file
    md_path = daily_dir / "2026-03-01.md"
    md_path.write_text("Old daily note content\n")

    mgr = MemoryManager(data_dir=str(data_dir), compact_threshold=100)

    # Reading daily note should work
    content = md_path.read_text()
    assert "Old daily note content" in content

    # Journal query for that date returns nothing (no .jsonl)
    entries = mgr.query_journal(start_date=date(2026, 3, 1), end_date=date(2026, 3, 1))
    assert entries == []

    # Appending still works
    mgr.append_daily_note("New content", day=date(2026, 3, 1))
    content = md_path.read_text()
    assert "New content" in content


# --- Salience-weighted search ---


def test_search_engine_scans_jsonl_with_salience(tmp_path):
    """MemorySearchEngine should scan .jsonl journal files with salience weighting."""
    data_dir = tmp_path / "data"
    agent_dir = data_dir / "agents" / "test-agent"
    daily_dir = agent_dir / ".memory" / "daily"
    daily_dir.mkdir(parents=True)

    store = JournalStore(str(daily_dir))
    today = date.today()
    store.append(JournalEntry(content="critical finding about deployment", salience=SalienceLevel.CRITICAL), day=today)
    store.append(JournalEntry(content="noise about deployment", salience=SalienceLevel.NOISE), day=today)

    engine = MemorySearchEngine(data_dir=str(data_dir))
    hits = engine.search("deployment", agent_ids=["test-agent"], memory_types=["daily"])

    assert len(hits) >= 2
    # Critical hit should have higher salience_weight
    critical_hits = [h for h in hits if "CRITICAL" in h.snippet]
    noise_hits = [h for h in hits if "NOISE" in h.snippet]
    assert len(critical_hits) >= 1
    assert len(noise_hits) >= 1
    assert critical_hits[0].salience_weight > noise_hits[0].salience_weight


# --- Compaction journal integration ---


def test_compaction_writes_journal_entries(tmp_path):
    """Compaction should produce structured journal entries alongside memory sections."""
    mgr = MemoryManager(
        data_dir=str(tmp_path / "data"),
        compact_threshold=4,
    )

    mgr.append_message("s1", "user", "I always prefer dark mode")
    mgr.append_message("s1", "assistant", "Noted, I'll remember your preference for dark mode.")
    mgr.append_message("s1", "user", "What's the weather?")
    mgr.append_message("s1", "assistant", "I don't have weather data.")

    # After compaction, check that journal JSONL file has entries
    today = date.today()
    entries = mgr.journal_store.read_day(today)
    assert len(entries) > 0

    # User preference should be classified as HIGH salience
    high_entries = [e for e in entries if e.salience == SalienceLevel.HIGH]
    assert len(high_entries) >= 1
