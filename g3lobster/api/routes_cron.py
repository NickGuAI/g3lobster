"""REST endpoints for per-agent cron task management."""

from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/agents", tags=["cron"])


class CronTaskCreateRequest(BaseModel):
    schedule: str
    instruction: str


class CronTaskUpdateRequest(BaseModel):
    schedule: Optional[str] = None
    instruction: Optional[str] = None
    enabled: Optional[bool] = None


def _get_store(request: Request):
    store = getattr(request.app.state, "cron_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Cron store is not available")
    return store


def _reload_manager(request: Request) -> None:
    """Signal the cron manager to re-sync its scheduled jobs."""
    manager = getattr(request.app.state, "cron_manager", None)
    if manager:
        try:
            manager.reload()
        except Exception:
            pass  # Non-fatal — store is already updated


@router.get("/{agent_id}/crons")
async def list_cron_tasks(agent_id: str, request: Request) -> List[dict]:
    store = _get_store(request)
    return [asdict(t) for t in store.list_tasks(agent_id)]


@router.post("/{agent_id}/crons", status_code=201)
async def create_cron_task(agent_id: str, payload: CronTaskCreateRequest, request: Request) -> dict:
    store = _get_store(request)
    task = store.add_task(agent_id, payload.schedule, payload.instruction)
    _reload_manager(request)
    return asdict(task)


@router.put("/{agent_id}/crons/{task_id}")
async def update_cron_task(agent_id: str, task_id: str, payload: CronTaskUpdateRequest, request: Request) -> dict:
    store = _get_store(request)
    updates = payload.model_dump(exclude_none=True)
    task = store.update_task(agent_id, task_id, **updates)
    if task is None:
        raise HTTPException(status_code=404, detail="Cron task not found")
    _reload_manager(request)
    return asdict(task)


@router.delete("/{agent_id}/crons/{task_id}")
async def delete_cron_task(agent_id: str, task_id: str, request: Request) -> dict:
    store = _get_store(request)
    deleted = store.delete_task(agent_id, task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cron task not found")
    _reload_manager(request)
    return {"deleted": True}
