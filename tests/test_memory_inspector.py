"""Tests for the memory inspector feature: intent detection, card building, and data gathering."""

from __future__ import annotations

import pytest

from g3lobster.chat.cards import (
    build_memory_inspector_card,
    build_memory_inspector_text,
)
from g3lobster.chat.memory_inspector import (
    build_memory_response,
    detect_memory_query,
)
from g3lobster.memory.manager import MemoryManager


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


class TestDetectMemoryQuery:
    """Positive and negative cases for detect_memory_query."""

    @pytest.mark.parametrize(
        "text",
        [
            "what do you remember about me?",
            "What do you remember?",
            "WHAT DO YOU REMEMBER ABOUT ME",
            "Hey, what do you remember about me",
            "what procedures have you learned?",
            "What procedures did you learn so far?",
            "show my preferences",
            "show preferences",
            "show my memory",
            "show memories",
            "my memory",
            "your memory",
            "what do you know about me?",
            "What do you know about me",
            "memory inspector",
        ],
    )
    def test_positive_matches(self, text: str) -> None:
        assert detect_memory_query(text) == "memory_query"

    @pytest.mark.parametrize(
        "text",
        [
            "hello",
            "how are you?",
            "please summarize yesterday",
            "search the web for memory foam mattresses",
            "what is the capital of France?",
            "",
            "   ",
            "remember to buy milk",
            "I prefer dark mode",
            "/memory",  # slash commands handled separately
        ],
    )
    def test_negative_matches(self, text: str) -> None:
        assert detect_memory_query(text) is None


# ---------------------------------------------------------------------------
# Card payload structure
# ---------------------------------------------------------------------------


class TestBuildMemoryInspectorCard:
    def test_card_has_correct_structure(self) -> None:
        result = build_memory_inspector_card(
            agent_name="Luna",
            agent_emoji="🦀",
            preferences=["Use dark mode"],
            procedures=[
                {
                    "title": "Deploy Flow",
                    "trigger": "deploy",
                    "steps": ["build", "test", "push"],
                    "weight": 5.0,
                    "status": "usable",
                }
            ],
            daily_notes=["2025-03-10: Had a good session"],
            stats={
                "total_sessions": 10,
                "total_messages": 42,
                "memory_bytes": 1024,
                "daily_notes": 5,
                "procedures_count": 1,
            },
        )

        assert "cardsV2" in result
        cards = result["cardsV2"]
        assert len(cards) == 1
        card = cards[0]["card"]
        assert "header" in card
        assert "Luna" in card["header"]["title"]
        assert len(card["sections"]) == 4  # prefs, procedures, context, stats

    def test_empty_data_shows_placeholders(self) -> None:
        result = build_memory_inspector_card(
            agent_name="Bot",
            agent_emoji="🤖",
            preferences=[],
            procedures=[],
            daily_notes=[],
            stats={},
        )

        card = result["cardsV2"][0]["card"]
        sections = card["sections"]
        # Each section should have a placeholder widget
        for section in sections:
            assert len(section["widgets"]) >= 1


class TestBuildMemoryInspectorText:
    def test_text_includes_all_sections(self) -> None:
        text = build_memory_inspector_text(
            agent_name="Luna",
            agent_emoji="🦀",
            preferences=["Use dark mode", "Respond briefly"],
            procedures=[
                {
                    "title": "Deploy Flow",
                    "weight": 5.0,
                    "status": "usable",
                }
            ],
            daily_notes=["2025-03-10: Summary"],
            stats={
                "total_sessions": 3,
                "total_messages": 20,
                "memory_bytes": 512,
                "daily_notes": 2,
                "procedures_count": 1,
            },
        )

        assert "User Preferences" in text
        assert "dark mode" in text
        assert "Learned Procedures" in text
        assert "Deploy Flow" in text
        assert "Recent Context" in text
        assert "Agent Stats" in text
        assert "Sessions: 3" in text

    def test_text_with_empty_data(self) -> None:
        text = build_memory_inspector_text(
            agent_name="Bot",
            agent_emoji="🤖",
            preferences=[],
            procedures=[],
            daily_notes=[],
            stats={},
        )

        assert "No preferences stored" in text
        assert "No procedures learned" in text
        assert "No daily notes" in text


# ---------------------------------------------------------------------------
# Data gathering (build_memory_response)
# ---------------------------------------------------------------------------


class TestBuildMemoryResponse:
    def test_builds_card_response(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "agent_data"))
        mm.append_tagged_memory("user preference", "prefers dark mode")

        result = build_memory_response(
            agent_name="Luna",
            agent_emoji="🦀",
            agent_id="luna",
            memory_manager=mm,
            use_cards=True,
        )

        assert "cardsV2" in result

    def test_builds_text_response(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "agent_data"))
        mm.append_tagged_memory("user preference", "prefers dark mode")

        result = build_memory_response(
            agent_name="Luna",
            agent_emoji="🦀",
            agent_id="luna",
            memory_manager=mm,
            use_cards=False,
        )

        assert "text" in result
        assert "dark mode" in result["text"]

    def test_stats_are_gathered(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "agent_data"))

        result = build_memory_response(
            agent_name="Luna",
            agent_emoji="🦀",
            agent_id="luna",
            memory_manager=mm,
            use_cards=False,
        )

        assert "Sessions: 0" in result["text"]


# ---------------------------------------------------------------------------
# delete_tagged_memory
# ---------------------------------------------------------------------------


class TestDeleteTaggedMemory:
    def test_delete_first_entry(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"))
        mm.append_tagged_memory("user preference", "dark mode")
        mm.append_tagged_memory("user preference", "brief replies")

        assert mm.delete_tagged_memory("user preference", 0)
        remaining = mm.get_memories_by_tag("user preference")
        assert len(remaining) == 1
        assert "brief replies" in remaining[0]

    def test_delete_second_entry(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"))
        mm.append_tagged_memory("user preference", "dark mode")
        mm.append_tagged_memory("user preference", "brief replies")

        assert mm.delete_tagged_memory("user preference", 1)
        remaining = mm.get_memories_by_tag("user preference")
        assert len(remaining) == 1
        assert "dark mode" in remaining[0]

    def test_delete_nonexistent_index(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"))
        mm.append_tagged_memory("user preference", "dark mode")

        assert not mm.delete_tagged_memory("user preference", 5)

    def test_delete_nonexistent_tag(self, tmp_path) -> None:
        mm = MemoryManager(data_dir=str(tmp_path / "data"))
        mm.append_tagged_memory("user preference", "dark mode")

        assert not mm.delete_tagged_memory("nonexistent", 0)


# ---------------------------------------------------------------------------
# /memory and /forget commands
# ---------------------------------------------------------------------------


class TestMemorySlashCommands:
    def test_detect_memory_command(self) -> None:
        from g3lobster.chat.commands import detect_command

        result = detect_command("/memory")
        assert result is not None
        assert result[0] == "memory"

    def test_detect_forget_command(self) -> None:
        from g3lobster.chat.commands import detect_command

        result = detect_command("/forget preference 0")
        assert result is not None
        assert result[0] == "forget"
        assert "preference 0" in result[1]

    def test_handle_memory_returns_text(self, tmp_path) -> None:
        from unittest.mock import MagicMock

        from g3lobster.chat.commands import handle

        mm = MemoryManager(data_dir=str(tmp_path / "data"))
        cron_store = MagicMock()
        persona = MagicMock()
        persona.name = "Luna"
        persona.emoji = "🦀"

        result = handle(
            "/memory", "luna", cron_store,
            memory_manager=mm, persona=persona,
        )
        assert result is not None
        assert "Memory Inspector" in result

    def test_handle_forget_preference(self, tmp_path) -> None:
        from unittest.mock import MagicMock

        from g3lobster.chat.commands import handle

        mm = MemoryManager(data_dir=str(tmp_path / "data"))
        mm.append_tagged_memory("user preference", "dark mode")

        cron_store = MagicMock()
        result = handle(
            "/forget preference 0", "luna", cron_store,
            memory_manager=mm,
        )
        assert result is not None
        assert "Forgot preference" in result

        # Verify it was deleted
        remaining = mm.get_memories_by_tag("user preference")
        assert len(remaining) == 0

    def test_handle_forget_bad_args(self, tmp_path) -> None:
        from unittest.mock import MagicMock

        from g3lobster.chat.commands import handle

        mm = MemoryManager(data_dir=str(tmp_path / "data"))
        cron_store = MagicMock()
        result = handle("/forget", "luna", cron_store, memory_manager=mm)
        assert result is not None
        assert "Usage" in result
