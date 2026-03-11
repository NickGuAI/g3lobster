"""Tests for the salience-classified journal and association graph."""

from __future__ import annotations

import json
from datetime import date, datetime

from g3lobster.memory.journal import (
    AssociationEdge,
    AssociationGraph,
    JournalEntry,
    JournalStore,
    SalienceLevel,
)
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine


# ---------------------------------------------------------------------------
# SalienceLevel enum
# ---------------------------------------------------------------------------


class TestSalienceLevel:
    def test_from_value_returns_correct_enum(self) -> None:
        assert SalienceLevel.from_value("noise") is SalienceLevel.NOISE
        assert SalienceLevel.from_value("low") is SalienceLevel.LOW
        assert SalienceLevel.from_value("normal") is SalienceLevel.NORMAL
        assert SalienceLevel.from_value("high") is SalienceLevel.HIGH
        assert SalienceLevel.from_value("critical") is SalienceLevel.CRITICAL

    def test_from_value_case_insensitive(self) -> None:
        assert SalienceLevel.from_value("HIGH") is SalienceLevel.HIGH
        assert SalienceLevel.from_value("Normal") is SalienceLevel.NORMAL

    def test_from_value_none_returns_normal(self) -> None:
        assert SalienceLevel.from_value(None) is SalienceLevel.NORMAL

    def test_from_value_unknown_returns_normal(self) -> None:
        assert SalienceLevel.from_value("unknown") is SalienceLevel.NORMAL

    def test_weight_property(self) -> None:
        assert SalienceLevel.NOISE.weight == 0.1
        assert SalienceLevel.LOW.weight == 0.5
        assert SalienceLevel.NORMAL.weight == 1.0
        assert SalienceLevel.HIGH.weight == 3.0
        assert SalienceLevel.CRITICAL.weight == 5.0

    def test_comparison_operators(self) -> None:
        assert SalienceLevel.NOISE < SalienceLevel.LOW
        assert SalienceLevel.LOW < SalienceLevel.NORMAL
        assert SalienceLevel.NORMAL < SalienceLevel.HIGH
        assert SalienceLevel.HIGH < SalienceLevel.CRITICAL
        assert SalienceLevel.CRITICAL > SalienceLevel.NOISE
        assert SalienceLevel.NORMAL >= SalienceLevel.NORMAL
        assert SalienceLevel.NORMAL <= SalienceLevel.NORMAL


# ---------------------------------------------------------------------------
# JournalEntry
# ---------------------------------------------------------------------------


class TestJournalEntry:
    def test_roundtrip_as_dict_from_dict(self) -> None:
        entry = JournalEntry(
            id="abc-123",
            timestamp="2025-06-01T10:00:00",
            content="deployed v2",
            salience=SalienceLevel.HIGH,
            tags=["deploy", "prod"],
            source_session="sess-1",
            associations=["other-id"],
        )
        restored = JournalEntry.from_dict(entry.as_dict())
        assert restored.id == entry.id
        assert restored.timestamp == entry.timestamp
        assert restored.content == entry.content
        assert restored.salience == entry.salience
        assert restored.tags == entry.tags
        assert restored.source_session == entry.source_session
        assert restored.associations == entry.associations

    def test_from_dict_missing_fields_uses_defaults(self) -> None:
        entry = JournalEntry.from_dict({"content": "hello"})
        assert entry.id == ""
        assert entry.timestamp == ""
        assert entry.salience is SalienceLevel.NORMAL
        assert entry.tags == []
        assert entry.source_session == ""
        assert entry.associations == []


# ---------------------------------------------------------------------------
# JournalStore CRUD
# ---------------------------------------------------------------------------


class TestJournalStore:
    def test_append_writes_jsonl_and_populates_id_timestamp(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        entry = JournalEntry(id="", timestamp="", content="test note")
        result = store.append(entry)
        assert result.id != ""
        assert result.timestamp != ""
        # File should exist with one line
        files = list((tmp_path / "daily").glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["content"] == "test note"

    def test_get_finds_entry_by_id(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        entry = store.append(JournalEntry(id="", timestamp="", content="find me"))
        found = store.get(entry.id)
        assert found is not None
        assert found.content == "find me"

    def test_get_returns_none_for_nonexistent_id(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        store.append(JournalEntry(id="", timestamp="", content="exists"))
        assert store.get("does-not-exist") is None

    def test_query_returns_all_sorted_by_timestamp_desc(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        e1 = store.append(
            JournalEntry(id="", timestamp="2025-06-01T08:00:00", content="first")
        )
        e2 = store.append(
            JournalEntry(id="", timestamp="2025-06-01T12:00:00", content="second")
        )
        results = store.query()
        assert len(results) == 2
        assert results[0].id == e2.id
        assert results[1].id == e1.id

    def test_query_salience_min_filters(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        store.append(
            JournalEntry(
                id="", timestamp="2025-06-01T01:00:00", content="noise",
                salience=SalienceLevel.NOISE,
            )
        )
        store.append(
            JournalEntry(
                id="", timestamp="2025-06-01T02:00:00", content="low",
                salience=SalienceLevel.LOW,
            )
        )
        store.append(
            JournalEntry(
                id="", timestamp="2025-06-01T03:00:00", content="normal",
                salience=SalienceLevel.NORMAL,
            )
        )
        high = store.append(
            JournalEntry(
                id="", timestamp="2025-06-01T04:00:00", content="high",
                salience=SalienceLevel.HIGH,
            )
        )
        crit = store.append(
            JournalEntry(
                id="", timestamp="2025-06-01T05:00:00", content="crit",
                salience=SalienceLevel.CRITICAL,
            )
        )
        results = store.query(salience_min=SalienceLevel.HIGH)
        ids = {e.id for e in results}
        assert high.id in ids
        assert crit.id in ids
        assert len(results) == 2

    def test_query_tags_filter(self, tmp_path) -> None:
        store = JournalStore(str(tmp_path / "daily"))
        store.append(
            JournalEntry(
                id="", timestamp="2025-06-01T01:00:00", content="a",
                tags=["foo", "bar"],
            )
        )
        store.append(
            JournalEntry(
                id="", timestamp="2025-06-01T02:00:00", content="b",
                tags=["baz"],
            )
        )
        results = store.query(tags=["foo"])
        assert len(results) == 1
        assert results[0].content == "a"

    def test_query_date_range_filter(self, tmp_path) -> None:
        daily = tmp_path / "daily"
        daily.mkdir(parents=True)
        # Write entries to two separate date files
        day1 = date(2025, 6, 1)
        day2 = date(2025, 6, 3)
        e1 = JournalEntry(id="e1", timestamp="2025-06-01T10:00:00", content="day1")
        e2 = JournalEntry(id="e2", timestamp="2025-06-03T10:00:00", content="day3")
        (daily / f"{day1.isoformat()}.jsonl").write_text(
            json.dumps(e1.as_dict()) + "\n"
        )
        (daily / f"{day2.isoformat()}.jsonl").write_text(
            json.dumps(e2.as_dict()) + "\n"
        )
        store = JournalStore(str(daily))
        results = store.query(date_start=date(2025, 6, 2), date_end=date(2025, 6, 4))
        assert len(results) == 1
        assert results[0].id == "e2"

    def test_list_dates(self, tmp_path) -> None:
        daily = tmp_path / "daily"
        daily.mkdir(parents=True)
        (daily / "2025-06-01.jsonl").write_text("{}\n")
        (daily / "2025-06-03.jsonl").write_text("{}\n")
        (daily / "not-a-date.jsonl").write_text("{}\n")
        store = JournalStore(str(daily))
        dates = store.list_dates()
        assert dates == [date(2025, 6, 1), date(2025, 6, 3)]

    def test_backward_compat_md_files_not_disturbed(self, tmp_path) -> None:
        daily = tmp_path / "daily"
        daily.mkdir(parents=True)
        md_file = daily / "2025-06-01.md"
        md_file.write_text("# Old daily note\nSome content\n")
        store = JournalStore(str(daily))
        store.append(
            JournalEntry(id="", timestamp="2025-06-01T10:00:00", content="new entry")
        )
        # The .md file must be untouched
        assert md_file.read_text() == "# Old daily note\nSome content\n"
        # The JSONL file was created separately
        assert (daily / f"{date.today().isoformat()}.jsonl").exists()


# ---------------------------------------------------------------------------
# AssociationGraph
# ---------------------------------------------------------------------------


class TestAssociationGraph:
    def test_add_edge_persists_to_jsonl(self, tmp_path) -> None:
        graph = AssociationGraph(str(tmp_path / "assoc.jsonl"))
        edge = AssociationEdge(
            source_id="a", target_id="b", relation_type="shared_tag"
        )
        graph.add_edge(edge)
        lines = (tmp_path / "assoc.jsonl").read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["source_id"] == "a"
        assert data["target_id"] == "b"

    def test_get_associations_matches_source_and_target(self, tmp_path) -> None:
        graph = AssociationGraph(str(tmp_path / "assoc.jsonl"))
        graph.add_edge(
            AssociationEdge(source_id="x", target_id="y", relation_type="tag")
        )
        graph.add_edge(
            AssociationEdge(source_id="z", target_id="x", relation_type="tag")
        )
        graph.add_edge(
            AssociationEdge(source_id="z", target_id="w", relation_type="tag")
        )
        edges = graph.get_associations("x")
        assert len(edges) == 2

    def test_add_edges_for_entry_discovers_shared_tags(self, tmp_path) -> None:
        graph = AssociationGraph(str(tmp_path / "assoc.jsonl"))
        existing = [
            JournalEntry(
                id="old-1", timestamp="", content="a", tags=["deploy", "prod"]
            ),
            JournalEntry(
                id="old-2", timestamp="", content="b", tags=["review"]
            ),
        ]
        new_entry = JournalEntry(
            id="new-1", timestamp="", content="c", tags=["deploy"]
        )
        created = graph.add_edges_for_entry(new_entry, existing)
        assert len(created) == 1
        assert created[0].source_id == "new-1"
        assert created[0].target_id == "old-1"
        assert created[0].relation_type == "shared_tag"

    def test_empty_graph(self, tmp_path) -> None:
        graph = AssociationGraph(str(tmp_path / "assoc.jsonl"))
        assert graph.get_associations("nonexistent") == []


# ---------------------------------------------------------------------------
# MemoryManager journal integration
# ---------------------------------------------------------------------------


class TestMemoryManagerJournal:
    def test_append_journal_entry_creates_entry_and_md(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=40)
        entry = JournalEntry(
            id="", timestamp="", content="important event",
            salience=SalienceLevel.HIGH, tags=["ops"],
        )
        saved = mm.append_journal_entry(entry)
        assert saved.id != ""
        assert saved.timestamp != ""
        # JSONL entry exists
        found = mm.journal_store.get(saved.id)
        assert found is not None
        assert found.content == "important event"
        # .md daily note also written
        md_path = mm.daily_note_path()
        assert md_path.exists()
        md_content = md_path.read_text()
        assert "important event" in md_content
        assert "[high]" in md_content

    def test_query_journal_delegates_to_store(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=40)
        mm.append_journal_entry(
            JournalEntry(
                id="", timestamp="2025-06-01T01:00:00", content="low item",
                salience=SalienceLevel.LOW,
            )
        )
        mm.append_journal_entry(
            JournalEntry(
                id="", timestamp="2025-06-01T02:00:00", content="high item",
                salience=SalienceLevel.HIGH,
            )
        )
        results = mm.query_journal(salience_min=SalienceLevel.HIGH)
        assert len(results) == 1
        assert results[0].content == "high item"


# ---------------------------------------------------------------------------
# Search integration
# ---------------------------------------------------------------------------


class TestSearchIntegration:
    def test_search_journal_finds_jsonl_entries(self, tmp_path) -> None:
        agent_dir = tmp_path / "agents" / "test-agent" / ".memory" / "daily"
        agent_dir.mkdir(parents=True)
        entry = JournalEntry(
            id="s1", timestamp="2025-06-01T10:00:00", content="deploy finished",
            salience=SalienceLevel.NORMAL, tags=["deploy"],
        )
        (agent_dir / f"{date(2025, 6, 1).isoformat()}.jsonl").write_text(
            json.dumps(entry.as_dict()) + "\n"
        )
        engine = MemorySearchEngine(data_dir=str(tmp_path))
        hits = engine.search("deploy", memory_types=["journal"])
        assert len(hits) == 1
        assert hits[0].memory_type == "journal"
        assert "deploy finished" in hits[0].snippet

    def test_salience_weighting_affects_sort_order(self, tmp_path) -> None:
        agent_dir = tmp_path / "agents" / "test-agent" / ".memory" / "daily"
        agent_dir.mkdir(parents=True)
        # Two entries with same timestamp but different salience
        ts = "2025-06-01T10:00:00"
        low_entry = JournalEntry(
            id="lo", timestamp=ts, content="task completed",
            salience=SalienceLevel.LOW,
        )
        high_entry = JournalEntry(
            id="hi", timestamp=ts, content="task completed critical",
            salience=SalienceLevel.CRITICAL,
        )
        jsonl_path = agent_dir / "2025-06-01.jsonl"
        jsonl_path.write_text(
            json.dumps(low_entry.as_dict()) + "\n"
            + json.dumps(high_entry.as_dict()) + "\n"
        )
        engine = MemorySearchEngine(data_dir=str(tmp_path))
        hits = engine.search("task completed", memory_types=["journal"])
        assert len(hits) == 2
        # Higher salience entry should rank first
        assert hits[0].salience_weight > hits[1].salience_weight


# ---------------------------------------------------------------------------
# Salience classification in compaction
# ---------------------------------------------------------------------------


class TestCompactionSalienceClassification:
    def test_classify_user_preference_as_high(self, tmp_path) -> None:
        mgr = MemoryManager(data_dir=str(tmp_path / "agent"))
        assert mgr._classify_salience("user", "I prefer dark mode") is SalienceLevel.HIGH

    def test_classify_normal_user_message(self, tmp_path) -> None:
        mgr = MemoryManager(data_dir=str(tmp_path / "agent"))
        assert mgr._classify_salience("user", "what time is it?") is SalienceLevel.NORMAL

    def test_classify_short_assistant_as_low(self, tmp_path) -> None:
        mgr = MemoryManager(data_dir=str(tmp_path / "agent"))
        assert mgr._classify_salience("assistant", "ok") is SalienceLevel.LOW
