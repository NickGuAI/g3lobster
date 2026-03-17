"""REST endpoints for the unified task board."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from g3lobster.board.store import TaskItem

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskInsertRequest(BaseModel):
    title: str = Field(min_length=1)
    type: str = "chore"
    status: str = "todo"
    priority: str = "normal"
    agent_id: Optional[str] = None
    created_by: str = "human"
    result: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    link: str = ""


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    agent_id: Optional[str] = None
    created_by: Optional[str] = None
    result: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    link: Optional[str] = None


class TaskCompleteRequest(BaseModel):
    result: Optional[str] = None


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


def _serialize(item: TaskItem) -> dict:
    return asdict(item)


def _publish_task_event(
    request: Request,
    event_type: str,
    item: Optional[TaskItem] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus is None:
        return
    payload: Dict[str, Any] = {"type": event_type}
    if item is not None:
        payload["task"] = _serialize(item)
    if extra:
        payload.update(extra)
    event_bus.publish("__board__", payload)
    if item is not None and item.agent_id:
        event_bus.publish(item.agent_id, payload)


@router.get("")
async def list_tasks(
    request: Request,
    type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    created_by: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1, le=500),
) -> List[dict]:
    store = _get_store(request)
    items = store.list_items(
        type_filter=type,
        status_filter=status,
        priority_filter=priority,
        agent_id=agent_id,
        created_by=created_by,
        limit=limit,
    )
    return [_serialize(item) for item in items]


@router.post("", status_code=201)
async def insert_task(payload: TaskInsertRequest, request: Request) -> dict:
    store = _get_store(request)
    item = store.insert(
        type=payload.type,
        title=payload.title,
        status=payload.status,
        priority=payload.priority,
        agent_id=payload.agent_id,
        created_by=payload.created_by,
        result=payload.result,
        metadata=payload.metadata,
        link=payload.link,
    )
    _publish_task_event(request, "board_task_created", item=item)
    return _serialize(item)


@router.post("/sync")
async def sync_tasks(payload: SyncRequest, request: Request) -> dict:
    sync = _get_sheets_sync(request)
    if payload.mode == "push":
        return sync.push()
    if payload.mode == "pull":
        return sync.pull()
    return sync.sync()


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict:
    store = _get_store(request)
    item = store.get_item(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    return _serialize(item)


@router.put("/{task_id}")
async def update_task(task_id: str, payload: TaskUpdateRequest, request: Request) -> dict:
    store = _get_store(request)
    updates = payload.model_dump(exclude_none=True)
    item = store.update(task_id, **updates)
    if item is None:
        raise HTTPException(status_code=404, detail="Task not found")
    _publish_task_event(request, "board_task_updated", item=item)
    return _serialize(item)


@router.post("/{task_id}/complete")
async def complete_task(task_id: str, payload: TaskCompleteRequest, request: Request) -> dict:
    store = _get_store(request)
    item = store.complete(task_id, result=payload.result)
    if item is None:
        raise HTTPException(status_code=404, detail="Task not found")
    _publish_task_event(request, "board_task_completed", item=item)
    return _serialize(item)


@router.delete("/{task_id}")
async def delete_task(task_id: str, request: Request) -> dict:
    store = _get_store(request)
    item = store.get_item(task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Task not found")
    deleted = store.delete(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    _publish_task_event(
        request,
        "board_task_deleted",
        item=item,
        extra={"task_id": task_id},
    )
    return {"deleted": True}
