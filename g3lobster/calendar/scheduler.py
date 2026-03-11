"""Multi-person scheduling via Google Calendar FreeBusy API."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from g3lobster.calendar.types import FreeBusySlot, SchedulingProposal, TimeSlot

logger = logging.getLogger(__name__)


def find_common_slots(
    service,
    attendee_emails: List[str],
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    duration_minutes: int = 30,
    max_results: int = 5,
) -> List[SchedulingProposal]:
    """Find common free time slots for multiple attendees.

    Uses the freebusy().query() API to check availability across calendars
    and returns proposed meeting slots.
    """
    now = datetime.now(tz=timezone.utc)
    if time_min is None:
        time_min = now
    if time_max is None:
        time_max = now + timedelta(days=7)

    body = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "items": [{"id": email} for email in attendee_emails],
    }

    result = service.freebusy().query(body=body).execute()

    # Collect all busy periods across all attendees
    all_busy: List[FreeBusySlot] = []
    for email in attendee_emails:
        cal_info = result.get("calendars", {}).get(email, {})
        for busy in cal_info.get("busy", []):
            start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00"))
            all_busy.append(FreeBusySlot(start=start, end=end))

    # Sort busy periods by start time
    all_busy.sort(key=lambda s: s.start)

    # Merge overlapping busy periods
    merged: List[FreeBusySlot] = []
    for slot in all_busy:
        if merged and slot.start <= merged[-1].end:
            merged[-1] = FreeBusySlot(
                start=merged[-1].start,
                end=max(merged[-1].end, slot.end),
            )
        else:
            merged.append(slot)

    # Find free slots between busy periods
    duration = timedelta(minutes=duration_minutes)
    proposals: List[SchedulingProposal] = []
    check_start = time_min

    for busy in merged:
        if busy.start - check_start >= duration:
            slot = TimeSlot(
                start=check_start,
                end=check_start + duration,
                duration_minutes=float(duration_minutes),
            )
            proposals.append(SchedulingProposal(
                slot=slot,
                attendees=list(attendee_emails),
            ))
            if len(proposals) >= max_results:
                break
        check_start = max(check_start, busy.end)

    # Check for a slot after the last busy period
    if len(proposals) < max_results and time_max - check_start >= duration:
        slot = TimeSlot(
            start=check_start,
            end=check_start + duration,
            duration_minutes=float(duration_minutes),
        )
        proposals.append(SchedulingProposal(
            slot=slot,
            attendees=list(attendee_emails),
        ))

    logger.info(
        "Found %d available slots for %d attendees",
        len(proposals), len(attendee_emails),
    )
    return proposals
