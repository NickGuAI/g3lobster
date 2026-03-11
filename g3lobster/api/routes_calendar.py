"""REST endpoints for calendar conflict detection and scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/calendar", tags=["calendar"])


class ConflictScanRequest(BaseModel):
    calendar_id: str = "primary"
    hours_ahead: float = 24.0


class FindSlotsRequest(BaseModel):
    attendee_emails: List[str]
    duration_minutes: int = 30
    days_ahead: int = 7
    max_results: int = 5


class RescheduleRequest(BaseModel):
    event_id: str
    calendar_id: str = "primary"
    new_start: datetime
    new_end: datetime


class CreateEventRequest(BaseModel):
    calendar_id: str = "primary"
    summary: str
    start: datetime
    end: datetime
    attendees: Optional[List[str]] = None
    description: str = ""


def _get_calendar_service(request: Request):
    service = getattr(request.app.state, "calendar_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Calendar service is not configured")
    return service


@router.post("/conflicts")
async def scan_conflicts(payload: ConflictScanRequest, request: Request) -> dict:
    """Scan for conflicting events on a calendar."""
    import asyncio
    from g3lobster.calendar.conflict_detector import detect_conflicts

    service = _get_calendar_service(request)
    now = datetime.now(tz=timezone.utc)
    time_max = now + timedelta(hours=payload.hours_ahead)
    conflicts = await asyncio.to_thread(
        detect_conflicts, service, payload.calendar_id, now, time_max,
    )
    return {
        "calendar_id": payload.calendar_id,
        "time_range": {"min": now.isoformat(), "max": time_max.isoformat()},
        "conflicts": [c.model_dump(mode="json") for c in conflicts],
        "count": len(conflicts),
    }


@router.post("/find-slots")
async def find_slots(payload: FindSlotsRequest, request: Request) -> dict:
    """Find common free slots across multiple attendees."""
    import asyncio
    from g3lobster.calendar.scheduler import find_common_slots

    service = _get_calendar_service(request)
    now = datetime.now(tz=timezone.utc)
    time_max = now + timedelta(days=payload.days_ahead)
    slots = await asyncio.to_thread(
        find_common_slots,
        service,
        payload.attendee_emails,
        now,
        time_max,
        payload.duration_minutes,
        payload.max_results,
    )
    return {
        "attendees": payload.attendee_emails,
        "duration_minutes": payload.duration_minutes,
        "slots": [s.model_dump(mode="json") for s in slots],
        "count": len(slots),
    }


@router.post("/reschedule")
async def reschedule_event(payload: RescheduleRequest, request: Request) -> dict:
    """Reschedule an existing calendar event."""
    import asyncio
    from g3lobster.calendar.actions import reschedule_event as _reschedule

    service = _get_calendar_service(request)
    updated = await asyncio.to_thread(
        _reschedule, service, payload.event_id, payload.calendar_id,
        payload.new_start, payload.new_end,
    )
    return {"status": "rescheduled", "event": updated}


@router.post("/create-event")
async def create_event(payload: CreateEventRequest, request: Request) -> dict:
    """Create a new calendar event."""
    import asyncio
    from g3lobster.calendar.actions import create_event as _create

    service = _get_calendar_service(request)
    created = await asyncio.to_thread(
        _create, service, payload.calendar_id, payload.summary,
        payload.start, payload.end, payload.attendees, payload.description,
    )
    return {"status": "created", "event": created}
