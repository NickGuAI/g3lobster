"""Pydantic models for calendar operations."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class CalendarEvent(BaseModel):
    """Represents a Google Calendar event."""
    id: str
    summary: str = ""
    start: datetime
    end: datetime
    calendar_id: str = "primary"
    attendees: List[str] = []
    html_link: str = ""


class ConflictPair(BaseModel):
    """A pair of overlapping events."""
    event_a: CalendarEvent
    event_b: CalendarEvent
    overlap_minutes: float = 0.0


class FreeBusySlot(BaseModel):
    """A busy time slot from the FreeBusy API."""
    start: datetime
    end: datetime


class TimeSlot(BaseModel):
    """An available time slot for scheduling."""
    start: datetime
    end: datetime
    duration_minutes: float


class SchedulingProposal(BaseModel):
    """A proposed meeting time with availability info."""
    slot: TimeSlot
    attendees: List[str] = []
    summary: str = ""
    conflicts: int = 0
