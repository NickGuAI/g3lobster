"""Calendar write actions: reschedule and create events."""
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
    """Reschedule an event by updating its start and end times.

    Thin wrapper around events().patch() to preserve other event fields.
    """
    body = {
        "start": {"dateTime": new_start.isoformat()},
        "end": {"dateTime": new_end.isoformat()},
    }
    updated = service.events().patch(
        calendarId=calendar_id,
        eventId=event_id,
        body=body,
    ).execute()
    logger.info("Rescheduled event %s to %s", event_id, new_start.isoformat())
    return updated


def create_event(
    service,
    calendar_id: str,
    summary: str,
    attendees: Optional[List[str]] = None,
    start: datetime = None,
    end: datetime = None,
    description: Optional[str] = None,
) -> Dict:
    """Create a new calendar event with optional attendees.

    Thin wrapper around events().insert().
    """
    body: Dict = {
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees]
    if description:
        body["description"] = description
    created = service.events().insert(
        calendarId=calendar_id,
        body=body,
        sendUpdates="all",
    ).execute()
    logger.info("Created event %s: %s", created.get("id"), summary)
    return created
