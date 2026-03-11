"""Tests for the memory inspector feature: intent detection, card building, and /forget."""

from __future__ import annotations

import pytest

from g3lobster.chat.cards import build_forget_button, build_memory_inspector_card
from g3lobster.chat.memory_inspector import detect_memory_query
from g3lobster.memory.manager import MemoryManager


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


class TestDetectMemoryQuery:
    """Verify MEMORY_QUERY_PATTERNS match the expected triggers."""

    @pytest.mark.parametrize(
        "text",
        [
            "what do you remember about me?",
            "What do you remember?",
            "WHAT DO YOU REMEMBER ABOUT ME",
            "what have you learned?",
            "what procedures have you learned so far?",
            "show my preferences",
            "show memory",
            "show my memories",
            "my memory",
            "what do you know about me?",
            "Hey, what do you know about me anyway?",
            "what have you stored about me",
            "memory inspector",
        ],
    )
    def test_positive_matches(self, text: str) -> None:
        assert detect_memory_query(text) == "memory_query"

    @pytest.mark.parametrize(
        "text",
        [
            "hello",
            "what is the weather like?",
            "remember to buy milk",
            "/cron list",
            "can you help me with a task?",
            "tell me a joke",
            "",
            "   ",
            "the procedure is simple",
        ],
    )
    def test_negative_matches(self, text: str) -> None:
        assert detect_memory_query(text) is None


# ---------------------------------------------------------------------------
# Card payload structure
# ---------------------------------------------------------------------------


class TestBuildMemoryInspectorCard:
    """Verify the Cards v2 payload structure."""

    def test_empty_data_produces_valid_card(self) -> None:
        cards = build_memory_inspector_card()
        assert isinstance(cards, list)
        assert len(cards) == 1
        card = cards[0]
        assert card["cardId"] == "memory-inspector"
        assert "header" in card["card"]
        assert "sections" in card["card"]
        # 5 sections: preferences, procedures, daily notes, stats, forget hint
        assert len(card["card"]["sections"]) == 5

    def test_with_preferences(self) -> None:
        prefs = ["Always use bullet points", "Prefers dark mode"]
        cards = build_memory_inspector_card(preferences=prefs)
        pref_section = cards[0]["card"]["sections"][0]
        assert pref_section["header"] == "\U0001f3af User Preferences"
        assert len(pref_section["widgets"]) == 2

    def test_with_procedures(self) -> None:
        procs = [
            {"title": "Deploy", "weight": 5.0, "status": "usable", "steps": ["step1", "step2"]},
            {"title": "Rollback", "weight": 10.0, "status": "permanent", "steps": ["a", "b", "c"]},
        ]
        cards = build_memory_inspector_card(procedures=procs)
        proc_section = cards[0]["card"]["sections"][1]
        assert proc_section["header"] == "\U0001f4d6 Learned Procedures"
        assert len(proc_section["widgets"]) == 2

    def test_with_daily_notes(self) -> None:
        notes = ["2025-01-01: Did stuff", "2025-01-02: More stuff"]
        cards = build_memory_inspector_card(daily_notes=notes)
        notes_section = cards[0]["card"]["sections"][2]
        assert notes_section["header"] == "\U0001f4c5 Recent Context"
        assert len(notes_section["widgets"]) == 2

    def test_with_stats(self) -> None:
        stats = {
            "total_sessions": 42,
            "total_messages": 300,
            "memory_bytes": 2048,
            "procedures_count": 5,
            "daily_notes_count": 10,
        }
        cards = build_memory_inspector_card(stats=stats)
        stats_section = cards[0]["card"]["sections"][3]
        assert stats_section["header"] == "\U0001f4ca Agent Stats"
        # Should contain the session count in the HTML
        widget_text = stats_section["widgets"][0]["decoratedText"]["text"]
        assert "42" in widget_text
        assert "300" in widget_text

    def test_agent_name_in_header(self) -> None:
        cards = build_memory_inspector_card(agent_name="Iris", agent_emoji="\U0001f338")
        header = cards[0]["card"]["header"]
        assert "Iris" in header["subtitle"]
        assert "\U0001f338" in header["title"]


class TestBuildForgetButton:
    def test_button_structure(self) -> None:
        btn = build_forget_button("preference", "3")
        assert btn["button"]["text"] == "\U0001f5d1 Forget"
        params = btn["button"]["onClick"]["action"]["parameters"]
        param_map = {p["key"]: p["value"] for p in params}
        assert param_map["type"] == "preference"
        assert param_map["id"] == "3"


# ---------------------------------------------------------------------------
# MemoryManager.delete_tagged_memory
# ---------------------------------------------------------------------------


class TestDeleteTaggedMemory:
    def test_delete_first_entry(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)
        mm.append_tagged_memory("user preference", "I like dark mode")
        mm.append_tagged_memory("user preference", "I prefer bullet points")
        mm.append_tagged_memory("ops", "Page rotation Monday")

        assert mm.delete_tagged_memory("user preference", 0) is True
        remaining = mm.get_memories_by_tag("user preference")
        assert len(remaining) == 1
        assert "bullet points" in remaining[0]

    def test_delete_second_entry(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)
        mm.append_tagged_memory("user preference", "I like dark mode")
        mm.append_tagged_memory("user preference", "I prefer bullet points")

        assert mm.delete_tagged_memory("user preference", 1) is True
        remaining = mm.get_memories_by_tag("user preference")
        assert len(remaining) == 1
        assert "dark mode" in remaining[0]

    def test_delete_nonexistent_index(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)
        mm.append_tagged_memory("user preference", "only one")

        assert mm.delete_tagged_memory("user preference", 5) is False

    def test_delete_nonexistent_tag(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)
        mm.append_tagged_memory("ops", "something")

        assert mm.delete_tagged_memory("nonexistent", 0) is False

    def test_other_tags_unaffected(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=100)
        mm.append_tagged_memory("user preference", "dark mode")
        mm.append_tagged_memory("ops", "pager rotation")

        mm.delete_tagged_memory("user preference", 0)
        ops = mm.get_memories_by_tag("ops")
        assert len(ops) == 1
        assert "pager" in ops[0]
