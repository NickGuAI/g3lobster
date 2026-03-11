"""REST endpoints for calendar conflict detection and scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/calendar", tags=["calendar"])


class ConflictScanRequest(BaseModel):
    calendar_id: str = "primary"
    days_ahead: int = 7


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
    attendees: List[str] = []
    start: datetime
    end: datetime
    description: Optional[str] = None


def _get_calendar_service(request: Request):
    """Get or create the calendar service from app state."""
    service = getattr(request.app.state, "calendar_service", None)
    if service is None:
        auth_dir = getattr(request.app.state, "chat_auth_dir", None)
        try:
            from g3lobster.calendar.client import get_calendar_service
            service = get_calendar_service(auth_dir)
            request.app.state.calendar_service = service
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Calendar service unavailable: {exc}",
            )
    return service


@router.get("/conflicts")
async def scan_conflicts(
    request: Request,
    calendar_id: str = "primary",
    days_ahead: int = 7,
) -> dict:
    """Scan for scheduling conflicts on a calendar."""
    from g3lobster.calendar.conflict_detector import detect_conflicts

    service = _get_calendar_service(request)
    now = datetime.now(tz=timezone.utc)
    conflicts = detect_conflicts(
        service,
        calendar_id=calendar_id,
        time_min=now,
        time_max=now + timedelta(days=days_ahead),
    )
    return {
        "conflicts": [c.model_dump(mode="json") for c in conflicts],
        "count": len(conflicts),
        "scanned_range": {
            "start": now.isoformat(),
            "end": (now + timedelta(days=days_ahead)).isoformat(),
        },
    }


@router.post("/find-slots")
async def find_slots(payload: FindSlotsRequest, request: Request) -> dict:
    """Find available meeting slots for multiple attendees."""
    from g3lobster.calendar.scheduler import find_common_slots

    if not payload.attendee_emails:
        raise HTTPException(status_code=400, detail="At least one attendee email required")

    service = _get_calendar_service(request)
    now = datetime.now(tz=timezone.utc)
    proposals = find_common_slots(
        service,
        attendee_emails=payload.attendee_emails,
        time_min=now,
        time_max=now + timedelta(days=payload.days_ahead),
        duration_minutes=payload.duration_minutes,
        max_results=payload.max_results,
    )
    return {
        "proposals": [p.model_dump(mode="json") for p in proposals],
        "count": len(proposals),
    }


@router.post("/reschedule")
async def reschedule_event(payload: RescheduleRequest, request: Request) -> dict:
    """Reschedule an existing calendar event."""
    from g3lobster.calendar.actions import reschedule_event as do_reschedule

    service = _get_calendar_service(request)
    updated = do_reschedule(
        service,
        event_id=payload.event_id,
        calendar_id=payload.calendar_id,
        new_start=payload.new_start,
        new_end=payload.new_end,
    )
    return {"updated": True, "event_id": updated.get("id"), "html_link": updated.get("htmlLink", "")}


@router.post("/create-event")
async def create_calendar_event(payload: CreateEventRequest, request: Request) -> dict:
    """Create a new calendar event."""
    from g3lobster.calendar.actions import create_event as do_create

    service = _get_calendar_service(request)
    created = do_create(
        service,
        calendar_id=payload.calendar_id,
        summary=payload.summary,
        attendees=payload.attendees,
        start=payload.start,
        end=payload.end,
        description=payload.description,
    )
    return {"created": True, "event_id": created.get("id"), "html_link": created.get("htmlLink", "")}
