"""Internal delegation API for MCP tool bridge."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/delegation", tags=["delegation"])


@router.post("/run")
async def create_delegation_run(request: Request) -> dict:
    """Called by delegation MCP server to trigger agent-to-agent delegation."""
    body = await request.json()
    registry = request.app.state.registry

    parent_agent_id = body.get("parent_agent_id")
    child_agent_id = body.get("child_agent_id")
    task = body.get("task")
    parent_session_id = body.get("parent_session_id", "default")
    timeout_s = float(body.get("timeout_s", 300.0))

    if not parent_agent_id or not child_agent_id or not task:
        raise HTTPException(
            status_code=422,
            detail="parent_agent_id, child_agent_id, and task are required",
        )

    try:
        run = await registry.delegate_task(
            parent_agent_id=parent_agent_id,
            child_agent_id=child_agent_id,
            task_prompt=task,
            parent_session_id=parent_session_id,
            timeout_s=timeout_s,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "result": run.result,
        "error": run.error,
    }


@router.get("/runs/{run_id}")
async def get_delegation_run(run_id: str, request: Request) -> dict:
    registry = request.app.state.registry
    run = registry.subagent_registry.get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "result": run.result,
        "error": run.error,
    }


@router.get("/runs")
async def list_delegation_runs(
    request: Request,
    parent_agent_id: Optional[str] = Query(default=None),
) -> List[dict]:
    registry = request.app.state.registry
    runs = registry.subagent_registry.list_runs(parent_agent_id)
    return [
        {
            "run_id": r.run_id,
            "parent": r.parent_agent_id,
            "child": r.child_agent_id,
            "status": r.status.value,
            "task": r.task[:100],
        }
        for r in runs
    ]
