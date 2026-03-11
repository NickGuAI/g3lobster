"""Detect overlapping events on a Google Calendar."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from g3lobster.calendar.types import CalendarEvent, ConflictPair

logger = logging.getLogger(__name__)


def _parse_event_time(event: dict, field: str) -> Optional[datetime]:
    """Extract a datetime from a Google Calendar event's start/end dict."""
    time_info = event.get(field, {})
    dt_str = time_info.get("dateTime")
    if dt_str:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    # All-day events use 'date' instead of 'dateTime'
    date_str = time_info.get("date")
    if date_str:
        return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    return None


def _event_to_model(event: dict, calendar_id: str = "primary") -> Optional[CalendarEvent]:
    """Convert a raw Google Calendar API event dict to a CalendarEvent model."""
    start = _parse_event_time(event, "start")
    end = _parse_event_time(event, "end")
    if not start or not end:
        return None
    attendees = [
        a.get("email", "")
        for a in event.get("attendees", [])
        if a.get("email")
    ]
    return CalendarEvent(
        id=event.get("id", ""),
        summary=event.get("summary", "(no title)"),
        start=start,
        end=end,
        calendar_id=calendar_id,
        attendees=attendees,
        html_link=event.get("htmlLink", ""),
    )


def detect_conflicts(
    service,
    calendar_id: str = "primary",
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
) -> List[ConflictPair]:
    """Scan a calendar for overlapping events in a time range.

    Uses ``events().list()`` API, sorts by start time, and detects overlaps
    via interval comparison.
    """
    if time_min is None:
        time_min = datetime.now(tz=timezone.utc)
    if time_max is None:
        from datetime import timedelta
        time_max = time_min + timedelta(days=1)

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    raw_events = events_result.get("items", [])
    events = []
    for raw in raw_events:
        # Skip cancelled or all-day events for conflict detection
        if raw.get("status") == "cancelled":
            continue
        model = _event_to_model(raw, calendar_id)
        if model:
            events.append(model)

    # Sort by start time
    events.sort(key=lambda e: e.start)

    conflicts: List[ConflictPair] = []
    for i in range(len(events)):
        for j in range(i + 1, len(events)):
            if events[j].start < events[i].end:
                overlap = min(events[i].end, events[j].end) - events[j].start
                overlap_minutes = overlap.total_seconds() / 60.0
                conflicts.append(ConflictPair(
                    event_a=events[i],
                    event_b=events[j],
                    overlap_minutes=round(overlap_minutes, 1),
                ))
            else:
                break  # No more overlaps possible with event i
    return conflicts
