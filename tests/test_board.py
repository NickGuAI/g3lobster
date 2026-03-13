"""Tests for the task board store and API routes."""

from __future__ import annotations

from pathlib import Path

from g3lobster.board.store import BoardStore


def test_board_store_crud(tmp_path: Path):
    store = BoardStore(data_dir=str(tmp_path))

    # Initially empty
    assert store.list_items() == []

    # Insert
    item = store.insert(
        type="bug",
        title="Fix login",
        link="https://github.com/org/repo/issues/1",
        status="todo",
        agent_id="agent-a",
        metadata={"priority": "high"},
    )
    assert item.type == "bug"
    assert item.title == "Fix login"
    assert item.agent_id == "agent-a"
    assert item.metadata == {"priority": "high"}

    # List
    items = store.list_items()
    assert len(items) == 1

    # Get
    fetched = store.get_item(item.id)
    assert fetched is not None
    assert fetched.link == "https://github.com/org/repo/issues/1"

    # Update
    updated = store.update(item.id, status="in_progress", title="Fix login page")
    assert updated is not None
    assert updated.status == "in_progress"
    assert updated.title == "Fix login page"
    assert updated.updated_at != item.created_at  # updated_at changed

    # Update non-existent
    assert store.update("no-such-id", status="done") is None

    # Delete
    assert store.delete(item.id) is True
    assert store.delete(item.id) is False
    assert store.list_items() == []


def test_board_store_filters(tmp_path: Path):
    store = BoardStore(data_dir=str(tmp_path))

    store.insert(type="bug", title="Bug 1", status="todo", agent_id="a")
    store.insert(type="feature", title="Feature 1", status="in_progress", agent_id="b")
    store.insert(type="bug", title="Bug 2", status="done", agent_id="a")

    # Filter by type
    bugs = store.list_items(type_filter="bug")
    assert len(bugs) == 2

    # Filter by status
    todo = store.list_items(status_filter="todo")
    assert len(todo) == 1
    assert todo[0].title == "Bug 1"

    # Filter by agent_id
    agent_b = store.list_items(agent_id="b")
    assert len(agent_b) == 1
    assert agent_b[0].title == "Feature 1"

    # Combined filters
    bugs_done = store.list_items(type_filter="bug", status_filter="done")
    assert len(bugs_done) == 1
    assert bugs_done[0].title == "Bug 2"


def test_board_api_routes(tmp_path: Path):
    """Test task board REST endpoints via TestClient."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from g3lobster.api.routes_tasks import router
    from g3lobster.board.store import BoardStore

    app = FastAPI()
    app.include_router(router)
    store = BoardStore(data_dir=str(tmp_path))
    app.state.board_store = store
    app.state.sheets_sync = None

    with TestClient(app) as client:
        # Insert
        resp = client.post("/tasks", json={
            "type": "feature",
            "title": "Add dark mode",
            "link": "https://github.com/org/repo/issues/42",
            "status": "todo",
        })
        assert resp.status_code == 201
        task_id = resp.json()["id"]
        assert resp.json()["type"] == "feature"

        # List
        resp = client.get("/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        # List with filter
        resp = client.get("/tasks?type=feature")
        assert len(resp.json()) == 1
        resp = client.get("/tasks?type=bug")
        assert len(resp.json()) == 0

        # Get one
        resp = client.get(f"/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Add dark mode"

        # Get non-existent
        resp = client.get("/tasks/no-such-id")
        assert resp.status_code == 404

        # Update
        resp = client.put(f"/tasks/{task_id}", json={
            "status": "in_progress",
            "agent_id": "my-agent",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"
        assert resp.json()["agent_id"] == "my-agent"

        # Update non-existent
        resp = client.put("/tasks/bad-id", json={"status": "done"})
        assert resp.status_code == 404

        # Delete
        resp = client.delete(f"/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}

        # Delete non-existent
        resp = client.delete(f"/tasks/{task_id}")
        assert resp.status_code == 404

        # Sync without sheets configured
        resp = client.post("/tasks/sync", json={"mode": "sync"})
        assert resp.status_code == 503


def test_board_store_metadata_update(tmp_path: Path):
    store = BoardStore(data_dir=str(tmp_path))
    item = store.insert(type="chore", title="Clean up", metadata={"area": "backend"})
    updated = store.update(item.id, metadata={"area": "frontend", "tags": ["ui"]})
    assert updated is not None
    assert updated.metadata == {"area": "frontend", "tags": ["ui"]}
