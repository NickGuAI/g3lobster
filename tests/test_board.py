"""Tests for unified task board storage and API routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from g3lobster.api.event_bus import EventBus
from g3lobster.api.routes_board import router as board_router
from g3lobster.api.routes_tasks import router as tasks_router
from g3lobster.board.store import BoardStore


def _build_board_app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(tasks_router)
    app.include_router(board_router)
    app.state.board_store = BoardStore(data_dir=str(tmp_path))
    app.state.sheets_sync = None
    app.state.event_bus = EventBus()
    return app


def test_board_store_crud_writes_per_agent_and_shared_files(tmp_path: Path) -> None:
    store = BoardStore(data_dir=str(tmp_path))

    created = store.insert(
        title="Fix login flow",
        type="bug",
        status="todo",
        priority="high",
        agent_id="agent-a",
        created_by="human",
        metadata={"component": "auth"},
    )
    assert created.title == "Fix login flow"
    assert created.priority == "high"
    assert created.created_by == "human"

    fetched = store.get_item(created.id)
    assert fetched is not None
    assert fetched.metadata["component"] == "auth"

    done = store.complete(created.id, result="patched")
    assert done is not None
    assert done.status == "done"
    assert done.result == "patched"

    per_agent_file = tmp_path / "agents" / "agent-a" / "task_board.json"
    shared_file = tmp_path / "task_board.json"
    assert per_agent_file.exists()
    assert shared_file.exists()

    payload = json.loads(shared_file.read_text(encoding="utf-8"))
    assert payload[0]["id"] == created.id
    assert payload[0]["status"] == "done"


def test_board_store_migrates_legacy_shared_entries(tmp_path: Path) -> None:
    legacy_file = tmp_path / "task_board.json"
    legacy_file.write_text(
        json.dumps(
            [
                {
                    "id": "legacy-1",
                    "title": "Daily triage",
                    "type": "chore",
                    "status": "running",
                    "agent_id": "concierge",
                    "metadata": {"priority": "critical"},
                }
            ]
        ),
        encoding="utf-8",
    )

    store = BoardStore(data_dir=str(tmp_path))
    items = store.list_items()
    assert len(items) == 1
    assert items[0].id == "legacy-1"
    assert items[0].status == "in_progress"
    assert items[0].priority == "critical"
    assert items[0].agent_id == "concierge"

    canonical_file = tmp_path / "agents" / "concierge" / "task_board.json"
    assert canonical_file.exists()


def test_board_store_filters_and_limit(tmp_path: Path) -> None:
    store = BoardStore(data_dir=str(tmp_path))
    store.insert(title="A", type="bug", status="todo", priority="high", agent_id="a", created_by="agent")
    store.insert(title="B", type="feature", status="in_progress", priority="normal", agent_id="b", created_by="human")
    store.insert(title="C", type="bug", status="done", priority="critical", agent_id="a", created_by="cron")

    assert len(store.list_items(type_filter="bug")) == 2
    assert len(store.list_items(status_filter="done")) == 1
    assert len(store.list_items(priority_filter="critical")) == 1
    assert len(store.list_items(created_by="agent")) == 1
    assert len(store.list_items(agent_id="a")) == 2
    assert len(store.list_items(limit=2)) == 2


def test_tasks_and_board_routes_unified_crud(tmp_path: Path) -> None:
    app = _build_board_app(tmp_path)

    with TestClient(app) as client:
        create = client.post(
            "/tasks",
            json={
                "title": "Review inbox patterns",
                "type": "research",
                "priority": "high",
                "status": "todo",
                "agent_id": "concierge",
                "created_by": "agent",
                "metadata": {"topic": "email"},
            },
        )
        assert create.status_code == 201
        created = create.json()
        task_id = created["id"]
        assert created["created_by"] == "agent"
        assert created["priority"] == "high"

        listing = client.get("/tasks", params={"agent_id": "concierge", "priority": "high"})
        assert listing.status_code == 200
        assert len(listing.json()) == 1

        update = client.put(f"/tasks/{task_id}", json={"status": "in_progress"})
        assert update.status_code == 200
        assert update.json()["status"] == "in_progress"

        complete = client.post(f"/tasks/{task_id}/complete", json={"result": "analysis complete"})
        assert complete.status_code == 200
        assert complete.json()["status"] == "done"
        assert complete.json()["result"] == "analysis complete"

        board_view = client.get("/board/tasks", params={"agent_id": "concierge"})
        assert board_view.status_code == 200
        assert len(board_view.json()["tasks"]) == 1
        assert board_view.json()["tasks"][0]["id"] == task_id

        remove = client.delete(f"/tasks/{task_id}")
        assert remove.status_code == 200
        assert remove.json() == {"deleted": True}

        after = client.get("/tasks", params={"agent_id": "concierge"})
        assert after.status_code == 200
        assert after.json() == []

        sync = client.post("/tasks/sync", json={"mode": "sync"})
        assert sync.status_code == 503
