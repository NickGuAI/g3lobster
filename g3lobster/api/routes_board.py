"""Kanban board routes."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Request, Query
from fastapi.responses import FileResponse
from pathlib import Path
from g3lobster.api.routes_agents import _to_task_summary
from g3lobster.api.models import TaskListResponse
from g3lobster.agents.persona import list_personas

router = APIRouter(prefix="/board", tags=["board"])

@router.get("")
async def get_board(request: Request):
    static_dir = Path(__file__).resolve().parent.parent / "static"
    return FileResponse(static_dir / "board.html")

@router.get("/tasks", response_model=TaskListResponse)
async def list_board_tasks(
    request: Request,
    limit_per_agent: int = Query(default=50, ge=1, le=200),
) -> TaskListResponse:
    config = request.app.state.config
    registry = request.app.state.registry
    
    all_tasks = []
    for persona in list_personas(config.agents.data_dir):
        # We use the registry to get both current and historical tasks
        tasks = registry.list_tasks(persona.id, limit=limit_per_agent)
        all_tasks.extend(tasks)
        
    # Sort by creation time, descending (newest first)
    all_tasks.sort(key=lambda t: t.created_at, reverse=True)
    
    return TaskListResponse(tasks=[_to_task_summary(task) for task in all_tasks])
