"""Tests for journal CRUD, salience classification, association graph,
backward-compatible daily note loading, and salience-weighted search."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from g3lobster.memory.journal import (
    AssociationEdge,
    AssociationGraph,
    JournalEntry,
    JournalStore,
    SalienceLevel,
)
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.search import MemorySearchEngine


# ── SalienceLevel ────────────────────────────────────────────────────────


class TestSalienceLevel:
    def test_from_value_known(self):
        assert SalienceLevel.from_value("critical") is SalienceLevel.CRITICAL
        assert SalienceLevel.from_value("HIGH") is SalienceLevel.HIGH

    def test_from_value_unknown_defaults_normal(self):
        assert SalienceLevel.from_value("bogus") is SalienceLevel.NORMAL
        assert SalienceLevel.from_value(None) is SalienceLevel.NORMAL

    def test_weight_multiplier(self):
        assert SalienceLevel.CRITICAL.weight == 5.0
        assert SalienceLevel.HIGH.weight == 3.0
        assert SalienceLevel.NORMAL.weight == 1.0
        assert SalienceLevel.LOW.weight == 0.5
        assert SalienceLevel.NOISE.weight == 0.1


# ── JournalEntry ─────────────────────────────────────────────────────────


class TestJournalEntry:
    def test_round_trip(self):
        entry = JournalEntry(
            content="hello world",
            salience="high",
            tags=["test", "demo"],
            source_session="s1",
        )
        data = entry.as_dict()
        restored = JournalEntry.from_dict(data)
        assert restored.content == "hello world"
        assert restored.salience == "high"
        assert restored.tags == ["test", "demo"]
        assert restored.id == entry.id

    def test_salience_level_property(self):
        entry = JournalEntry(salience="critical")
        assert entry.salience_level is SalienceLevel.CRITICAL

    def test_defaults(self):
        entry = JournalEntry()
        assert entry.salience == "normal"
        assert entry.tags == []
        assert entry.associations == []
        assert entry.id  # UUID is generated


# ── JournalStore ─────────────────────────────────────────────────────────


class TestJournalStore:
    def test_append_and_read(self, tmp_path: Path):
        store = JournalStore(tmp_path / "daily")
        entry = JournalEntry(content="test entry", salience="high", tags=["alpha"])
        store.append(entry)

        entries = store.read_day()
        assert len(entries) == 1
        assert entries[0].id == entry.id
        assert entries[0].content == "test entry"
        assert entries[0].salience == "high"

    def test_read_empty_day(self, tmp_path: Path):
        store = JournalStore(tmp_path / "daily")
        assert store.read_day(date(2020, 1, 1)) == []

    def test_get_entry_by_id(self, tmp_path: Path):
        store = JournalStore(tmp_path / "daily")
        e1 = JournalEntry(content="first")
        e2 = JournalEntry(content="second")
        store.append(e1)
        store.append(e2)

        found = store.get_entry(e2.id)
        assert found is not None
        assert found.content == "second"

    def test_get_entry_not_found(self, tmp_path: Path):
        store = JournalStore(tmp_path / "daily")
        assert store.get_entry("nonexistent") is None

    def test_query_by_salience(self, tmp_path: Path):
        store = JournalStore(tmp_path / "daily")
        store.append(JournalEntry(content="critical item", salience="critical"))
        store.append(JournalEntry(content="normal item", salience="normal"))
        store.append(JournalEntry(content="noise item", salience="noise"))

        results = store.query(salience_min="high")
        contents = [r.content for r in results]
        assert "critical item" in contents
        # normal and noise should be excluded
        assert "normal item" not in contents
        assert "noise item" not in contents

    def test_query_by_tags(self, tmp_path: Path):
        store = JournalStore(tmp_path / "daily")
        store.append(JournalEntry(content="tagged", tags=["deploy"]))
        store.append(JournalEntry(content="untagged", tags=["other"]))

        results = store.query(tags=["deploy"])
        assert len(results) == 1
        assert results[0].content == "tagged"

    def test_query_limit(self, tmp_path: Path):
        store = JournalStore(tmp_path / "daily")
        for i in range(10):
            store.append(JournalEntry(content=f"entry {i}"))
        results = store.query(limit=3)
        assert len(results) == 3

    def test_list_days(self, tmp_path: Path):
        store = JournalStore(tmp_path / "daily")
        today = date.today()
        store.append(JournalEntry(content="today"))
        days = store.list_days()
        assert today in days


# ── AssociationGraph ─────────────────────────────────────────────────────


class TestAssociationGraph:
    def test_add_and_get_edge(self, tmp_path: Path):
        graph = AssociationGraph(tmp_path / ".memory")
        edge = AssociationEdge(source_id="a", target_id="b", relation_type="related")
        graph.add_edge(edge)

        edges = graph.get_associations("a")
        assert len(edges) == 1
        assert edges[0].source_id == "a"
        assert edges[0].target_id == "b"

    def test_get_associations_both_directions(self, tmp_path: Path):
        graph = AssociationGraph(tmp_path / ".memory")
        graph.add_edge(AssociationEdge(source_id="a", target_id="b"))
        graph.add_edge(AssociationEdge(source_id="c", target_id="a"))

        edges = graph.get_associations("a")
        assert len(edges) == 2

    def test_auto_link_shared_tags(self, tmp_path: Path):
        daily_dir = tmp_path / "daily"
        store = JournalStore(daily_dir)
        graph = AssociationGraph(tmp_path / ".memory")

        e1 = JournalEntry(content="first", tags=["deploy", "prod"])
        e2 = JournalEntry(content="second", tags=["deploy", "staging"])
        store.append(e1)
        store.append(e2)

        created = graph.add_edges_from_entry(e2, store)
        assert len(created) == 1
        assert created[0].relation_type == "shared_tags"

    def test_bfs_traversal(self, tmp_path: Path):
        graph = AssociationGraph(tmp_path / ".memory")
        graph.add_edge(AssociationEdge(source_id="a", target_id="b"))
        graph.add_edge(AssociationEdge(source_id="b", target_id="c"))
        graph.add_edge(AssociationEdge(source_id="c", target_id="d"))

        # Depth 1: only direct neighbors
        ids_1 = graph.get_connected_ids("a", max_depth=1)
        assert ids_1 == ["b"]

        # Depth 2: two hops
        ids_2 = graph.get_connected_ids("a", max_depth=2)
        assert "b" in ids_2
        assert "c" in ids_2
        assert "d" not in ids_2

    def test_empty_graph(self, tmp_path: Path):
        graph = AssociationGraph(tmp_path / ".memory")
        assert graph.get_associations("nonexistent") == []
        assert graph.get_connected_ids("nonexistent") == []


# ── MemoryManager journal integration ────────────────────────────────────


class TestMemoryManagerJournal:
    def test_append_journal_entry_creates_both_files(self, tmp_path: Path):
        manager = MemoryManager(data_dir=str(tmp_path / "data"))
        entry = JournalEntry(content="test note", salience="high", tags=["demo"])
        manager.append_journal_entry(entry)

        # JSONL file should exist
        jsonl_files = list(manager.daily_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1

        # MD file should also exist (backward compat)
        md_files = list(manager.daily_dir.glob("*.md"))
        assert len(md_files) == 1
        md_content = md_files[0].read_text()
        assert "[high]" in md_content
        assert "test note" in md_content

    def test_query_journal(self, tmp_path: Path):
        manager = MemoryManager(data_dir=str(tmp_path / "data"))
        manager.append_journal_entry(JournalEntry(content="important", salience="critical"))
        manager.append_journal_entry(JournalEntry(content="trivial", salience="noise"))

        results = manager.query_journal(salience_min="high")
        contents = [r.content for r in results]
        assert "important" in contents
        assert "trivial" not in contents

    def test_compaction_creates_journal_entries(self, tmp_path: Path):
        """Compacted messages should produce structured journal entries."""
        manager = MemoryManager(
            data_dir=str(tmp_path / "data"),
            compact_threshold=4,
            compact_keep_ratio=0.25,
        )
        session_id = "test-session"
        for i in range(6):
            role = "user" if i % 2 == 0 else "assistant"
            manager.append_message(session_id, role, f"Message {i}")

        # After compaction, journal entries should exist
        entries = manager.journal_store.read_day()
        assert len(entries) > 0
        # All should have 'compaction' tag
        for entry in entries:
            assert "compaction" in entry.tags

    def test_salience_classification_user_preference(self, tmp_path: Path):
        """User preferences should be classified as HIGH salience."""
        assert MemoryManager._classify_salience("user", "I always prefer dark mode") is SalienceLevel.HIGH

    def test_salience_classification_chitchat(self, tmp_path: Path):
        """Chitchat should be classified as LOW salience."""
        assert MemoryManager._classify_salience("user", "ok thanks!") is SalienceLevel.LOW

    def test_salience_classification_normal(self, tmp_path: Path):
        """Regular messages should be classified as NORMAL salience."""
        assert MemoryManager._classify_salience("assistant", "Here is the code you requested") is SalienceLevel.NORMAL


# ── Backward compatibility ───────────────────────────────────────────────


class TestBackwardCompatibility:
    def test_existing_md_daily_notes_still_load(self, tmp_path: Path):
        """Existing .md daily notes should continue to be readable."""
        manager = MemoryManager(data_dir=str(tmp_path / "data"))
        # Write directly to the .md file (simulating pre-journal notes)
        manager.append_daily_note("Old-style note")

        md_content = manager.daily_note_path().read_text()
        assert "Old-style note" in md_content

    def test_search_finds_md_daily_notes(self, tmp_path: Path):
        """MemorySearchEngine should still find content in .md daily files."""
        data_dir = tmp_path / "data"
        agent_dir = data_dir / "agents" / "test-agent" / ".memory" / "daily"
        agent_dir.mkdir(parents=True)
        today = date.today().isoformat()
        (agent_dir / f"{today}.md").write_text("unique_keyword_test\n")

        engine = MemorySearchEngine(str(data_dir))
        hits = engine.search("unique_keyword_test", agent_ids=["test-agent"], memory_types=["daily"])
        assert len(hits) >= 1


# ── Salience-weighted search ─────────────────────────────────────────────


class TestSalienceWeightedSearch:
    def test_journal_search_returns_hits(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        agent_dir = data_dir / "agents" / "test-agent" / ".memory" / "daily"
        agent_dir.mkdir(parents=True)

        store = JournalStore(str(agent_dir))
        store.append(JournalEntry(content="deploy to production", salience="critical", tags=["deploy"]))
        store.append(JournalEntry(content="random chatter", salience="noise"))

        engine = MemorySearchEngine(str(data_dir))
        hits = engine.search("deploy", agent_ids=["test-agent"], memory_types=["journal"])
        assert len(hits) >= 1
        assert hits[0].salience_weight == 5.0  # critical = 5x

    def test_critical_entries_rank_higher(self, tmp_path: Path):
        """Critical-salience entries should rank above normal ones with same timestamp."""
        data_dir = tmp_path / "data"
        agent_dir = data_dir / "agents" / "test-agent" / ".memory" / "daily"
        agent_dir.mkdir(parents=True)

        store = JournalStore(str(agent_dir))
        ts = datetime.now(tz=timezone.utc).isoformat()
        store.append(JournalEntry(content="searchterm normal", salience="normal", timestamp=ts))
        store.append(JournalEntry(content="searchterm critical", salience="critical", timestamp=ts))

        engine = MemorySearchEngine(str(data_dir))
        hits = engine.search("searchterm", agent_ids=["test-agent"], memory_types=["journal"])
        assert len(hits) == 2
        # Critical should be ranked first due to higher salience weight
        assert hits[0].salience_weight > hits[1].salience_weight
