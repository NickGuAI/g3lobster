"""Tests for the salience-classified journal and association graph."""

from __future__ import annotations

import json
from datetime import date, timedelta
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


# ---------------------------------------------------------------------------
# SalienceLevel
# ---------------------------------------------------------------------------


class TestSalienceLevel:
    def test_from_value_valid(self):
        assert SalienceLevel.from_value("critical") is SalienceLevel.CRITICAL
        assert SalienceLevel.from_value("HIGH") is SalienceLevel.HIGH
        assert SalienceLevel.from_value("Normal") is SalienceLevel.NORMAL
        assert SalienceLevel.from_value("low") is SalienceLevel.LOW
        assert SalienceLevel.from_value("noise") is SalienceLevel.NOISE

    def test_from_value_none(self):
        assert SalienceLevel.from_value(None) is SalienceLevel.NORMAL

    def test_from_value_invalid(self):
        assert SalienceLevel.from_value("unknown") is SalienceLevel.NORMAL

    def test_weight_values(self):
        assert SalienceLevel.CRITICAL.weight == 5.0
        assert SalienceLevel.HIGH.weight == 3.0
        assert SalienceLevel.NORMAL.weight == 1.0
        assert SalienceLevel.LOW.weight == 0.5
        assert SalienceLevel.NOISE.weight == 0.1


# ---------------------------------------------------------------------------
# JournalEntry
# ---------------------------------------------------------------------------


class TestJournalEntry:
    def test_defaults(self):
        entry = JournalEntry(content="test")
        assert entry.content == "test"
        assert entry.salience is SalienceLevel.NORMAL
        assert entry.tags == []
        assert entry.id  # UUID generated

    def test_as_dict_roundtrip(self):
        entry = JournalEntry(
            content="hello",
            salience=SalienceLevel.HIGH,
            tags=["foo", "bar"],
            source_session="s1",
        )
        d = entry.as_dict()
        restored = JournalEntry.from_dict(d)
        assert restored.content == "hello"
        assert restored.salience is SalienceLevel.HIGH
        assert restored.tags == ["foo", "bar"]
        assert restored.source_session == "s1"
        assert restored.id == entry.id


# ---------------------------------------------------------------------------
# JournalStore CRUD
# ---------------------------------------------------------------------------


class TestJournalStore:
    def test_append_and_read(self, tmp_path: Path):
        store = JournalStore(str(tmp_path / "daily"))
        entry = JournalEntry(content="first entry", tags=["test"])
        store.append(entry)

        entries = store.read_day()
        assert len(entries) == 1
        assert entries[0].content == "first entry"

    def test_read_empty_day(self, tmp_path: Path):
        store = JournalStore(str(tmp_path / "daily"))
        assert store.read_day() == []

    def test_get_entry_by_id(self, tmp_path: Path):
        store = JournalStore(str(tmp_path / "daily"))
        e1 = JournalEntry(content="one")
        e2 = JournalEntry(content="two")
        store.append(e1)
        store.append(e2)

        found = store.get_entry(e2.id)
        assert found is not None
        assert found.content == "two"

    def test_get_entry_not_found(self, tmp_path: Path):
        store = JournalStore(str(tmp_path / "daily"))
        assert store.get_entry("nonexistent") is None

    def test_query_by_salience(self, tmp_path: Path):
        store = JournalStore(str(tmp_path / "daily"))
        store.append(JournalEntry(content="crit", salience=SalienceLevel.CRITICAL))
        store.append(JournalEntry(content="norm", salience=SalienceLevel.NORMAL))
        store.append(JournalEntry(content="noise", salience=SalienceLevel.NOISE))

        results = store.query(salience_min=SalienceLevel.HIGH)
        contents = {e.content for e in results}
        assert "crit" in contents
        # HIGH is at index 1, so it includes CRITICAL (0) and HIGH (1)
        assert "norm" not in contents
        assert "noise" not in contents

    def test_query_by_tags(self, tmp_path: Path):
        store = JournalStore(str(tmp_path / "daily"))
        store.append(JournalEntry(content="a", tags=["python", "ml"]))
        store.append(JournalEntry(content="b", tags=["rust"]))
        store.append(JournalEntry(content="c", tags=["python"]))

        results = store.query(tags=["python"])
        contents = {e.content for e in results}
        assert contents == {"a", "c"}

    def test_query_by_date_range(self, tmp_path: Path):
        store = JournalStore(str(tmp_path / "daily"))
        today = date.today()
        yesterday = today - timedelta(days=1)
        store.append(JournalEntry(content="today"), day=today)
        store.append(JournalEntry(content="yesterday"), day=yesterday)

        results = store.query(start_date=today)
        contents = {e.content for e in results}
        assert "today" in contents
        assert "yesterday" not in contents

    def test_query_limit(self, tmp_path: Path):
        store = JournalStore(str(tmp_path / "daily"))
        for i in range(10):
            store.append(JournalEntry(content=f"entry-{i}"))
        results = store.query(limit=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# AssociationGraph
# ---------------------------------------------------------------------------


class TestAssociationGraph:
    def test_add_and_get(self, tmp_path: Path):
        graph = AssociationGraph(str(tmp_path / ".memory"))
        edge = AssociationEdge(source_id="a", target_id="b", relation_type="test")
        graph.add_edge(edge)

        edges = graph.get_associations("a")
        assert len(edges) == 1
        assert edges[0].target_id == "b"

        # Also found when querying target.
        edges_b = graph.get_associations("b")
        assert len(edges_b) == 1

    def test_auto_link_shared_tags(self, tmp_path: Path):
        daily_dir = tmp_path / "daily"
        store = JournalStore(str(daily_dir))
        graph = AssociationGraph(str(tmp_path / ".memory"))

        e1 = JournalEntry(content="first", tags=["python", "ml"])
        e2 = JournalEntry(content="second", tags=["python", "web"])
        e3 = JournalEntry(content="third", tags=["rust"])
        store.append(e1)
        store.append(e2)
        store.append(e3)

        # Link e2 — should connect to e1 (shared 'python') but not e3.
        edges = graph.add_edges_for_entry(e2, store)
        target_ids = {e.target_id for e in edges}
        assert e1.id in target_ids
        assert e3.id not in target_ids

    def test_explicit_associations(self, tmp_path: Path):
        daily_dir = tmp_path / "daily"
        store = JournalStore(str(daily_dir))
        graph = AssociationGraph(str(tmp_path / ".memory"))

        e1 = JournalEntry(content="first")
        store.append(e1)

        e2 = JournalEntry(content="second", associations=[e1.id])
        store.append(e2)

        edges = graph.add_edges_for_entry(e2, store)
        assert any(e.target_id == e1.id and e.relation_type == "explicit" for e in edges)

    def test_empty_graph(self, tmp_path: Path):
        graph = AssociationGraph(str(tmp_path / ".memory"))
        assert graph.get_associations("nonexistent") == []


# ---------------------------------------------------------------------------
# MemoryManager integration
# ---------------------------------------------------------------------------


class TestMemoryManagerJournal:
    def _make_manager(self, tmp_path: Path) -> MemoryManager:
        return MemoryManager(data_dir=str(tmp_path / "agent"))

    def test_append_journal_entry(self, tmp_path: Path):
        mgr = self._make_manager(tmp_path)
        entry = JournalEntry(
            content="important insight",
            salience=SalienceLevel.HIGH,
            tags=["test"],
        )
        saved = mgr.append_journal_entry(entry)
        assert saved.id == entry.id

        # Verify it's in the journal store.
        results = mgr.query_journal()
        assert len(results) == 1
        assert results[0].content == "important insight"

    def test_backward_compat_daily_note(self, tmp_path: Path):
        mgr = self._make_manager(tmp_path)
        entry = JournalEntry(
            content="test entry",
            salience=SalienceLevel.NORMAL,
            tags=["demo"],
        )
        mgr.append_journal_entry(entry)

        # The daily .md file should also have a human-readable line.
        md_path = mgr.daily_note_path()
        assert md_path.exists()
        md_content = md_path.read_text(encoding="utf-8")
        assert "[normal]" in md_content
        assert "test entry" in md_content

    def test_get_journal_entry(self, tmp_path: Path):
        mgr = self._make_manager(tmp_path)
        entry = JournalEntry(content="findme")
        mgr.append_journal_entry(entry)
        found = mgr.get_journal_entry(entry.id)
        assert found is not None
        assert found.content == "findme"

    def test_get_journal_associations(self, tmp_path: Path):
        mgr = self._make_manager(tmp_path)
        e1 = JournalEntry(content="a", tags=["shared"])
        e2 = JournalEntry(content="b", tags=["shared"])
        mgr.append_journal_entry(e1)
        mgr.append_journal_entry(e2)

        edges = mgr.get_journal_associations(e2.id)
        assert len(edges) >= 1
        assert any(e["target_id"] == e1.id for e in edges)

    def test_existing_daily_notes_still_work(self, tmp_path: Path):
        """Existing .md daily notes continue to load as normal."""
        mgr = self._make_manager(tmp_path)
        mgr.append_daily_note("plain text note")

        md_content = mgr.daily_note_path().read_text(encoding="utf-8")
        assert "plain text note" in md_content


# ---------------------------------------------------------------------------
# Search with salience weighting
# ---------------------------------------------------------------------------


class TestSalienceWeightedSearch:
    def test_journal_search_returns_results(self, tmp_path: Path):
        agent_id = "test-agent"
        agent_dir = tmp_path / "agents" / agent_id
        daily_dir = agent_dir / ".memory" / "daily"
        daily_dir.mkdir(parents=True)

        store = JournalStore(str(daily_dir))
        store.append(JournalEntry(
            content="python machine learning project",
            salience=SalienceLevel.CRITICAL,
            tags=["python"],
        ))
        store.append(JournalEntry(
            content="grocery list",
            salience=SalienceLevel.NOISE,
        ))

        engine = MemorySearchEngine(str(tmp_path))
        hits = engine.search("python", agent_ids=[agent_id], memory_types=["journal"])
        assert len(hits) >= 1
        assert hits[0].salience_weight == 5.0  # CRITICAL weight

    def test_salience_weighting_affects_ranking(self, tmp_path: Path):
        agent_id = "test-agent"
        agent_dir = tmp_path / "agents" / agent_id
        daily_dir = agent_dir / ".memory" / "daily"
        daily_dir.mkdir(parents=True)

        store = JournalStore(str(daily_dir))
        store.append(JournalEntry(
            content="python is great",
            salience=SalienceLevel.NOISE,
        ))
        store.append(JournalEntry(
            content="python is critical",
            salience=SalienceLevel.CRITICAL,
        ))

        engine = MemorySearchEngine(str(tmp_path))
        hits = engine.search("python", agent_ids=[agent_id], memory_types=["journal"])
        assert len(hits) == 2
        # Critical entry should rank higher due to salience weight.
        assert hits[0].salience_weight > hits[1].salience_weight


# ---------------------------------------------------------------------------
# Salience classification in compaction
# ---------------------------------------------------------------------------


class TestCompactionSalienceClassification:
    def test_classify_user_preference_as_high(self, tmp_path: Path):
        mgr = MemoryManager(data_dir=str(tmp_path / "agent"))
        assert mgr._classify_salience("user", "I prefer dark mode") is SalienceLevel.HIGH

    def test_classify_normal_user_message(self, tmp_path: Path):
        mgr = MemoryManager(data_dir=str(tmp_path / "agent"))
        assert mgr._classify_salience("user", "what time is it?") is SalienceLevel.NORMAL

    def test_classify_short_assistant_as_low(self, tmp_path: Path):
        mgr = MemoryManager(data_dir=str(tmp_path / "agent"))
        assert mgr._classify_salience("assistant", "ok") is SalienceLevel.LOW
