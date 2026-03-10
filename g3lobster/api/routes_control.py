"""Control-plane API routes for multi-agent orchestration."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from g3lobster.control_plane import BackpressureError
from g3lobster.control_plane.task_unit import TaskUnit, TaskUnitStatus
from g3lobster.control_plane.types import Plan, PlanStep

router = APIRouter(prefix="/control-plane", tags=["control-plane"])


class PlanStepRequest(BaseModel):
    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    agent_id: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PlanRequest(BaseModel):
    steps: List[PlanStepRequest] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskSubmitRequest(BaseModel):
    prompt: str = Field(min_length=1)
    source: str = "human"
    agent_id: Optional[str] = None
    parent_id: Optional[str] = None
    session_id: Optional[str] = None
    timeout_s: float = Field(default=300.0, gt=0)
    wait: bool = False
    long_running: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    plan: Optional[PlanRequest] = None


class DelegateRequest(BaseModel):
    parent_agent_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    agent_name: Optional[str] = None
    wait: bool = True
    timeout_s: float = Field(default=300.0, gt=0)
    parent_session_id: str = "default"
    parent_task_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _control_plane(request: Request):
    control_plane = getattr(request.app.state, "control_plane", None)
    if control_plane is None:
        raise HTTPException(status_code=503, detail="Control plane is disabled")
    return control_plane


def _serialize_task(control_plane, task: TaskUnit) -> Dict[str, Any]:
    payload = task.as_dict()
    payload["children"] = [child.id for child in control_plane.task_registry.children(task.id)]
    return payload


def _build_plan(payload: PlanRequest) -> Plan:
    return Plan(
        steps=[
            PlanStep(
                id=step.id,
                prompt=step.prompt,
                agent_id=step.agent_id,
                depends_on=list(step.depends_on),
                metadata=dict(step.metadata),
            )
            for step in payload.steps
        ],
        metadata=dict(payload.metadata),
    )


def _parse_status(raw: Optional[str]) -> Optional[TaskUnitStatus]:
    if not raw:
        return None
    try:
        return TaskUnitStatus(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown status: {raw}") from exc


@router.post("/tasks")
async def create_task(payload: TaskSubmitRequest, request: Request) -> Dict[str, Any]:
    control_plane = _control_plane(request)

    metadata = dict(payload.metadata)
    metadata.setdefault("timeout_s", payload.timeout_s)
    if payload.session_id:
        metadata["session_id"] = payload.session_id
    if payload.long_running:
        metadata["long_running"] = True

    task = TaskUnit(
        prompt=payload.prompt,
        source=payload.source,
        parent_id=payload.parent_id,
        metadata=metadata,
    )
    control_plane.task_registry.add(task)

    try:
        if payload.plan is not None:
            plan = _build_plan(payload.plan)
            await control_plane.orchestrator.execute_plan(plan, task)
        else:
            await control_plane.dispatcher.dispatch(
                task,
                preferred_agent_id=payload.agent_id,
                on_complete=control_plane.orchestrator.on_task_complete,
            )
    except BackpressureError as exc:
        control_plane.task_registry.fail(task.id, str(exc))
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        control_plane.task_registry.fail(task.id, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if payload.wait:
        try:
            task = await control_plane.task_registry.wait_for_terminal(task.id, timeout_s=payload.timeout_s)
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Task wait timed out") from exc

    latest = control_plane.task_registry.get(task.id)
    if latest is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _serialize_task(control_plane, latest)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> Dict[str, Any]:
    control_plane = _control_plane(request)
    task = control_plane.task_registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _serialize_task(control_plane, task)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> Dict[str, Any]:
    control_plane = _control_plane(request)
    task = control_plane.task_registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    await control_plane.dispatcher.cancel(task_id)
    cancelled = control_plane.task_registry.cancel_tree(task_id)
    return {
        "cancelled": True,
        "task_id": task_id,
        "tasks_cancelled": [item.id for item in cancelled],
    }


@router.get("/tasks")
async def list_tasks(
    request: Request,
    status: Optional[str] = Query(default=None),
    agent: Optional[str] = Query(default=None),
    parent: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> Dict[str, Any]:
    control_plane = _control_plane(request)
    items = control_plane.task_registry.list(
        status=_parse_status(status),
        agent_id=agent,
        parent_id=parent,
        source=source,
        limit=limit,
    )
    return {
        "tasks": [_serialize_task(control_plane, item) for item in items],
        "count": len(items),
    }


@router.get("/status")
async def status(request: Request) -> Dict[str, Any]:
    control_plane = _control_plane(request)
    registry = request.app.state.registry
    agent_payload = await registry.status()

    return {
        "agents": agent_payload.get("agents", []),
        "tasks": control_plane.task_registry.summary(),
        "dispatcher": control_plane.dispatcher.snapshot(),
    }


@router.post("/delegate")
async def delegate(payload: DelegateRequest, request: Request) -> Dict[str, Any]:
    control_plane = _control_plane(request)

    if payload.agent_name and payload.agent_name == payload.parent_agent_id:
        raise HTTPException(status_code=422, detail="Circular delegation is not allowed")

    if payload.agent_name and payload.parent_task_id:
        blocked_agents = {payload.parent_agent_id}
        cursor = control_plane.task_registry.get(payload.parent_task_id)
        while cursor is not None:
            if cursor.agent_id:
                blocked_agents.add(cursor.agent_id)
            if not cursor.parent_id:
                break
            cursor = control_plane.task_registry.get(cursor.parent_id)
        if payload.agent_name in blocked_agents:
            raise HTTPException(status_code=422, detail="Delegation cycle detected")

    metadata = dict(payload.metadata)
    metadata.setdefault("session_id", payload.parent_session_id)
    metadata.setdefault("timeout_s", payload.timeout_s)
    metadata.setdefault("delegated_by", payload.parent_agent_id)

    task = TaskUnit(
        prompt=payload.prompt,
        source="agent",
        parent_id=payload.parent_task_id,
        metadata=metadata,
    )
    control_plane.task_registry.add(task)

    try:
        agent_id = await control_plane.dispatcher.dispatch(
            task,
            preferred_agent_id=payload.agent_name,
            on_complete=control_plane.orchestrator.on_task_complete,
        )
    except BackpressureError as exc:
        control_plane.task_registry.fail(task.id, str(exc))
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        control_plane.task_registry.fail(task.id, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if payload.wait:
        try:
            task = await control_plane.task_registry.wait_for_terminal(task.id, timeout_s=payload.timeout_s)
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Delegated task wait timed out") from exc

    latest = control_plane.task_registry.get(task.id)
    if latest is None:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "task_id": latest.id,
        "agent_id": agent_id,
        "status": latest.status.value,
        "result": latest.result_md,
        "error": latest.error,
    }


@router.get("/sessions")
async def list_sessions(request: Request) -> Dict[str, Any]:
    control_plane = _control_plane(request)
    spawner = getattr(control_plane, "tmux_spawner", None)
    if spawner is None:
        return {"sessions": []}

    await spawner.evict_idle_sessions()
    return {"sessions": spawner.list_sessions()}
