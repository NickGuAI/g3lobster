"""Calendar write actions — reschedule and create events."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def reschedule_event(
    service,
    event_id: str,
    calendar_id: str,
    new_start: datetime,
    new_end: datetime,
) -> Dict:
    """Move an existing calendar event to a new time.

    Returns the updated event resource dict from the API.
    """
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    event["start"] = {"dateTime": new_start.isoformat()}
    event["end"] = {"dateTime": new_end.isoformat()}
    updated = service.events().update(
        calendarId=calendar_id,
        eventId=event_id,
        body=event,
    ).execute()
    logger.info("Rescheduled event %s to %s - %s", event_id, new_start, new_end)
    return updated


def create_event(
    service,
    calendar_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    attendees: Optional[List[str]] = None,
    description: str = "",
) -> Dict:
    """Create a new calendar event with optional attendees.

    Returns the created event resource dict from the API.
    """
    body: Dict = {
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees]
    created = service.events().insert(calendarId=calendar_id, body=body).execute()
    logger.info("Created event '%s' with id %s", summary, created.get("id"))
    return created
