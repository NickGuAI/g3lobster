"""Internal delegation API for cross-agent task delegation."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/delegation", tags=["delegation"])


class DelegationRunRequest(BaseModel):
    parent_agent_id: str = Field(min_length=1)
    child_agent_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    parent_session_id: str = "default"
    timeout_s: float = 300.0


class DelegationRunResponse(BaseModel):
    run_id: str
    status: str
    result: Optional[str] = None
    error: Optional[str] = None


class DelegationRunSummary(BaseModel):
    run_id: str
    parent: str
    child: str
    status: str
    task: str


@router.post("/run", response_model=DelegationRunResponse)
async def create_delegation_run(payload: DelegationRunRequest, request: Request) -> DelegationRunResponse:
    """Trigger an agent-to-agent delegation run."""
    registry = request.app.state.registry
    try:
        run = await registry.delegate_task(
            parent_agent_id=payload.parent_agent_id,
            child_agent_id=payload.child_agent_id,
            task_prompt=payload.task,
            parent_session_id=payload.parent_session_id,
            timeout_s=payload.timeout_s,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return DelegationRunResponse(
        run_id=run.run_id,
        status=run.status.value,
        result=run.result,
        error=run.error,
    )


@router.get("/runs/{run_id}", response_model=DelegationRunResponse)
async def get_delegation_run(run_id: str, request: Request) -> DelegationRunResponse:
    """Get the status of a delegation run."""
    registry = request.app.state.registry
    run = registry.subagent_registry.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return DelegationRunResponse(
        run_id=run.run_id,
        status=run.status.value,
        result=run.result,
        error=run.error,
    )


@router.get("/runs", response_model=list[DelegationRunSummary])
async def list_delegation_runs(
    request: Request,
    parent_agent_id: Optional[str] = None,
) -> list[DelegationRunSummary]:
    """List delegation runs, optionally filtered by parent agent."""
    registry = request.app.state.registry
    runs = registry.subagent_registry.list_runs(parent_agent_id)
    return [
        DelegationRunSummary(
            run_id=r.run_id,
            parent=r.parent_agent_id,
            child=r.child_agent_id,
            status=r.status.value,
            task=r.task[:100],
        )
        for r in runs
    ]
