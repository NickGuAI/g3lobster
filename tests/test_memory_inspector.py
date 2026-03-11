"""Tests for the memory inspector feature — intent detection, card building, and /forget."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from g3lobster.chat.cards import build_memory_inspector_card
from g3lobster.chat.memory_inspector import (
    MEMORY_QUERY_PATTERNS,
    build_memory_card,
    detect_memory_query,
    _gather_daily_notes,
    _gather_preferences,
    _gather_procedures,
    _gather_stats,
)
from g3lobster.memory.manager import MemoryManager


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

class TestDetectMemoryQuery:
    """Test natural language memory query detection."""

    @pytest.mark.parametrize(
        "text",
        [
            "what do you remember about me?",
            "What do you remember about me",
            "  what do you remember about me  ",
            "what do you know about me?",
            "What procedures have you learned?",
            "show my preferences",
            "show me my memory",
            "show memory",
            "my memory",
            "what have you remembered?",
            "memory inspector",
            "@robo what do you remember about me?",
        ],
    )
    def test_positive_matches(self, text: str) -> None:
        assert detect_memory_query(text) == "memory"

    @pytest.mark.parametrize(
        "text",
        [
            "hello",
            "what is the weather?",
            "tell me about Python",
            "schedule a meeting",
            "/cron list",
            "remember to buy milk",
            "",
            "   ",
        ],
    )
    def test_negative_matches(self, text: str) -> None:
        assert detect_memory_query(text) is None


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------

class TestBuildMemoryInspectorCard:
    """Test Cards v2 payload generation."""

    def test_basic_structure(self) -> None:
        cards = build_memory_inspector_card(
            agent_name="Robo",
            agent_emoji="🤖",
            preferences=["Always use dark mode"],
            procedures=[
                {"title": "Deploy app", "weight": 5.0, "status": "usable", "steps_count": 3},
            ],
            daily_notes=["*2026-03-10*: Worked on feature X"],
            stats={
                "sessions_total": 10,
                "messages_total": 150,
                "memory_bytes": 2048,
                "daily_notes_count": 5,
            },
        )

        assert len(cards) == 1
        card = cards[0]
        assert card["cardId"] == "memory-inspector"
        assert "Memory Inspector" in card["card"]["header"]["title"]

        sections = card["card"]["sections"]
        assert len(sections) == 4  # stats, prefs, procedures, daily notes

    def test_empty_state(self) -> None:
        cards = build_memory_inspector_card(
            agent_name="Bot",
            agent_emoji="🤖",
            preferences=[],
            procedures=[],
            daily_notes=[],
            stats={
                "sessions_total": 0,
                "messages_total": 0,
                "memory_bytes": 0,
                "daily_notes_count": 0,
            },
        )

        sections = cards[0]["card"]["sections"]
        # Preferences section should show "no preferences" message
        prefs_section = sections[1]
        assert "No preferences" in prefs_section["widgets"][0]["decoratedText"]["text"]

        # Procedures section should show "no procedures" message
        procs_section = sections[2]
        assert "No procedures" in procs_section["widgets"][0]["decoratedText"]["text"]

    def test_preferences_truncation(self) -> None:
        long_pref = "x" * 300
        cards = build_memory_inspector_card(
            agent_name="Bot",
            agent_emoji="🤖",
            preferences=[long_pref],
            procedures=[],
            daily_notes=[],
            stats={"sessions_total": 0, "messages_total": 0, "memory_bytes": 0, "daily_notes_count": 0},
        )

        prefs_section = cards[0]["card"]["sections"][1]
        text = prefs_section["widgets"][0]["decoratedText"]["text"]
        assert len(text) <= 203  # 200 + "..."


# ---------------------------------------------------------------------------
# Data gathering (with real MemoryManager on tmp dirs)
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    """Create a temporary agent data directory with memory structure."""
    data_dir = tmp_path / "agent-data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def memory_manager(memory_dir: Path) -> MemoryManager:
    return MemoryManager(data_dir=str(memory_dir))


class TestGatherPreferences:
    def test_empty_preferences(self, memory_manager: MemoryManager) -> None:
        prefs = _gather_preferences(memory_manager, None, "user1")
        assert prefs == []

    def test_tagged_preferences(self, memory_manager: MemoryManager) -> None:
        memory_manager.append_tagged_memory("preference", "Always use dark mode")
        memory_manager.append_tagged_memory("user preference", "Prefers concise replies")

        prefs = _gather_preferences(memory_manager, None, "user1")
        assert len(prefs) == 2
        assert "dark mode" in prefs[0]
        assert "concise" in prefs[1]


class TestGatherProcedures:
    def test_empty_procedures(self, memory_manager: MemoryManager) -> None:
        procs = _gather_procedures(memory_manager, None)
        assert procs == []


class TestGatherDailyNotes:
    def test_no_notes(self, memory_manager: MemoryManager) -> None:
        notes = _gather_daily_notes(memory_manager)
        assert notes == []

    def test_with_notes(self, memory_manager: MemoryManager) -> None:
        (memory_manager.daily_dir / "2026-03-10.md").write_text("Worked on feature X", encoding="utf-8")
        (memory_manager.daily_dir / "2026-03-09.md").write_text("Fixed bug Y", encoding="utf-8")

        notes = _gather_daily_notes(memory_manager)
        assert len(notes) == 2
        assert "2026-03-10" in notes[0]


class TestGatherStats:
    def test_empty_stats(self, memory_manager: MemoryManager) -> None:
        stats = _gather_stats(memory_manager)
        assert stats["sessions_total"] == 0
        assert stats["messages_total"] == 0
        assert stats["memory_bytes"] > 0  # MEMORY.md has "# MEMORY\n\n"


# ---------------------------------------------------------------------------
# Full card builder
# ---------------------------------------------------------------------------

class TestBuildMemoryCard:
    def test_full_card(self, memory_manager: MemoryManager) -> None:
        memory_manager.append_tagged_memory("preference", "Always respond in English")

        result = asyncio.get_event_loop().run_until_complete(
            build_memory_card(
                agent_name="Robo",
                agent_emoji="🤖",
                memory_manager=memory_manager,
                global_memory=None,
                user_id="user1",
            )
        )

        assert "cardsV2" in result
        cards = result["cardsV2"]
        assert len(cards) == 1
        assert cards[0]["cardId"] == "memory-inspector"


# ---------------------------------------------------------------------------
# delete_tagged_memory (used by /forget)
# ---------------------------------------------------------------------------

class TestDeleteTaggedMemory:
    def test_delete_existing(self, memory_manager: MemoryManager) -> None:
        memory_manager.append_tagged_memory("preference", "Like dark mode")
        memory_manager.append_tagged_memory("preference", "Prefer short answers")

        assert memory_manager.delete_tagged_memory("preference", 0)
        prefs = memory_manager.get_memories_by_tag("preference")
        assert len(prefs) == 1
        assert "short answers" in prefs[0]

    def test_delete_out_of_range(self, memory_manager: MemoryManager) -> None:
        memory_manager.append_tagged_memory("preference", "Like dark mode")
        assert not memory_manager.delete_tagged_memory("preference", 5)

    def test_delete_nonexistent_tag(self, memory_manager: MemoryManager) -> None:
        assert not memory_manager.delete_tagged_memory("nonexistent", 0)
