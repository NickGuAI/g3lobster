"""Tests for standup file-based storage."""

from datetime import datetime, timezone

import pytest

from g3lobster.standup.store import StandupConfig, StandupEntry, StandupStore


def _make_config(agent_id: str = "agent-1", **overrides) -> StandupConfig:
    defaults = dict(
        agent_id=agent_id,
        team_members=[{"user_id": "u1", "display_name": "Alice"}],
        prompt_schedule="0 9 * * 1-5",
        summary_schedule="0 17 * * 1-5",
        summary_space_id="spaces/ABC",
        enabled=True,
    )
    defaults.update(overrides)
    return StandupConfig(**defaults)


def _make_entry(user_id: str = "u1", date: str = "2026-03-10", **overrides) -> StandupEntry:
    defaults = dict(
        user_id=user_id,
        display_name="Alice",
        date=date,
        response="Did X yesterday, doing Y today.",
        blockers=["waiting on API access"],
    )
    defaults.update(overrides)
    return StandupEntry(**defaults)


def test_save_and_get_config(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    cfg = _make_config()
    store.save_config("agent-1", cfg)

    loaded = store.get_config("agent-1")
    assert loaded is not None
    assert loaded.agent_id == "agent-1"
    assert loaded.team_members == [{"user_id": "u1", "display_name": "Alice"}]
    assert loaded.prompt_schedule == "0 9 * * 1-5"
    assert loaded.summary_space_id == "spaces/ABC"
    assert loaded.enabled is True


def test_get_config_missing(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    assert store.get_config("nonexistent") is None


def test_delete_config(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    store.save_config("agent-1", _make_config())
    assert store.get_config("agent-1") is not None

    assert store.delete_config("agent-1") is True
    assert store.get_config("agent-1") is None


def test_delete_config_missing(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    assert store.delete_config("nonexistent") is False


def test_add_and_get_entries(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    e1 = _make_entry(user_id="u1", date="2026-03-10")
    e2 = _make_entry(user_id="u2", date="2026-03-10", display_name="Bob", response="All good.")

    store.add_entry("agent-1", e1)
    store.add_entry("agent-1", e2)

    entries = store.get_entries("agent-1", "2026-03-10")
    assert len(entries) == 2
    assert entries[0].user_id == "u1"
    assert entries[1].user_id == "u2"
    assert entries[1].display_name == "Bob"


def test_entries_empty_date(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    assert store.get_entries("agent-1", "2026-03-10") == []


def test_get_entries_range(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    for day in ("2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12"):
        store.add_entry("agent-1", _make_entry(date=day))

    result = store.get_entries_range("agent-1", "2026-03-10", "2026-03-11")
    assert sorted(result.keys()) == ["2026-03-10", "2026-03-11"]
    assert len(result["2026-03-10"]) == 1
    assert len(result["2026-03-11"]) == 1

    # Dates outside the range must not appear
    assert "2026-03-09" not in result
    assert "2026-03-12" not in result


def test_list_configured_agents(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    store.save_config("alpha", _make_config(agent_id="alpha"))
    store.save_config("beta", _make_config(agent_id="beta"))
    store.save_config("gamma", _make_config(agent_id="gamma"))

    agents = sorted(store.list_configured_agents())
    assert agents == ["alpha", "beta", "gamma"]


def test_config_update_sets_updated_at(tmp_path):
    store = StandupStore(str(tmp_path / "data"))
    cfg = _make_config()
    original_updated = cfg.updated_at

    saved = store.save_config("agent-1", cfg)
    # save_config overwrites updated_at with a fresh timestamp
    assert saved.updated_at >= original_updated

    # Save again and confirm updated_at advances (or stays equal on fast machines)
    second_save = store.save_config("agent-1", saved)
    assert second_save.updated_at >= saved.updated_at


def test_atomic_write_survives_reread(tmp_path):
    data_dir = str(tmp_path / "data")
    store1 = StandupStore(data_dir)
    store1.save_config("agent-1", _make_config())
    store1.add_entry("agent-1", _make_entry(date="2026-03-10"))

    # Create a completely new store instance pointing at the same directory
    store2 = StandupStore(data_dir)

    cfg = store2.get_config("agent-1")
    assert cfg is not None
    assert cfg.agent_id == "agent-1"

    entries = store2.get_entries("agent-1", "2026-03-10")
    assert len(entries) == 1
    assert entries[0].user_id == "u1"
