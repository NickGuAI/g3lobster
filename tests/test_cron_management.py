"""Tests for cron task management API extensions."""

from datetime import datetime, timezone

import pytest

from g3lobster.cron.manager import CronManager
from g3lobster.cron.store import CronRunRecord, CronStore
from g3lobster.tasks.types import TaskStatus


class _DummyRuntime:
    async def assign(self, task):
        task.status = TaskStatus.COMPLETED
        task.result = "ok"
        return task


class _DummyRegistry:
    def __init__(self) -> None:
        self._runtime = _DummyRuntime()

    def get_agent(self, _agent_id):
        return self._runtime

    async def start_agent(self, _agent_id):
        return True


def test_record_and_get_history(tmp_path):
    store = CronStore(str(tmp_path / "data"))
    agent_id = "test-agent"
    (tmp_path / "data" / agent_id).mkdir(parents=True)

    record = CronRunRecord(
        task_id="task-1",
        fired_at=datetime.now(tz=timezone.utc).isoformat(),
        status="completed",
        duration_s=1.5,
        result_preview="done",
    )
    store.record_run(agent_id, record)

    history = store.get_history(agent_id, "task-1")
    assert len(history) == 1
    assert history[0]["status"] == "completed"
    assert history[0]["duration_s"] == 1.5


def test_history_ring_buffer(tmp_path):
    store = CronStore(str(tmp_path / "data"))
    agent_id = "test-agent"
    (tmp_path / "data" / agent_id).mkdir(parents=True)

    for i in range(25):
        store.record_run(agent_id, CronRunRecord(
            task_id="task-1",
            fired_at=f"2026-01-{i+1:02d}T00:00:00+00:00",
            status="completed",
            duration_s=float(i),
            result_preview=f"run {i}",
        ))

    history = store.get_history(agent_id, "task-1")
    assert len(history) == 20  # ring buffer limit
    assert history[0]["result_preview"] == "run 5"  # oldest kept


def test_validate_valid_cron():
    """Test CronTrigger validation (requires apscheduler)."""
    try:
        from apscheduler.triggers.cron import CronTrigger
        trigger = CronTrigger.from_crontab("0 9 * * 1-5")
        assert trigger is not None
    except ImportError:
        pytest.skip("apscheduler not installed")


def test_validate_invalid_cron():
    """Invalid cron should raise."""
    try:
        from apscheduler.triggers.cron import CronTrigger
        with pytest.raises(ValueError):
            CronTrigger.from_crontab("invalid cron expression")
    except ImportError:
        pytest.skip("apscheduler not installed")


def test_cron_manager_enforces_max_jobs_per_agent(tmp_path):
    store = CronStore(str(tmp_path / "data"))
    manager = CronManager(
        cron_store=store,
        registry=_DummyRegistry(),
        max_jobs_per_agent=2,
    )
    agent_id = "test-agent"

    manager.create_task(
        agent_id=agent_id,
        schedule="0 9 * * *",
        instruction="first",
        enforce_agent_guardrails=True,
        actor_agent_id=agent_id,
        source="mcp",
    )
    manager.create_task(
        agent_id=agent_id,
        schedule="0 10 * * *",
        instruction="second",
        enforce_agent_guardrails=True,
        actor_agent_id=agent_id,
        source="mcp",
    )

    with pytest.raises(ValueError, match="Maximum cron jobs per agent exceeded"):
        manager.create_task(
            agent_id=agent_id,
            schedule="0 11 * * *",
            instruction="third",
            enforce_agent_guardrails=True,
            actor_agent_id=agent_id,
            source="mcp",
        )


def test_cron_manager_enforces_instruction_length(tmp_path):
    store = CronStore(str(tmp_path / "data"))
    manager = CronManager(cron_store=store, registry=_DummyRegistry())
    agent_id = "test-agent"

    with pytest.raises(ValueError, match="Instruction must be non-empty"):
        manager.create_task(
            agent_id=agent_id,
            schedule="0 9 * * *",
            instruction="   ",
            enforce_agent_guardrails=True,
            actor_agent_id=agent_id,
            source="mcp",
        )

    with pytest.raises(ValueError, match="2000 characters or fewer"):
        manager.create_task(
            agent_id=agent_id,
            schedule="0 9 * * *",
            instruction="x" * 2001,
            enforce_agent_guardrails=True,
            actor_agent_id=agent_id,
            source="mcp",
        )


def test_cron_manager_rejects_sub_minute_schedule(tmp_path):
    store = CronStore(str(tmp_path / "data"))
    manager = CronManager(cron_store=store, registry=_DummyRegistry())

    with pytest.raises(ValueError, match="Sub-minute cron schedules are not allowed"):
        manager.create_task(
            agent_id="test-agent",
            schedule="*/30 * * * * *",
            instruction="ping",
            enforce_agent_guardrails=True,
            actor_agent_id="test-agent",
            source="mcp",
        )


@pytest.mark.asyncio
async def test_cron_manager_audit_logs_agent_initiated_changes(tmp_path, caplog):
    store = CronStore(str(tmp_path / "data"))
    manager = CronManager(cron_store=store, registry=_DummyRegistry())
    agent_id = "test-agent"

    with caplog.at_level("INFO"):
        task = manager.create_task(
            agent_id=agent_id,
            schedule="0 9 * * *",
            instruction="ping",
            enforce_agent_guardrails=True,
            actor_agent_id=agent_id,
            source="mcp",
        )
        manager.update_task(
            agent_id=agent_id,
            task_id=task.id,
            instruction="pong",
            enforce_agent_guardrails=True,
            actor_agent_id=agent_id,
            source="mcp",
        )
        await manager.run_task(
            agent_id=agent_id,
            task_id=task.id,
            actor_agent_id=agent_id,
            source="mcp",
        )
        manager.delete_task(
            agent_id=agent_id,
            task_id=task.id,
            actor_agent_id=agent_id,
            source="mcp",
        )

    assert "cron_audit action=create" in caplog.text
    assert "cron_audit action=update" in caplog.text
    assert "cron_audit action=run" in caplog.text
    assert "cron_audit action=delete" in caplog.text
