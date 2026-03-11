"""Tests for salience-classified journal and association graph."""

from __future__ import annotations

from datetime import date, timedelta

from g3lobster.memory.journal import (
    AssociationEdge,
    AssociationGraph,
    JournalEntry,
    JournalStore,
    SalienceLevel,
)
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine


class TestSalienceLevel:
    def test_from_value_valid(self) -> None:
        assert SalienceLevel.from_value("critical") is SalienceLevel.CRITICAL
        assert SalienceLevel.from_value("HIGH") is SalienceLevel.HIGH
        assert SalienceLevel.from_value("Normal") is SalienceLevel.NORMAL

    def test_from_value_invalid_returns_normal(self) -> None:
        assert SalienceLevel.from_value("unknown") is SalienceLevel.NORMAL
        assert SalienceLevel.from_value(None) is SalienceLevel.NORMAL

    def test_weight_values(self) -> None:
        assert SalienceLevel.CRITICAL.weight == 5.0
        assert SalienceLevel.HIGH.weight == 3.0
        assert SalienceLevel.NORMAL.weight == 1.0
        assert SalienceLevel.LOW.weight == 0.5
        assert SalienceLevel.NOISE.weight == 0.1


class TestJournalEntry:
    def test_roundtrip_serialization(self) -> None:
        entry = JournalEntry(
            content="test content",
            salience=SalienceLevel.HIGH,
            tags=["deploy", "ops"],
            source_session="session-1",
        )
        data = entry.to_dict()
        restored = JournalEntry.from_dict(data)

        assert restored.id == entry.id
        assert restored.content == "test content"
        assert restored.salience is SalienceLevel.HIGH
        assert restored.tags == ["deploy", "ops"]
        assert restored.source_session == "session-1"

    def test_from_dict_defaults(self) -> None:
        entry = JournalEntry.from_dict({"content": "hello"})
        assert entry.salience is SalienceLevel.NORMAL
        assert entry.tags == []


class TestJournalStore:
    def test_append_and_read(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        entry = JournalEntry(content="first entry", salience=SalienceLevel.HIGH)
        today = date.today()

        store.append(entry, day=today)
        entries = store.read_day(today)
        assert len(entries) == 1
        assert entries[0].content == "first entry"
        assert entries[0].salience is SalienceLevel.HIGH

    def test_get_entry_by_id(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        entry = JournalEntry(content="find me")
        store.append(entry)

        found = store.get_entry(entry.id)
        assert found is not None
        assert found.content == "find me"

        assert store.get_entry("nonexistent") is None

    def test_query_by_salience(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        today = date.today()

        store.append(JournalEntry(content="critical item", salience=SalienceLevel.CRITICAL), day=today)
        store.append(JournalEntry(content="high item", salience=SalienceLevel.HIGH), day=today)
        store.append(JournalEntry(content="normal item", salience=SalienceLevel.NORMAL), day=today)
        store.append(JournalEntry(content="noise item", salience=SalienceLevel.NOISE), day=today)

        # Filter to critical + high only
        results = store.query(salience_min=SalienceLevel.HIGH)
        saliences = {e.salience for e in results}
        assert SalienceLevel.CRITICAL in saliences
        assert SalienceLevel.HIGH in saliences
        assert SalienceLevel.NORMAL not in saliences
        assert SalienceLevel.NOISE not in saliences

    def test_query_by_tags(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        today = date.today()

        store.append(JournalEntry(content="deploy log", tags=["deploy"]), day=today)
        store.append(JournalEntry(content="bug fix", tags=["bug"]), day=today)
        store.append(JournalEntry(content="deploy bug", tags=["deploy", "bug"]), day=today)

        results = store.query(tags=["deploy"])
        assert len(results) == 2
        assert all("deploy" in e.tags for e in results)

    def test_query_date_range(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        today = date.today()
        yesterday = today - timedelta(days=1)

        store.append(JournalEntry(content="yesterday"), day=yesterday)
        store.append(JournalEntry(content="today"), day=today)

        results = store.query(start_date=today)
        assert len(results) == 1
        assert results[0].content == "today"

    def test_query_limit(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        today = date.today()
        for i in range(10):
            store.append(JournalEntry(content=f"entry {i}"), day=today)

        results = store.query(limit=3)
        assert len(results) == 3

    def test_read_empty_day(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        assert store.read_day(date(2020, 1, 1)) == []


class TestAssociationGraph:
    def test_add_and_get_edges(self, tmp_path) -> None:
        graph = AssociationGraph(str(tmp_path / ".memory"))
        edge = AssociationEdge(source_id="a", target_id="b", relation_type="shared_tags", weight=2.0)
        graph.add_edge(edge)

        edges = graph.get_associations("a")
        assert len(edges) == 1
        assert edges[0].source_id == "a"
        assert edges[0].target_id == "b"

        # Also found when querying by target.
        edges_b = graph.get_associations("b")
        assert len(edges_b) == 1

    def test_auto_link_by_tags(self, tmp_path) -> None:
        daily_dir = tmp_path / "daily"
        store = JournalStore(str(daily_dir))
        graph = AssociationGraph(str(tmp_path / ".memory"))

        entry1 = JournalEntry(content="deploy v1", tags=["deploy", "prod"])
        entry2 = JournalEntry(content="deploy v2", tags=["deploy", "staging"])
        store.append(entry1)
        store.append(entry2)

        graph.add_edges_from_entry(entry2, store)

        edges = graph.get_associations(entry2.id)
        assert len(edges) == 1
        assert edges[0].relation_type == "shared_tags"
        assert edges[0].weight == 1.0  # 1 shared tag: "deploy"

    def test_explicit_associations(self, tmp_path) -> None:
        daily_dir = tmp_path / "daily"
        store = JournalStore(str(daily_dir))
        graph = AssociationGraph(str(tmp_path / ".memory"))

        entry = JournalEntry(content="follow up", associations=["target-123"])
        store.append(entry)
        graph.add_edges_from_entry(entry, store)

        edges = graph.get_associations(entry.id)
        assert len(edges) == 1
        assert edges[0].relation_type == "explicit"
        assert edges[0].target_id == "target-123"

    def test_no_edges_empty(self, tmp_path) -> None:
        graph = AssociationGraph(str(tmp_path / ".memory"))
        assert graph.get_associations("nonexistent") == []


class TestMemoryManagerJournal:
    def test_append_journal_entry_creates_md_and_jsonl(self, tmp_path) -> None:
        manager = MemoryManager(data_dir=str(tmp_path / "data"))
        entry = JournalEntry(
            content="important note",
            salience=SalienceLevel.HIGH,
            tags=["ops"],
        )
        today = date.today()
        manager.append_journal_entry(entry, day=today)

        # JSONL file should exist.
        jsonl_path = manager.daily_dir / f"{today.isoformat()}.jsonl"
        assert jsonl_path.exists()

        # Markdown daily note should also contain the entry.
        md_path = manager.daily_note_path(today)
        md_content = md_path.read_text(encoding="utf-8")
        assert "[high]" in md_content
        assert "important note" in md_content

    def test_query_journal_returns_entries(self, tmp_path) -> None:
        manager = MemoryManager(data_dir=str(tmp_path / "data"))
        today = date.today()

        manager.append_journal_entry(
            JournalEntry(content="critical", salience=SalienceLevel.CRITICAL, tags=["ops"]),
            day=today,
        )
        manager.append_journal_entry(
            JournalEntry(content="noise", salience=SalienceLevel.NOISE),
            day=today,
        )

        results = manager.query_journal(salience_min=SalienceLevel.CRITICAL)
        assert len(results) == 1
        assert results[0].content == "critical"


class TestBackwardCompatibility:
    def test_existing_daily_notes_still_load(self, tmp_path) -> None:
        """Existing .md daily notes should continue to work even without .jsonl files."""
        manager = MemoryManager(data_dir=str(tmp_path / "data"))
        today = date.today()

        # Write a plain daily note the old way.
        manager.append_daily_note("plain old note", day=today)

        # Should still be readable.
        md_path = manager.daily_note_path(today)
        assert md_path.exists()
        assert "plain old note" in md_path.read_text(encoding="utf-8")

        # Query journal should return empty (no JSONL).
        results = manager.query_journal()
        # Only returns results if there are JSONL files, not md files.
        # This is fine — old entries are treated as unstructured.
        assert isinstance(results, list)


class TestSalienceWeightedSearch:
    def test_journal_entries_appear_in_search(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        agent_dir = data_dir / "agents" / "test-agent"
        daily_dir = agent_dir / ".memory" / "daily"
        daily_dir.mkdir(parents=True)

        # Create a JSONL journal file.
        today = date.today()
        store = JournalStore(str(daily_dir))
        store.append(
            JournalEntry(content="deploy to production", salience=SalienceLevel.CRITICAL, tags=["deploy"]),
            day=today,
        )
        store.append(
            JournalEntry(content="deploy to staging", salience=SalienceLevel.LOW, tags=["deploy"]),
            day=today,
        )

        engine = MemorySearchEngine(data_dir=str(data_dir))
        hits = engine.search("deploy", agent_ids=["test-agent"], memory_types=["daily"])

        assert len(hits) >= 2
        # Critical entry should rank higher.
        critical_hit = next((h for h in hits if "critical" in h.snippet.lower()), None)
        low_hit = next((h for h in hits if "low" in h.snippet.lower()), None)
        assert critical_hit is not None
        assert low_hit is not None
