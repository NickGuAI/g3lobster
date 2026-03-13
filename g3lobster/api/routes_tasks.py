"""REST endpoints for the shared task board."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from g3lobster.board.store import BoardItem

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskInsertRequest(BaseModel):
    type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    link: str = ""
    status: str = "todo"
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskUpdateRequest(BaseModel):
    type: Optional[str] = None
    title: Optional[str] = None
    link: Optional[str] = None
    status: Optional[str] = None
    agent_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SyncRequest(BaseModel):
    mode: str = "sync"  # "push", "pull", or "sync"


def _get_store(request: Request):
    store = getattr(request.app.state, "board_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Task board is not available")
    return store


def _get_sheets_sync(request: Request):
    sync = getattr(request.app.state, "sheets_sync", None)
    if sync is None:
        raise HTTPException(status_code=503, detail="Google Sheets sync is not configured")
    return sync


@router.get("")
async def list_tasks(
    request: Request,
    type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
) -> List[dict]:
    store = _get_store(request)
    items = store.list_items(type_filter=type, status_filter=status, agent_id=agent_id)
    return [asdict(i) for i in items]


@router.post("", status_code=201)
async def insert_task(payload: TaskInsertRequest, request: Request) -> dict:
    store = _get_store(request)
    item = store.insert(
        type=payload.type,
        title=payload.title,
        link=payload.link,
        status=payload.status,
        agent_id=payload.agent_id,
        metadata=payload.metadata,
    )
    return asdict(item)


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict:
    store = _get_store(request)
    item = store.get_item(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    return asdict(item)


@router.put("/{task_id}")
async def update_task(task_id: str, payload: TaskUpdateRequest, request: Request) -> dict:
    store = _get_store(request)
    updates = payload.model_dump(exclude_none=True)
    item = store.update(task_id, **updates)
    if item is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return asdict(item)


@router.delete("/{task_id}")
async def delete_task(task_id: str, request: Request) -> dict:
    store = _get_store(request)
    deleted = store.delete(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"deleted": True}


@router.post("/sync")
async def sync_tasks(payload: SyncRequest, request: Request) -> dict:
    sync = _get_sheets_sync(request)
    if payload.mode == "push":
        return sync.push()
    elif payload.mode == "pull":
        return sync.pull()
    else:
        return sync.sync()
