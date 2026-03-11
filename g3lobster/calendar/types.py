"""Pydantic models for calendar data."""

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
    """Two overlapping calendar events."""
    event_a: CalendarEvent
    event_b: CalendarEvent
    overlap_minutes: float = 0.0


class FreeBusySlot(BaseModel):
    """A busy time range from freebusy query."""
    start: datetime
    end: datetime


class TimeSlot(BaseModel):
    """A proposed available time slot."""
    start: datetime
    end: datetime
    duration_minutes: float


class SchedulingProposal(BaseModel):
    """A proposed meeting time with attendees."""
    slot: TimeSlot
    attendees: List[str]
    summary: str = ""
