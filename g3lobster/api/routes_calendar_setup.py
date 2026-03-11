"""REST endpoint to set up the calendar conflict-resolver agent."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.post("/setup-conflict-resolver")
async def setup_conflict_resolver(request: Request) -> dict:
    """Create the conflict-resolver agent persona and register its cron task."""
    from g3lobster.calendar.setup import setup_conflict_resolver as _setup

    data_dir = getattr(request.app.state.registry, "data_dir", None)
    if data_dir is None:
        return {"error": "data_dir not available from registry"}

    cron_store = getattr(request.app.state, "cron_store", None)
    result = _setup(data_dir, cron_store)
    return result
