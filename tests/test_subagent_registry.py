"""Tests for SubagentRegistry and SubagentRun data model."""

from __future__ import annotations

import time

import pytest

from g3lobster.agents.subagent_registry import RunStatus, SubagentRegistry, SubagentRun


def test_register_and_complete_run(tmp_path):
    registry = SubagentRegistry(tmp_path)
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
    assert run.parent_session_id == "session-1"
    assert run.session_id.startswith("delegation-")
    assert run.result is None

    registry.complete_run(run.run_id, "dashboard built")
    completed = registry.get_run(run.run_id)
    assert completed.status == RunStatus.COMPLETED
    assert completed.result == "dashboard built"
    assert completed.completed_at is not None


def test_fail_run(tmp_path):
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="build something",
        parent_session_id="session-1",
    )
    registry.fail_run(run.run_id, "agent crashed")
    failed = registry.get_run(run.run_id)
    assert failed.status == RunStatus.FAILED
    assert failed.error == "agent crashed"
    assert failed.completed_at is not None


def test_circular_delegation_rejected(tmp_path):
    registry = SubagentRegistry(tmp_path)
    with pytest.raises(ValueError, match="Circular delegation"):
        registry.register_run(
            parent_agent_id="athena",
            child_agent_id="athena",
            task="self-delegation",
            parent_session_id="session-1",
        )


def test_timeout_detection(tmp_path):
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="slow task",
        parent_session_id="session-1",
        timeout_s=0.0,  # immediate timeout
    )
    # Must be RUNNING to be timed out
    run.status = RunStatus.RUNNING
    run.created_at = time.time() - 10  # created 10s ago
    registry._save_to_disk()

    timed_out = registry.check_timeouts()
    assert len(timed_out) == 1
    assert timed_out[0].run_id == run.run_id
    assert timed_out[0].status == RunStatus.TIMED_OUT
    assert "Timed out" in timed_out[0].error


def test_timeout_skips_non_running(tmp_path):
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="already done",
        parent_session_id="session-1",
        timeout_s=0.0,
    )
    # REGISTERED status should not be timed out
    run.created_at = time.time() - 10
    timed_out = registry.check_timeouts()
    assert len(timed_out) == 0


def test_disk_persistence_and_reload(tmp_path):
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="persist me",
        parent_session_id="session-1",
    )
    registry.complete_run(run.run_id, "persisted result")

    # Reload from disk
    registry2 = SubagentRegistry(tmp_path)
    reloaded = registry2.get_run(run.run_id)
    assert reloaded is not None
    assert reloaded.status == RunStatus.COMPLETED
    assert reloaded.result == "persisted result"
    assert reloaded.parent_agent_id == "athena"
    assert reloaded.child_agent_id == "hephaestus"


def test_list_runs_by_parent(tmp_path):
    registry = SubagentRegistry(tmp_path)
    registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="task 1",
        parent_session_id="s1",
    )
    registry.register_run(
        parent_agent_id="athena",
        child_agent_id="apollo",
        task="task 2",
        parent_session_id="s2",
    )
    registry.register_run(
        parent_agent_id="apollo",
        child_agent_id="hephaestus",
        task="task 3",
        parent_session_id="s3",
    )

    all_runs = registry.list_runs()
    assert len(all_runs) == 3

    athena_runs = registry.list_runs(parent_agent_id="athena")
    assert len(athena_runs) == 2
    assert all(r.parent_agent_id == "athena" for r in athena_runs)

    apollo_runs = registry.list_runs(parent_agent_id="apollo")
    assert len(apollo_runs) == 1
    assert apollo_runs[0].task == "task 3"


def test_list_runs_sorted_by_created_at(tmp_path):
    registry = SubagentRegistry(tmp_path)
    run1 = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="first",
        parent_session_id="s1",
    )
    run2 = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="apollo",
        task="second",
        parent_session_id="s2",
    )
    # Most recent first
    runs = registry.list_runs()
    assert runs[0].run_id == run2.run_id
    assert runs[1].run_id == run1.run_id


def test_get_nonexistent_run(tmp_path):
    registry = SubagentRegistry(tmp_path)
    assert registry.get_run("nonexistent-id") is None


def test_complete_nonexistent_run(tmp_path):
    registry = SubagentRegistry(tmp_path)
    # Should not raise, just no-op
    registry.complete_run("nonexistent-id", "result")
    registry.fail_run("nonexistent-id", "error")


def test_default_timeout(tmp_path):
    registry = SubagentRegistry(tmp_path)
    run = registry.register_run(
        parent_agent_id="athena",
        child_agent_id="hephaestus",
        task="default timeout",
        parent_session_id="session-1",
    )
    assert run.timeout_s == 300.0
