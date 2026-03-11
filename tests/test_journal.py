"""Tests for the journal subsystem: SalienceLevel, JournalEntry, JournalStore,
AssociationGraph, MemoryManager journal integration, and search integration."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from g3lobster.memory.journal import (
    AssociationGraph,
    JournalEntry,
    JournalStore,
    SalienceLevel,
)
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine


# ---------------------------------------------------------------------------
# 1. SalienceLevel tests
# ---------------------------------------------------------------------------


def test_salience_from_value() -> None:
    """Valid values, None, and invalid strings all resolve correctly."""
    assert SalienceLevel.from_value("critical") is SalienceLevel.CRITICAL
    assert SalienceLevel.from_value("HIGH") is SalienceLevel.HIGH
    assert SalienceLevel.from_value("  Normal ") is SalienceLevel.NORMAL
    assert SalienceLevel.from_value("low") is SalienceLevel.LOW
    assert SalienceLevel.from_value("noise") is SalienceLevel.NOISE

    # None falls back to NORMAL
    assert SalienceLevel.from_value(None) is SalienceLevel.NORMAL

    # Invalid / unknown falls back to NORMAL
    assert SalienceLevel.from_value("bogus") is SalienceLevel.NORMAL
    assert SalienceLevel.from_value("") is SalienceLevel.NORMAL


def test_salience_weight() -> None:
    """Each salience level exposes the expected weight."""
    assert SalienceLevel.CRITICAL.weight == 5.0
    assert SalienceLevel.HIGH.weight == 3.0
    assert SalienceLevel.NORMAL.weight == 1.0
    assert SalienceLevel.LOW.weight == 0.5
    assert SalienceLevel.NOISE.weight == 0.1


# ---------------------------------------------------------------------------
# 2. JournalEntry tests
# ---------------------------------------------------------------------------


def test_entry_defaults() -> None:
    """A minimal entry gets a uuid id, an ISO timestamp, and NORMAL salience."""
    entry = JournalEntry(content="hello world")

    assert entry.id  # non-empty
    assert len(entry.id) == 36  # UUID format
    assert entry.timestamp  # non-empty
    # Should parse as ISO datetime
    datetime.fromisoformat(entry.timestamp)
    assert entry.salience is SalienceLevel.NORMAL
    assert entry.tags == []
    assert entry.source_session == ""
    assert entry.associations == []


def test_entry_as_dict_from_dict_roundtrip() -> None:
    """Create an entry, serialise via as_dict, deserialise via from_dict, verify equality."""
    original = JournalEntry(
        content="Important meeting notes",
        salience=SalienceLevel.HIGH,
        tags=["meeting", "notes"],
        source_session="sess-42",
        associations=["entry-a", "entry-b"],
    )

    data = original.as_dict()
    restored = JournalEntry.from_dict(data)

    assert restored.id == original.id
    assert restored.timestamp == original.timestamp
    assert restored.content == original.content
    assert restored.salience is original.salience
    assert restored.tags == original.tags
    assert restored.source_session == original.source_session
    assert restored.associations == original.associations


def test_from_dict_missing_fields() -> None:
    """from_dict handles a mostly-empty dict without raising."""
    entry = JournalEntry.from_dict({})

    assert entry.id  # auto-generated
    assert entry.timestamp  # auto-generated
    assert entry.content == ""
    assert entry.salience is SalienceLevel.NORMAL
    assert entry.tags == []
    assert entry.source_session == ""
    assert entry.associations == []


# ---------------------------------------------------------------------------
# 3. JournalStore tests
# ---------------------------------------------------------------------------


def test_append_and_get(tmp_path: Path) -> None:
    """Append an entry, then retrieve it by id."""
    store = JournalStore(tmp_path / "daily")
    entry = JournalEntry(content="test entry")
    store.append(entry, day=date(2025, 6, 15))

    result = store.get(entry.id)
    assert result is not None
    assert result.id == entry.id
    assert result.content == "test entry"


def test_query_all(tmp_path: Path) -> None:
    """Append multiple entries, query returns them sorted by timestamp desc."""
    store = JournalStore(tmp_path / "daily")

    e1 = JournalEntry(content="first", timestamp="2025-06-15T10:00:00")
    e2 = JournalEntry(content="second", timestamp="2025-06-15T12:00:00")
    e3 = JournalEntry(content="third", timestamp="2025-06-15T11:00:00")

    store.append(e1, day=date(2025, 6, 15))
    store.append(e2, day=date(2025, 6, 15))
    store.append(e3, day=date(2025, 6, 15))

    results = store.query()
    assert len(results) == 3
    # Descending by timestamp
    assert results[0].content == "second"
    assert results[1].content == "third"
    assert results[2].content == "first"


def test_query_salience_filter(tmp_path: Path) -> None:
    """Filter by minimum salience level."""
    store = JournalStore(tmp_path / "daily")

    low = JournalEntry(content="low item", salience=SalienceLevel.LOW)
    normal = JournalEntry(content="normal item", salience=SalienceLevel.NORMAL)
    high = JournalEntry(content="high item", salience=SalienceLevel.HIGH)
    critical = JournalEntry(content="critical item", salience=SalienceLevel.CRITICAL)

    day = date(2025, 6, 15)
    for e in [low, normal, high, critical]:
        store.append(e, day=day)

    results = store.query(salience_min=SalienceLevel.HIGH)
    contents = {r.content for r in results}
    assert contents == {"high item", "critical item"}


def test_query_tag_filter(tmp_path: Path) -> None:
    """Filter by tags -- entry must have at least one matching tag."""
    store = JournalStore(tmp_path / "daily")
    day = date(2025, 6, 15)

    e1 = JournalEntry(content="alpha", tags=["project-a", "ops"])
    e2 = JournalEntry(content="beta", tags=["project-b"])
    e3 = JournalEntry(content="gamma", tags=["ops"])

    for e in [e1, e2, e3]:
        store.append(e, day=day)

    results = store.query(tags=["ops"])
    contents = {r.content for r in results}
    assert contents == {"alpha", "gamma"}

    results2 = store.query(tags=["project-b", "ops"])
    contents2 = {r.content for r in results2}
    assert contents2 == {"alpha", "beta", "gamma"}


def test_query_date_range(tmp_path: Path) -> None:
    """Filter by explicit date range."""
    store = JournalStore(tmp_path / "daily")

    e1 = JournalEntry(content="june 10")
    e2 = JournalEntry(content="june 15")
    e3 = JournalEntry(content="june 20")

    store.append(e1, day=date(2025, 6, 10))
    store.append(e2, day=date(2025, 6, 15))
    store.append(e3, day=date(2025, 6, 20))

    results = store.query(date_start=date(2025, 6, 12), date_end=date(2025, 6, 18))
    assert len(results) == 1
    assert results[0].content == "june 15"


def test_query_limit(tmp_path: Path) -> None:
    """Respects the limit parameter."""
    store = JournalStore(tmp_path / "daily")
    day = date(2025, 6, 15)

    for i in range(10):
        store.append(JournalEntry(content=f"entry-{i}"), day=day)

    results = store.query(limit=3)
    assert len(results) == 3


def test_get_nonexistent(tmp_path: Path) -> None:
    """Returns None for a missing id."""
    store = JournalStore(tmp_path / "daily")
    assert store.get("does-not-exist") is None


# ---------------------------------------------------------------------------
# 4. AssociationGraph tests
# ---------------------------------------------------------------------------


def test_add_and_get_associations(tmp_path: Path) -> None:
    """Add edges, then retrieve by entry id."""
    graph = AssociationGraph(tmp_path / "assoc.jsonl")

    graph.add_edge("a", "b", relation_type="causes", weight=2.0)
    graph.add_edge("a", "c", relation_type="related")
    graph.add_edge("d", "e", relation_type="related")

    edges_a = graph.get_associations("a")
    assert len(edges_a) == 2
    assert all(
        edge["source_id"] == "a" or edge["target_id"] == "a" for edge in edges_a
    )

    # "b" appears as target in one edge
    edges_b = graph.get_associations("b")
    assert len(edges_b) == 1
    assert edges_b[0]["source_id"] == "a"
    assert edges_b[0]["target_id"] == "b"
    assert edges_b[0]["weight"] == 2.0


def test_remove_edges(tmp_path: Path) -> None:
    """Add edges, remove by entry_id, verify removed."""
    graph = AssociationGraph(tmp_path / "assoc.jsonl")

    graph.add_edge("x", "y")
    graph.add_edge("x", "z")
    graph.add_edge("m", "n")

    graph.remove_edges("x")

    assert graph.get_associations("x") == []
    assert graph.get_associations("y") == []
    # Unrelated edge still present
    assert len(graph.get_associations("m")) == 1


def test_get_empty(tmp_path: Path) -> None:
    """Returns empty list for no associations (file does not even exist)."""
    graph = AssociationGraph(tmp_path / "assoc.jsonl")
    assert graph.get_associations("nonexistent") == []


# ---------------------------------------------------------------------------
# 5. MemoryManager integration tests
# ---------------------------------------------------------------------------


def test_append_journal_entry_creates_md_and_jsonl(tmp_path: Path) -> None:
    """append_journal_entry writes both a .md daily note and a .jsonl journal file."""
    mm = MemoryManager(data_dir=str(tmp_path / "agent1"))

    day = date(2025, 6, 15)
    entry = JournalEntry(
        content="Shipped v2 release",
        salience=SalienceLevel.HIGH,
        tags=["release"],
    )
    mm.append_journal_entry(entry, day=day)

    daily_dir = tmp_path / "agent1" / ".memory" / "daily"
    md_path = daily_dir / "2025-06-15.md"
    jsonl_path = daily_dir / "2025-06-15.jsonl"

    assert md_path.exists(), "Expected .md daily note file"
    assert jsonl_path.exists(), "Expected .jsonl journal file"

    md_content = md_path.read_text(encoding="utf-8")
    assert "Shipped v2 release" in md_content
    assert "[high]" in md_content

    jsonl_content = jsonl_path.read_text(encoding="utf-8").strip()
    data = json.loads(jsonl_content)
    assert data["content"] == "Shipped v2 release"
    assert data["salience"] == "high"
    assert data["tags"] == ["release"]


def test_query_journal_via_manager(tmp_path: Path) -> None:
    """Append entries via MemoryManager, query them back."""
    mm = MemoryManager(data_dir=str(tmp_path / "agent1"))
    day = date(2025, 6, 15)

    mm.append_journal_entry(
        JournalEntry(content="alpha", salience=SalienceLevel.NORMAL, tags=["ops"]),
        day=day,
    )
    mm.append_journal_entry(
        JournalEntry(content="beta", salience=SalienceLevel.CRITICAL, tags=["deploy"]),
        day=day,
    )

    all_entries = mm.query_journal()
    assert len(all_entries) == 2

    critical_only = mm.query_journal(salience_min=SalienceLevel.CRITICAL)
    assert len(critical_only) == 1
    assert critical_only[0].content == "beta"

    tagged = mm.query_journal(tags=["ops"])
    assert len(tagged) == 1
    assert tagged[0].content == "alpha"


# ---------------------------------------------------------------------------
# 6. Backward compatibility test
# ---------------------------------------------------------------------------


def test_existing_daily_notes_still_work(tmp_path: Path) -> None:
    """The old append_daily_note API still works alongside the new journal system."""
    mm = MemoryManager(data_dir=str(tmp_path / "agent1"))
    day = date(2025, 6, 15)

    mm.append_daily_note("Legacy daily note entry", day=day)

    md_path = tmp_path / "agent1" / ".memory" / "daily" / "2025-06-15.md"
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "Legacy daily note entry" in content


# ---------------------------------------------------------------------------
# 7. Search integration test
# ---------------------------------------------------------------------------


def test_search_finds_journal_entries(tmp_path: Path) -> None:
    """MemorySearchEngine finds journal entries from JSONL files with memory_type='journal'."""
    # Set up directory structure as the search engine expects:
    #   data_dir/agents/<agent_id>/.memory/daily/<date>.jsonl
    agent_id = "test-agent"
    daily_dir = tmp_path / "data" / "agents" / agent_id / ".memory" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    entry_data = {
        "id": "entry-001",
        "timestamp": "2025-06-15T10:00:00",
        "content": "Deployed the frobnicator service successfully",
        "salience": "high",
        "tags": ["deploy"],
        "source_session": "",
        "associations": [],
    }
    jsonl_path = daily_dir / "2025-06-15.jsonl"
    jsonl_path.write_text(json.dumps(entry_data) + "\n", encoding="utf-8")

    engine = MemorySearchEngine(data_dir=str(tmp_path / "data"))
    hits = engine.search("frobnicator", memory_types=["journal"])

    assert len(hits) >= 1
    journal_hits = [h for h in hits if h.memory_type == "journal"]
    assert len(journal_hits) == 1
    assert journal_hits[0].agent_id == agent_id
    assert "frobnicator" in journal_hits[0].snippet
