"""Calendar conflict resolver: cron-driven scanning and Chat notification.

Integrates with the cron infrastructure to periodically scan for conflicts
and notify the user via the Chat bridge with resolution options.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from g3lobster.calendar.types import ConflictPair, SchedulingProposal

logger = logging.getLogger(__name__)


def format_conflict_notification(conflicts: List["ConflictPair"]) -> str:
    """Format conflict pairs into a human-readable Chat message."""
    if not conflicts:
        return ""

    lines = [f"📅 **Calendar Conflict Alert** — Found {len(conflicts)} conflict(s):\n"]
    for i, conflict in enumerate(conflicts, 1):
        a = conflict.event_a
        b = conflict.event_b
        start_a = a.start.strftime("%I:%M %p")
        end_a = a.end.strftime("%I:%M %p")
        start_b = b.start.strftime("%I:%M %p")
        end_b = b.end.strftime("%I:%M %p")
        date_str = a.start.strftime("%A, %b %d")

        lines.append(
            f"{i}. **{a.summary}** ({start_a}–{end_a}) overlaps with "
            f"**{b.summary}** ({start_b}–{end_b}) on {date_str} "
            f"({conflict.overlap_minutes:.0f} min overlap)"
        )

    lines.append("")
    lines.append(
        "Reply with a number to reschedule that conflict's second event "
        "to your next open slot, or say \"ignore\" to dismiss."
    )
    return "\n".join(lines)


def format_scheduling_proposals(
    proposals: List["SchedulingProposal"],
    attendees: List[str],
) -> str:
    """Format scheduling proposals into a Chat message."""
    if not proposals:
        return "No available slots found for the requested attendees."

    names = ", ".join(attendees)
    lines = [f"📅 **Meeting Scheduling** — Available slots for {names}:\n"]
    for i, proposal in enumerate(proposals, 1):
        slot = proposal.slot
        start = slot.start.strftime("%A, %b %d at %I:%M %p")
        end = slot.end.strftime("%I:%M %p")
        lines.append(f"{i}. {start} – {end} ({slot.duration_minutes:.0f} min)")

    lines.append("")
    lines.append("Reply with a number to create the event, or \"cancel\" to dismiss.")
    return "\n".join(lines)


def build_conflict_scan_instruction(calendar_id: str = "primary", days_ahead: int = 1) -> str:
    """Build the cron task instruction for periodic conflict scanning.

    This instruction is used as the prompt for the conflict-resolver agent
    when triggered by the cron scheduler.
    """
    return (
        f"Scan my calendar ('{calendar_id}') for the next {days_ahead} day(s) "
        f"for scheduling conflicts. If conflicts are found, notify me with details "
        f"and offer to reschedule. If no conflicts, do nothing."
    )


def parse_user_resolution(text: str, num_conflicts: int) -> Optional[int]:
    """Parse user's reply to a conflict notification.

    Returns the 1-based conflict index to resolve, or None if the user
    wants to ignore/cancel.
    """
    text = text.strip().lower()
    if text in ("ignore", "cancel", "dismiss", "no", "skip"):
        return None
    try:
        choice = int(text)
        if 1 <= choice <= num_conflicts:
            return choice
    except ValueError:
        pass
    return None
