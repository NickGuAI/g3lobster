"""Multi-person scheduling via Google Calendar FreeBusy API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from g3lobster.calendar.types import FreeBusySlot, TimeSlot

logger = logging.getLogger(__name__)


def find_common_slots(
    service,
    attendee_emails: List[str],
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    duration_minutes: int = 30,
    max_results: int = 5,
) -> List[TimeSlot]:
    """Find common free time slots across multiple calendars.

    Uses the ``freebusy().query()`` API to find times when all attendees
    are available.
    """
    if time_min is None:
        time_min = datetime.now(tz=timezone.utc)
    if time_max is None:
        time_max = time_min + timedelta(days=7)

    # Query freebusy for all attendees
    body = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "items": [{"id": email} for email in attendee_emails],
    }
    result = service.freebusy().query(body=body).execute()

    # Collect all busy slots across all attendees
    all_busy: List[FreeBusySlot] = []
    calendars = result.get("calendars", {})
    for email in attendee_emails:
        cal_info = calendars.get(email, {})
        for busy in cal_info.get("busy", []):
            start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00"))
            all_busy.append(FreeBusySlot(start=start, end=end))

    # Sort busy slots by start time
    all_busy.sort(key=lambda s: s.start)

    # Merge overlapping busy slots
    merged: List[FreeBusySlot] = []
    for slot in all_busy:
        if merged and slot.start <= merged[-1].end:
            merged[-1] = FreeBusySlot(
                start=merged[-1].start,
                end=max(merged[-1].end, slot.end),
            )
        else:
            merged.append(slot)

    # Find gaps that are at least duration_minutes long
    duration = timedelta(minutes=duration_minutes)
    free_slots: List[TimeSlot] = []

    # Check gap before first busy slot
    cursor = time_min
    for busy in merged:
        if busy.start - cursor >= duration:
            free_slots.append(TimeSlot(
                start=cursor,
                end=busy.start,
                duration_minutes=(busy.start - cursor).total_seconds() / 60.0,
            ))
        cursor = max(cursor, busy.end)

    # Check gap after last busy slot
    if time_max - cursor >= duration:
        free_slots.append(TimeSlot(
            start=cursor,
            end=time_max,
            duration_minutes=(time_max - cursor).total_seconds() / 60.0,
        ))

    # Trim slots to requested duration and limit results
    trimmed: List[TimeSlot] = []
    for slot in free_slots:
        if len(trimmed) >= max_results:
            break
        trimmed.append(TimeSlot(
            start=slot.start,
            end=slot.start + duration,
            duration_minutes=float(duration_minutes),
        ))

    return trimmed
