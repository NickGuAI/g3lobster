"""REST endpoints for per-agent cron task management."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from g3lobster.cron.store import CronTask
from g3lobster.tasks.types import Task, TaskStatus

router = APIRouter(prefix="/agents", tags=["cron"])


class CronTaskCreateRequest(BaseModel):
    schedule: str
    instruction: str
    enabled: bool = True
    dm_target: Optional[str] = None


class CronTaskUpdateRequest(BaseModel):
    schedule: Optional[str] = None
    instruction: Optional[str] = None
    enabled: Optional[bool] = None
    dm_target: Optional[str] = None


class CronValidateRequest(BaseModel):
    schedule: str


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


@router.get("/_cron/all")
async def list_all_cron_tasks(request: Request) -> List[dict]:
    store = _get_store(request)
    return [asdict(t) for t in store.list_all_enabled()]


@router.post("/_cron/validate")
async def validate_cron(payload: CronValidateRequest, request: Request) -> dict:
    try:
        from apscheduler.triggers.cron import CronTrigger
        trigger = CronTrigger.from_crontab(payload.schedule)
        next_fire = trigger.get_next_fire_time(None, datetime.now(tz=timezone.utc))
        return {
            "valid": True,
            "next_run": next_fire.isoformat() if next_fire else None,
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="apscheduler is not installed")
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


@router.get("/{agent_id}/crons")
async def list_cron_tasks(agent_id: str, request: Request) -> List[dict]:
    store = _get_store(request)
    return [asdict(t) for t in store.list_tasks(agent_id)]


@router.post("/{agent_id}/crons", status_code=201)
async def create_cron_task(agent_id: str, payload: CronTaskCreateRequest, request: Request) -> dict:
    store = _get_store(request)
    task = store.add_task(
        agent_id,
        payload.schedule,
        payload.instruction,
        enabled=payload.enabled,
        dm_target=payload.dm_target,
    )
    _reload_manager(request)
    return asdict(task)


@router.put("/{agent_id}/crons/{task_id}")
async def update_cron_task(agent_id: str, task_id: str, payload: CronTaskUpdateRequest, request: Request) -> dict:
    store = _get_store(request)
    updates = payload.model_dump(exclude_unset=True)
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


@router.post("/{agent_id}/crons/{task_id}/run")
async def run_cron_task(agent_id: str, task_id: str, request: Request) -> dict:
    store = _get_store(request)
    task = store.get_task(agent_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Cron task not found")

    registry = request.app.state.registry
    runtime = registry.get_agent(agent_id)
    if not runtime:
        started = await registry.start_agent(agent_id)
        if not started:
            raise HTTPException(status_code=400, detail="Agent could not be started")
        runtime = registry.get_agent(agent_id)
        if not runtime:
            raise HTTPException(status_code=400, detail="Agent not available")

    import time as time_mod
    from g3lobster.cron.store import CronRunRecord

    session_id = f"cron__{agent_id}"
    run_task = Task(prompt=task.instruction, session_id=session_id)
    now = datetime.now(tz=timezone.utc).isoformat()
    store.update_task(agent_id, task_id, last_run=now)

    start_time = time_mod.monotonic()
    try:
        result = await runtime.assign(run_task)
        duration = round(time_mod.monotonic() - start_time, 1)
        status = "completed" if result.status == TaskStatus.COMPLETED else "failed"
        preview = (result.result or result.error or "")[:200]
    except Exception as exc:
        duration = round(time_mod.monotonic() - start_time, 1)
        status = "failed"
        preview = f"Exception: {exc}"[:200]

    store.record_run(agent_id, CronRunRecord(
        task_id=task_id, fired_at=now, status=status,
        duration_s=duration, result_preview=preview,
    ))

    return {
        "task_id": task_id,
        "status": status,
        "duration_s": duration,
        "result_preview": preview,
    }


@router.get("/{agent_id}/crons/{task_id}/history")
async def get_cron_task_history(agent_id: str, task_id: str, request: Request) -> dict:
    store = _get_store(request)
    task = store.get_task(agent_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Cron task not found")
    runs = store.get_history(agent_id, task_id)
    return {"task_id": task_id, "runs": runs}
