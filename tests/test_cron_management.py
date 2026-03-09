"""Tests for cron task management API extensions."""

from datetime import datetime, timezone

import pytest

from g3lobster.cron.store import CronRunRecord, CronStore


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
