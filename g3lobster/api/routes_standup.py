"""REST endpoints for per-agent standup management."""

from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from g3lobster.standup.store import StandupConfig

router = APIRouter(prefix="/agents", tags=["standup"])


class StandupConfigRequest(BaseModel):
    team_members: List[dict]  # [{user_id, display_name, chat_user_id}]
    prompt_schedule: str = "0 9 * * 1-5"
    summary_schedule: str = "0 17 * * 1-5"
    prompt_template: str = "What did you do yesterday? What are you doing today? Any blockers?"
    summary_space_id: str = ""
    enabled: bool = True


class StandupTriggerRequest(BaseModel):
    action: str  # "prompt" or "summary"


def _get_store(request: Request):
    store = getattr(request.app.state, "standup_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Standup store is not available")
    return store


def _get_orchestrator(request: Request):
    orchestrator = getattr(request.app.state, "standup_orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Standup orchestrator is not available")
    return orchestrator


def _register_standup_crons(request: Request, agent_id: str, config: StandupConfig) -> None:
    cron_store = getattr(request.app.state, "cron_store", None)
    if not cron_store:
        return
    # Remove old standup crons
    existing = cron_store.list_tasks(agent_id)
    for task in existing:
        if task.instruction.startswith("__standup_"):
            cron_store.delete_task(agent_id, task.id)
    # Add new ones if enabled
    if config.enabled:
        cron_store.add_task(agent_id, config.prompt_schedule, "__standup_prompt__")
        cron_store.add_task(agent_id, config.summary_schedule, "__standup_summary__")
    # Reload cron manager
    manager = getattr(request.app.state, "cron_manager", None)
    if manager:
        try:
            manager.reload()
        except Exception:
            pass


@router.get("/{agent_id}/standup")
async def get_standup_config(agent_id: str, request: Request) -> dict:
    store = _get_store(request)
    config = store.get_config(agent_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Standup not configured for this agent")
    return asdict(config)


@router.put("/{agent_id}/standup")
async def put_standup_config(agent_id: str, payload: StandupConfigRequest, request: Request) -> dict:
    store = _get_store(request)
    config = StandupConfig(
        agent_id=agent_id,
        team_members=payload.team_members,
        prompt_schedule=payload.prompt_schedule,
        summary_schedule=payload.summary_schedule,
        prompt_template=payload.prompt_template,
        summary_space_id=payload.summary_space_id,
        enabled=payload.enabled,
    )
    saved = store.save_config(agent_id, config)
    _register_standup_crons(request, agent_id, saved)
    return asdict(saved)


@router.delete("/{agent_id}/standup")
async def delete_standup_config(agent_id: str, request: Request) -> dict:
    store = _get_store(request)
    # Remove associated cron tasks first
    cron_store = getattr(request.app.state, "cron_store", None)
    if cron_store:
        existing = cron_store.list_tasks(agent_id)
        for task in existing:
            if task.instruction.startswith("__standup_"):
                cron_store.delete_task(agent_id, task.id)
        manager = getattr(request.app.state, "cron_manager", None)
        if manager:
            try:
                manager.reload()
            except Exception:
                pass
    deleted = store.delete_config(agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Standup not configured for this agent")
    return {"deleted": True}


@router.get("/{agent_id}/standup/entries")
async def get_standup_entries(agent_id: str, request: Request, date: Optional[str] = None) -> dict:
    from datetime import datetime, timezone

    store = _get_store(request)
    if date is None:
        date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    entries = store.get_entries(agent_id, date)
    return {"date": date, "entries": [asdict(e) for e in entries]}


@router.get("/{agent_id}/standup/trends")
async def get_standup_trends(agent_id: str, request: Request, days: int = 14) -> dict:
    from g3lobster.standup.trends import TrendAnalyzer

    store = _get_store(request)
    analyzer = TrendAnalyzer(store)
    blocker_analysis = analyzer.analyze_blockers(agent_id, days=days)
    pattern_analysis = analyzer.analyze_patterns(agent_id, days=days)
    report = analyzer.format_trend_report(blocker_analysis, pattern_analysis)
    return {
        "days": days,
        "blockers": blocker_analysis,
        "patterns": pattern_analysis,
        "report": report,
    }


@router.post("/{agent_id}/standup/trigger")
async def trigger_standup(agent_id: str, payload: StandupTriggerRequest, request: Request) -> dict:
    orchestrator = _get_orchestrator(request)
    if payload.action == "prompt":
        await orchestrator.prompt_team(agent_id)
        return {"triggered": "prompt", "agent_id": agent_id}
    elif payload.action == "summary":
        summary = await orchestrator.generate_summary(agent_id)
        return {"triggered": "summary", "agent_id": agent_id, "summary": summary}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {payload.action!r}. Use 'prompt' or 'summary'.")
