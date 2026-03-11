"""Cron-driven proactive calendar conflict scanning.

Designed to be invoked as a cron task instruction. When an agent receives
this as a task prompt, it calls the conflict detection API and formats
a user-facing notification.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from g3lobster.calendar.conflict_detector import detect_conflicts
from g3lobster.calendar.types import ConflictPair

logger = logging.getLogger(__name__)

# Default scan window: 8 hours ahead during work hours
DEFAULT_SCAN_HOURS = 8.0


def scan_and_format(
    service,
    calendar_id: str = "primary",
    scan_hours: float = DEFAULT_SCAN_HOURS,
) -> Optional[str]:
    """Scan for conflicts and return a formatted notification string.

    Returns None if no conflicts are found.
    """
    now = datetime.now(tz=timezone.utc)
    time_max = now + timedelta(hours=scan_hours)

    conflicts = detect_conflicts(service, calendar_id, now, time_max)
    if not conflicts:
        return None

    return format_conflict_notification(conflicts)


def format_conflict_notification(conflicts: List[ConflictPair]) -> str:
    """Format conflict pairs into a human-readable Chat notification."""
    lines = [f"⚠️ *Calendar Conflicts Detected* ({len(conflicts)} found):\n"]

    for i, conflict in enumerate(conflicts, 1):
        a = conflict.event_a
        b = conflict.event_b
        overlap = conflict.overlap_minutes
        lines.append(
            f"{i}. *{a.summary}* ({a.start:%H:%M}-{a.end:%H:%M}) "
            f"overlaps with *{b.summary}* ({b.start:%H:%M}-{b.end:%H:%M}) "
            f"— {overlap:.0f} min overlap"
        )

    lines.append(
        "\nReply with a number to reschedule that conflict, "
        "or say 'reschedule <event name> to <time>' for a specific fix."
    )
    return "\n".join(lines)


def format_scheduling_proposal(
    slots,
    attendees: List[str],
    duration_minutes: int,
) -> str:
    """Format proposed meeting slots into a user-facing message."""
    names = ", ".join(attendees)
    lines = [
        f"📅 *Available slots* for a {duration_minutes}-min meeting with {names}:\n"
    ]

    for i, slot in enumerate(slots, 1):
        lines.append(
            f"{i}. {slot.start:%A %b %d, %H:%M} - {slot.end:%H:%M}"
        )

    lines.append("\nReply with a number to book that slot.")
    return "\n".join(lines)
