"""Tests for the cron store and API routes."""

from __future__ import annotations

from pathlib import Path

from g3lobster.cron.store import CronRunRecord, CronStore, CronTask


def test_cron_store_crud(tmp_path: Path):
    store = CronStore(data_dir=str(tmp_path))
    agent_id = "test-agent"

    # Initially empty
    assert store.list_tasks(agent_id) == []

    # Add a task
    task = store.add_task(agent_id, "0 9 * * *", "check email")
    assert task.schedule == "0 9 * * *"
    assert task.instruction == "check email"
    assert task.enabled is True

    # List tasks
    tasks = store.list_tasks(agent_id)
    assert len(tasks) == 1
    assert tasks[0].id == task.id

    # Get task
    fetched = store.get_task(agent_id, task.id)
    assert fetched is not None
    assert fetched.instruction == "check email"

    # Update task
    updated = store.update_task(agent_id, task.id, schedule="*/5 * * * *", instruction="check slack")
    assert updated is not None
    assert updated.schedule == "*/5 * * * *"
    assert updated.instruction == "check slack"

    # Update non-existent
    assert store.update_task(agent_id, "no-such-id") is None

    # Delete task
    assert store.delete_task(agent_id, task.id) is True
    assert store.delete_task(agent_id, task.id) is False
    assert store.list_tasks(agent_id) == []


def test_cron_store_list_all_enabled(tmp_path: Path):
    store = CronStore(data_dir=str(tmp_path))
    store.add_task("agent-a", "0 * * * *", "task a")
    task_b = store.add_task("agent-b", "0 12 * * *", "task b")
    store.update_task("agent-b", task_b.id, enabled=False)

    enabled = store.list_all_enabled()
    assert len(enabled) == 1
    assert enabled[0].agent_id == "agent-a"


def test_cron_store_run_history(tmp_path: Path):
    store = CronStore(data_dir=str(tmp_path))
    agent_id = "test-agent"
    task = store.add_task(agent_id, "* * * * *", "ping")

    # Record a run
    store.record_run(agent_id, CronRunRecord(
        task_id=task.id,
        fired_at="2026-01-01T00:00:00Z",
        status="completed",
        duration_s=1.5,
        result_preview="pong",
    ))

    history = store.get_history(agent_id, task.id)
    assert len(history) == 1
    assert history[0]["status"] == "completed"
    assert history[0]["result_preview"] == "pong"


def test_cron_store_invalid_agent_id(tmp_path: Path):
    store = CronStore(data_dir=str(tmp_path))
    import pytest
    with pytest.raises(ValueError, match="Invalid agent_id"):
        store.list_tasks("../escape")


def test_cron_api_routes(tmp_path: Path):
    """Test cron REST endpoints via TestClient."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from g3lobster.api.routes_cron import router
    from g3lobster.cron.store import CronStore

    app = FastAPI()
    app.include_router(router)
    store = CronStore(data_dir=str(tmp_path))
    app.state.cron_store = store
    app.state.cron_manager = None

    # Ensure agent dir exists for the store
    (tmp_path / "my-agent").mkdir()

    with TestClient(app) as client:
        # Create
        resp = client.post("/agents/my-agent/crons", json={
            "schedule": "0 9 * * *",
            "instruction": "daily standup",
            "enabled": False,
            "dm_target": "nick@example.com",
        })
        assert resp.status_code == 201
        task_id = resp.json()["id"]
        assert resp.json()["enabled"] is False
        assert resp.json()["dm_target"] == "nick@example.com"

        # List
        resp = client.get("/agents/my-agent/crons")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        # Update
        resp = client.put(f"/agents/my-agent/crons/{task_id}", json={
            "schedule": "0 10 * * *",
        })
        assert resp.status_code == 200
        assert resp.json()["schedule"] == "0 10 * * *"

        # Update non-existent
        resp = client.put("/agents/my-agent/crons/bad-id", json={"enabled": False})
        assert resp.status_code == 404

        # History (empty)
        resp = client.get(f"/agents/my-agent/crons/{task_id}/history")
        assert resp.status_code == 200
        assert resp.json()["runs"] == []

        # Delete
        resp = client.delete(f"/agents/my-agent/crons/{task_id}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}

        # Delete non-existent
        resp = client.delete(f"/agents/my-agent/crons/{task_id}")
        assert resp.status_code == 404

        # List all enabled
        resp = client.get("/agents/_cron/all")
        assert resp.status_code == 200
        assert resp.json() == []


def test_cron_api_prefers_manager_methods(tmp_path: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from g3lobster.api.routes_cron import router
    from g3lobster.cron.store import CronStore

    class _Manager:
        def __init__(self):
            self.calls = []

        def list_tasks(self, agent_id):
            self.calls.append(("list", agent_id))
            return []

        def create_task(self, agent_id, schedule, instruction, enabled, dm_target, **kwargs):
            self.calls.append(("create", agent_id, schedule, instruction, enabled, dm_target, kwargs.get("source")))
            return CronTask(
                id="task-1",
                agent_id=agent_id,
                schedule=schedule,
                instruction=instruction,
                enabled=enabled,
                dm_target=dm_target,
            )

        async def run_task(self, agent_id, task_id, **kwargs):
            self.calls.append(("run", agent_id, task_id, kwargs.get("source")))
            return {
                "task_id": task_id,
                "status": "completed",
                "duration_s": 0.1,
                "result_preview": "ok",
            }

    app = FastAPI()
    app.include_router(router)
    app.state.cron_store = CronStore(data_dir=str(tmp_path))
    manager = _Manager()
    app.state.cron_manager = manager

    with TestClient(app) as client:
        list_resp = client.get("/agents/my-agent/crons")
        assert list_resp.status_code == 200
        assert list_resp.json() == []

        create_resp = client.post("/agents/my-agent/crons", json={
            "schedule": "0 9 * * *",
            "instruction": "ping",
            "enabled": True,
            "dm_target": "nick@example.com",
        })
        assert create_resp.status_code == 201
        assert create_resp.json()["id"] == "task-1"

        run_resp = client.post("/agents/my-agent/crons/task-1/run")
        assert run_resp.status_code == 200
        assert run_resp.json()["status"] == "completed"

    assert ("list", "my-agent") in manager.calls
    assert ("run", "my-agent", "task-1", "api") in manager.calls


def test_cron_api_marks_mcp_agent_requests(tmp_path: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from g3lobster.api.routes_cron import router
    from g3lobster.cron.store import CronStore

    class _Manager:
        def __init__(self):
            self.calls = []

        def create_task(self, **kwargs):
            self.calls.append(("create", kwargs))
            return CronTask(
                id="task-1",
                agent_id=kwargs["agent_id"],
                schedule=kwargs["schedule"],
                instruction=kwargs["instruction"],
                enabled=kwargs["enabled"],
                dm_target=kwargs.get("dm_target"),
            )

        async def run_task(self, **kwargs):
            self.calls.append(("run", kwargs))
            return {
                "task_id": kwargs["task_id"],
                "status": "completed",
                "duration_s": 0.1,
                "result_preview": "ok",
            }

    app = FastAPI()
    app.include_router(router)
    app.state.cron_store = CronStore(data_dir=str(tmp_path))
    manager = _Manager()
    app.state.cron_manager = manager

    headers = {
        "X-G3LOBSTER-AGENT-SOURCE": "mcp",
        "X-G3LOBSTER-ACTOR-AGENT-ID": "my-agent",
    }

    with TestClient(app) as client:
        create_resp = client.post(
            "/agents/my-agent/crons",
            json={"schedule": "0 9 * * *", "instruction": "ping"},
            headers=headers,
        )
        assert create_resp.status_code == 201

        run_resp = client.post(
            "/agents/my-agent/crons/task-1/run",
            headers=headers,
        )
        assert run_resp.status_code == 200

    create_kwargs = manager.calls[0][1]
    run_kwargs = manager.calls[1][1]
    assert create_kwargs["enforce_agent_guardrails"] is True
    assert create_kwargs["actor_agent_id"] == "my-agent"
    assert create_kwargs["source"] == "mcp"
    assert run_kwargs["actor_agent_id"] == "my-agent"
    assert run_kwargs["source"] == "mcp"
