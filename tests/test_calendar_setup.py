"""Tests for calendar conflict-resolver setup."""

from __future__ import annotations

import pytest

from g3lobster.calendar.setup import (
    CONFLICT_RESOLVER_AGENT_ID,
    CONFLICT_RESOLVER_SOUL,
    CONFLICT_SCAN_SCHEDULE,
    setup_conflict_resolver,
)


def test_setup_creates_persona(tmp_path):
    data_dir = str(tmp_path / "data")
    result = setup_conflict_resolver(data_dir)
    assert result["agent_id"] == CONFLICT_RESOLVER_AGENT_ID
    assert result["persona_created"] is True

    # Verify persona files exist
    agent_dir = tmp_path / "data" / "agents" / CONFLICT_RESOLVER_AGENT_ID
    assert (agent_dir / "agent.json").exists()
    assert (agent_dir / "SOUL.md").exists()

    # Verify SOUL.md content
    soul_content = (agent_dir / "SOUL.md").read_text(encoding="utf-8")
    assert "Conflict Resolver Agent" in soul_content
    assert "check_conflicts" in soul_content


def test_setup_idempotent(tmp_path):
    data_dir = str(tmp_path / "data")
    result1 = setup_conflict_resolver(data_dir)
    assert result1["persona_created"] is True

    result2 = setup_conflict_resolver(data_dir)
    assert result2["persona_created"] is False


def test_setup_with_cron_store(tmp_path):
    from g3lobster.cron.store import CronStore

    data_dir = str(tmp_path / "data")
    cron_store = CronStore(data_dir)
    # Create agent dir for cron store
    (tmp_path / "data" / "agents" / CONFLICT_RESOLVER_AGENT_ID).mkdir(parents=True, exist_ok=True)

    result = setup_conflict_resolver(data_dir, cron_store)
    assert result["persona_created"] is True
    assert result["cron_task_created"] is True

    # Verify cron task exists
    tasks = cron_store.list_tasks(CONFLICT_RESOLVER_AGENT_ID)
    assert len(tasks) == 1
    assert tasks[0].schedule == CONFLICT_SCAN_SCHEDULE


def test_setup_cron_idempotent(tmp_path):
    from g3lobster.cron.store import CronStore

    data_dir = str(tmp_path / "data")
    cron_store = CronStore(data_dir)
    (tmp_path / "data" / "agents" / CONFLICT_RESOLVER_AGENT_ID).mkdir(parents=True, exist_ok=True)

    setup_conflict_resolver(data_dir, cron_store)
    result2 = setup_conflict_resolver(data_dir, cron_store)
    assert result2["cron_task_created"] is False

    tasks = cron_store.list_tasks(CONFLICT_RESOLVER_AGENT_ID)
    assert len(tasks) == 1


def test_soul_contains_chat_interaction_flow():
    """Verify the SOUL.md content covers the Chat interaction flow."""
    assert "Handling User Replies" in CONFLICT_RESOLVER_SOUL
    assert "Multi-Person Scheduling" in CONFLICT_RESOLVER_SOUL
    assert "NEVER modify or delete events without explicit user approval" in CONFLICT_RESOLVER_SOUL
    assert "reschedule_event" in CONFLICT_RESOLVER_SOUL
    assert "create_event" in CONFLICT_RESOLVER_SOUL
    assert "find_meeting_slots" in CONFLICT_RESOLVER_SOUL
