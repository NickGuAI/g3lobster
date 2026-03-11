"""Unit tests for the standup orchestrator."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from g3lobster.standup.orchestrator import StandupOrchestrator
from g3lobster.standup.store import StandupConfig, StandupEntry, StandupStore


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_registry(agent_id: str = "agent-1") -> MagicMock:
    """Return a mock registry whose get_agent() provides a persona."""
    persona = MagicMock()
    persona.emoji = "\U0001f916"
    persona.name = "TestBot"

    runtime = MagicMock()
    runtime.persona = persona

    registry = MagicMock()
    registry.get_agent.return_value = runtime
    return registry


def _make_chat_bridge() -> AsyncMock:
    """Return an async-mock chat bridge with a send_message method."""
    bridge = AsyncMock()
    bridge.send_message = AsyncMock()
    return bridge


def _make_config(agent_id: str = "agent-1", **overrides) -> StandupConfig:
    defaults = dict(
        agent_id=agent_id,
        team_members=[
            {"user_id": "u1", "display_name": "Alice"},
            {"user_id": "u2", "display_name": "Bob"},
        ],
        summary_space_id="space-1",
    )
    defaults.update(overrides)
    return StandupConfig(**defaults)


# ------------------------------------------------------------------
# _extract_blockers
# ------------------------------------------------------------------


class TestExtractBlockers:
    def test_extract_blockers(self, tmp_path):
        store = StandupStore(str(tmp_path))
        orch = StandupOrchestrator(store, _make_registry())

        text = "I finished the API work. I'm blocked on the database migration. All tests pass."
        result = orch._extract_blockers(text)

        assert len(result) == 1
        assert "blocked" in result[0].lower()

    def test_extract_blockers_none(self, tmp_path):
        store = StandupStore(str(tmp_path))
        orch = StandupOrchestrator(store, _make_registry())

        text = "Everything is going well. Finished the feature. Tests pass."
        result = orch._extract_blockers(text)

        assert result == []


# ------------------------------------------------------------------
# _extract_action_items
# ------------------------------------------------------------------


class TestExtractActionItems:
    def test_extract_action_items(self, tmp_path):
        store = StandupStore(str(tmp_path))
        orch = StandupOrchestrator(store, _make_registry())

        text = "Yesterday I reviewed PRs. Today I will deploy the service. No blockers."
        result = orch._extract_action_items(text)

        assert len(result) == 1
        assert "will deploy" in result[0].lower()


# ------------------------------------------------------------------
# collect_response
# ------------------------------------------------------------------


class TestCollectResponse:
    def test_collect_response(self, tmp_path):
        store = StandupStore(str(tmp_path))
        config = _make_config()
        store.save_config("agent-1", config)
        orch = StandupOrchestrator(store, _make_registry())

        entry = orch.collect_response(
            agent_id="agent-1",
            user_id="u1",
            display_name="Alice",
            text="Did code review. Blocked on CI pipeline. Will fix tests.",
        )

        assert isinstance(entry, StandupEntry)
        assert entry.user_id == "u1"
        assert entry.display_name == "Alice"
        assert len(entry.blockers) == 1
        assert "Blocked" in entry.blockers[0]

        # Verify the entry was persisted
        entries = store.get_entries("agent-1", entry.date)
        assert len(entries) == 1
        assert entries[0].user_id == "u1"

    def test_collect_response_no_blockers(self, tmp_path):
        store = StandupStore(str(tmp_path))
        config = _make_config()
        store.save_config("agent-1", config)
        orch = StandupOrchestrator(store, _make_registry())

        entry = orch.collect_response(
            agent_id="agent-1",
            user_id="u2",
            display_name="Bob",
            text="Finished the feature. All tests pass.",
        )

        assert isinstance(entry, StandupEntry)
        assert entry.blockers == []


# ------------------------------------------------------------------
# is_standup_participant
# ------------------------------------------------------------------


class TestIsStandupParticipant:
    def test_is_standup_participant_true(self, tmp_path):
        store = StandupStore(str(tmp_path))
        config = _make_config()
        store.save_config("agent-1", config)
        orch = StandupOrchestrator(store, _make_registry())

        assert orch.is_standup_participant("agent-1", "u1") is True

    def test_is_standup_participant_false(self, tmp_path):
        store = StandupStore(str(tmp_path))
        config = _make_config()
        store.save_config("agent-1", config)
        orch = StandupOrchestrator(store, _make_registry())

        assert orch.is_standup_participant("agent-1", "unknown-user") is False

    def test_is_standup_participant_no_config(self, tmp_path):
        store = StandupStore(str(tmp_path))
        orch = StandupOrchestrator(store, _make_registry())

        assert orch.is_standup_participant("no-such-agent", "u1") is False


# ------------------------------------------------------------------
# generate_summary
# ------------------------------------------------------------------


class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_generate_summary_with_entries(self, tmp_path):
        store = StandupStore(str(tmp_path))
        config = _make_config()
        store.save_config("agent-1", config)
        bridge = _make_chat_bridge()
        orch = StandupOrchestrator(store, _make_registry(), chat_bridge=bridge)

        # Collect two responses
        orch.collect_response("agent-1", "u1", "Alice", "Did code review. Blocked on CI pipeline.")
        orch.collect_response("agent-1", "u2", "Bob", "Will deploy the service. No issues.")

        summary = await orch.generate_summary("agent-1")

        assert summary is not None
        assert "Standup Summary" in summary
        assert "Alice" in summary
        assert "Bob" in summary
        assert "Blockers" in summary
        assert "CI pipeline" in summary
        # No missing updates — both members responded
        assert "Missing Updates" not in summary
        bridge.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_summary_no_entries(self, tmp_path):
        store = StandupStore(str(tmp_path))
        config = _make_config()
        store.save_config("agent-1", config)
        bridge = _make_chat_bridge()
        orch = StandupOrchestrator(store, _make_registry(), chat_bridge=bridge)

        summary = await orch.generate_summary("agent-1")

        assert summary is not None
        assert "No updates received" in summary


# ------------------------------------------------------------------
# prompt_team
# ------------------------------------------------------------------


class TestPromptTeam:
    @pytest.mark.asyncio
    async def test_prompt_team_sends_messages(self, tmp_path):
        store = StandupStore(str(tmp_path))
        config = _make_config()
        store.save_config("agent-1", config)
        bridge = _make_chat_bridge()
        orch = StandupOrchestrator(store, _make_registry(), chat_bridge=bridge)

        await orch.prompt_team("agent-1")

        assert bridge.send_message.call_count == 2
        calls = [call.args[0] for call in bridge.send_message.call_args_list]
        assert any("Alice" in c for c in calls)
        assert any("Bob" in c for c in calls)
        assert all("TestBot" in c for c in calls)
