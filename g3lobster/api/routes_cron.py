"""REST endpoints for per-agent cron task management."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from g3lobster.cron.store import CronTask
from g3lobster.tasks.types import Task, TaskStatus

router = APIRouter(prefix="/agents", tags=["cron"])
logger = logging.getLogger(__name__)


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


def _get_manager(request: Request):
    return getattr(request.app.state, "cron_manager", None)


def _agent_request_context(request: Request, agent_id: str) -> dict:
    source = str(request.headers.get("x-g3lobster-agent-source", "")).strip().lower()
    actor_agent_id = str(request.headers.get("x-g3lobster-actor-agent-id", "")).strip()
    is_agent_mcp = source == "mcp" and actor_agent_id == agent_id
    return {
        "enforce_guardrails": is_agent_mcp,
        "actor_agent_id": actor_agent_id if is_agent_mcp else None,
        "source": "mcp" if is_agent_mcp else "api",
    }


def _audit_agent_fallback(context: dict, action: str, agent_id: str, task_id: str, details: str = "") -> None:
    actor_agent_id = context.get("actor_agent_id")
    if not actor_agent_id:
        return
    detail_suffix = f" {details}" if details else ""
    logger.info(
        "cron_audit action=%s source=%s actor_agent_id=%s owner_agent_id=%s task_id=%s%s",
        action,
        context.get("source", "api"),
        actor_agent_id,
        agent_id,
        task_id,
        detail_suffix,
    )


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
    manager = _get_manager(request)
    if manager and hasattr(manager, "list_tasks"):
        return [asdict(t) for t in manager.list_tasks(agent_id)]
    store = _get_store(request)
    return [asdict(t) for t in store.list_tasks(agent_id)]


@router.post("/{agent_id}/crons", status_code=201)
async def create_cron_task(agent_id: str, payload: CronTaskCreateRequest, request: Request) -> dict:
    context = _agent_request_context(request, agent_id)
    manager = _get_manager(request)
    if manager and hasattr(manager, "create_task"):
        try:
            task = manager.create_task(
                agent_id=agent_id,
                schedule=payload.schedule,
                instruction=payload.instruction,
                enabled=payload.enabled,
                dm_target=payload.dm_target,
                enforce_agent_guardrails=context["enforce_guardrails"],
                actor_agent_id=context["actor_agent_id"],
                source=context["source"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return asdict(task)

    store = _get_store(request)
    task = store.add_task(
        agent_id,
        payload.schedule,
        payload.instruction,
        enabled=payload.enabled,
        dm_target=payload.dm_target,
    )
    _reload_manager(request)
    _audit_agent_fallback(
        context,
        action="create",
        agent_id=agent_id,
        task_id=task.id,
        details=f"schedule={task.schedule!r} enabled={task.enabled}",
    )
    return asdict(task)


@router.put("/{agent_id}/crons/{task_id}")
async def update_cron_task(agent_id: str, task_id: str, payload: CronTaskUpdateRequest, request: Request) -> dict:
    context = _agent_request_context(request, agent_id)
    manager = _get_manager(request)
    if manager and hasattr(manager, "update_task"):
        try:
            task = manager.update_task(
                agent_id=agent_id,
                task_id=task_id,
                schedule=payload.schedule,
                instruction=payload.instruction,
                enabled=payload.enabled,
                enforce_agent_guardrails=context["enforce_guardrails"],
                actor_agent_id=context["actor_agent_id"],
                source=context["source"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if task is None:
            raise HTTPException(status_code=404, detail="Cron task not found")
        return asdict(task)

    store = _get_store(request)
    updates = payload.model_dump(exclude_unset=True)
    task = store.update_task(agent_id, task_id, **updates)
    if task is None:
        raise HTTPException(status_code=404, detail="Cron task not found")
    _reload_manager(request)
    _audit_agent_fallback(
        context,
        action="update",
        agent_id=agent_id,
        task_id=task_id,
        details=f"fields={sorted(updates.keys())}",
    )
    return asdict(task)


@router.delete("/{agent_id}/crons/{task_id}")
async def delete_cron_task(agent_id: str, task_id: str, request: Request) -> dict:
    context = _agent_request_context(request, agent_id)
    manager = _get_manager(request)
    if manager and hasattr(manager, "delete_task"):
        deleted = manager.delete_task(
            agent_id=agent_id,
            task_id=task_id,
            actor_agent_id=context["actor_agent_id"],
            source=context["source"],
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Cron task not found")
        return {"deleted": True}

    store = _get_store(request)
    deleted = store.delete_task(agent_id, task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cron task not found")
    _reload_manager(request)
    _audit_agent_fallback(context, action="delete", agent_id=agent_id, task_id=task_id)
    return {"deleted": True}


@router.post("/{agent_id}/crons/{task_id}/run")
async def run_cron_task(agent_id: str, task_id: str, request: Request) -> dict:
    context = _agent_request_context(request, agent_id)
    manager = _get_manager(request)
    if manager and hasattr(manager, "run_task"):
        try:
            return await manager.run_task(
                agent_id=agent_id,
                task_id=task_id,
                actor_agent_id=context["actor_agent_id"],
                source=context["source"],
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Cron task not found")

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
    _audit_agent_fallback(
        context,
        action="run",
        agent_id=agent_id,
        task_id=task_id,
        details=f"status={status}",
    )

    return {
        "task_id": task_id,
        "status": status,
        "duration_s": duration,
        "result_preview": preview,
    }


@router.get("/{agent_id}/crons/{task_id}/history")
async def get_cron_task_history(agent_id: str, task_id: str, request: Request) -> dict:
    manager = _get_manager(request)
    if manager and hasattr(manager, "get_task_history"):
        try:
            runs = manager.get_task_history(agent_id=agent_id, task_id=task_id, limit=20)
        except KeyError:
            raise HTTPException(status_code=404, detail="Cron task not found")
        return {"task_id": task_id, "runs": runs}

    store = _get_store(request)
    task = store.get_task(agent_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Cron task not found")
    runs = store.get_history(agent_id, task_id)
    return {"task_id": task_id, "runs": runs}
