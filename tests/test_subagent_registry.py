"""Tests for SubagentRegistry persistent run tracking."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from g3lobster.agents.subagent_registry import RunStatus, SubagentRegistry, SubagentRun


@pytest.fixture
def registry(tmp_path: Path) -> SubagentRegistry:
    return SubagentRegistry(tmp_path)


def test_register_and_complete_run(registry: SubagentRegistry):
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="build a dashboard",
        parent_session_id="session-1",
    )
    assert run.status == RunStatus.REGISTERED
    assert run.parent_agent_id == "athena"
    assert run.child_agent_id == "hephaestus"
    assert run.task == "build a dashboard"
    assert run.run_id

    registry.complete_run(run.run_id, "dashboard built successfully")

    updated = registry.get_run(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.COMPLETED
    assert updated.result == "dashboard built successfully"
    assert updated.completed_at is not None


def test_fail_run(registry: SubagentRegistry):
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="impossible task",
        parent_session_id="session-1",
    )

    registry.fail_run(run.run_id, "child agent crashed")

    updated = registry.get_run(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error == "child agent crashed"
    assert updated.completed_at is not None


def test_timeout_detection(registry: SubagentRegistry):
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="slow task",
        parent_session_id="session-1",
        timeout_s=0.0,  # Immediate timeout
    )
    # Mark as running so it can be detected as timed out
    run.status = RunStatus.RUNNING
    run.created_at = time.time() - 10  # Created 10s ago
    registry._save_to_disk()

    timed_out = registry.check_timeouts()
    assert len(timed_out) == 1
    assert timed_out[0].run_id == run.run_id
    assert timed_out[0].status == RunStatus.TIMED_OUT
    assert "Timed out" in (timed_out[0].error or "")


def test_timeout_skips_non_running(registry: SubagentRegistry):
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="registered task",
        parent_session_id="session-1",
        timeout_s=0.0,
    )
    # REGISTERED status should not be timed out
    run.created_at = time.time() - 10
    registry._save_to_disk()

    timed_out = registry.check_timeouts()
    assert len(timed_out) == 0


def test_disk_persistence_and_reload(tmp_path: Path):
    registry1 = SubagentRegistry(tmp_path)
    run = registry1.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="persist this task",
        parent_session_id="session-1",
    )
    registry1.complete_run(run.run_id, "persisted result")

    # Create a new registry from the same directory
    registry2 = SubagentRegistry(tmp_path)
    loaded_run = registry2.get_run(run.run_id)
    assert loaded_run is not None
    assert loaded_run.status == RunStatus.COMPLETED
    assert loaded_run.result == "persisted result"
    assert loaded_run.parent_agent_id == "athena"
    assert loaded_run.child_agent_id == "hephaestus"


def test_list_runs_by_parent(registry: SubagentRegistry):
    registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="task 1",
        parent_session_id="session-1",
    )
    registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hermes",
        task="task 2",
        parent_session_id="session-1",
    )
    registry.register_run(
        parent_agent_id="hermes",
        child_agent_id="hephaestus",
        task="task 3",
        parent_session_id="session-2",
    )

    all_runs = registry.list_runs()
    assert len(all_runs) == 3

    athena_runs = registry.list_runs(parent_agent_id="athena")
    assert len(athena_runs) == 2
    assert all(r.parent_agent_id == "athena" for r in athena_runs)

    hermes_runs = registry.list_runs(parent_agent_id="hermes")
    assert len(hermes_runs) == 1
    assert hermes_runs[0].parent_agent_id == "hermes"


def test_list_runs_sorted_newest_first(registry: SubagentRegistry):
    run1 = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="first task",
        parent_session_id="session-1",
    )
    run1.created_at = 1000.0
    registry._save_to_disk()

    run2 = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hermes",
        task="second task",
        parent_session_id="session-1",
    )
    run2.created_at = 2000.0
    registry._save_to_disk()

    runs = registry.list_runs()
    assert len(runs) == 2
    assert runs[0].run_id == run2.run_id  # Newest first
    assert runs[1].run_id == run1.run_id


def test_get_run_not_found(registry: SubagentRegistry):
    assert registry.get_run("nonexistent-id") is None


def test_complete_run_nonexistent(registry: SubagentRegistry):
    # Should not raise
    registry.complete_run("nonexistent-id", "result")


def test_fail_run_nonexistent(registry: SubagentRegistry):
    # Should not raise
    registry.fail_run("nonexistent-id", "error")


def test_session_id_format(registry: SubagentRegistry):
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="test session format",
        parent_session_id="session-1",
    )
    assert run.session_id.startswith("delegation-")


def test_default_timeout(registry: SubagentRegistry):
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="test default timeout",
        parent_session_id="session-1",
    )
    assert run.timeout_s == 300.0


def test_custom_timeout(registry: SubagentRegistry):
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="test custom timeout",
        parent_session_id="session-1",
        timeout_s=60.0,
    )
    assert run.timeout_s == 60.0


def test_load_from_corrupt_file(tmp_path: Path):
    registry_file = tmp_path / ".subagent_runs.json"
    registry_file.write_text("not valid json!!!", encoding="utf-8")

    # Should not raise, starts fresh
    registry = SubagentRegistry(tmp_path)
    assert registry.list_runs() == []
