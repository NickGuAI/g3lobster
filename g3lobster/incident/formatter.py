"""Text formatting functions for incident cards and summaries (Google Chat markdown)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from g3lobster.incident.model import Incident, IncidentSeverity

_SEVERITY_EMOJI = {
    IncidentSeverity.SEV1: "🚨",
    IncidentSeverity.SEV2: "🔴",
    IncidentSeverity.SEV3: "🟡",
    IncidentSeverity.SEV4: "🔵",
}


def _parse_iso(ts: str) -> Optional[datetime]:
    """Return a timezone-aware datetime from an ISO 8601 string, or None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _hhmm(ts: str) -> str:
    """Return HH:MM from an ISO 8601 timestamp, or the raw string on failure."""
    dt = _parse_iso(ts)
    return dt.strftime("%H:%M") if dt else ts


def format_incident_card(incident: Incident) -> str:
    """Create a structured text card for an incident."""
    emoji = _SEVERITY_EMOJI.get(incident.severity, "")
    severity_label = incident.severity.value.upper()
    lines = [
        f"{emoji} *[{severity_label}] {incident.title}*",
        f"*Status:* {incident.status.value.capitalize()}",
    ]

    if incident.commander:
        lines.append(f"*Commander:* {incident.commander}")

    if incident.roles:
        roles_str = ", ".join(f"{role}: {person}" for role, person in incident.roles.items())
        lines.append(f"*Roles:* {roles_str}")

    lines.append(f"*Timeline entries:* {len(incident.timeline)}")

    open_count = sum(1 for a in incident.actions if a.status == "open")
    total_count = len(incident.actions)
    lines.append(f"*Action items:* {open_count} open / {total_count} total")

    if incident.created_at:
        lines.append(f"*Created at:* {incident.created_at}")

    return "\n".join(lines)


def format_timeline(incident: Incident) -> str:
    """Render a chronological timeline of all entries."""
    if not incident.timeline:
        return f"*Timeline for '{incident.title}'*\n_(no entries yet)_"

    header = f"*Timeline for '{incident.title}'*"
    entries = [
        f"[{_hhmm(e.timestamp)}] ({e.entry_type}) {e.author}: {e.content}"
        for e in incident.timeline
    ]
    return header + "\n" + "\n".join(entries)


def format_resolution_summary(incident: Incident) -> str:
    """Produce a full post-mortem summary for a resolved incident."""
    emoji = _SEVERITY_EMOJI.get(incident.severity, "")
    severity_label = incident.severity.value.upper()
    lines = [f"{emoji} *[{severity_label}] Resolution Summary: {incident.title}*", ""]

    # Duration
    created = _parse_iso(incident.created_at)
    resolved = _parse_iso(incident.resolved_at or "")
    if created and resolved:
        delta = resolved - created
        total_minutes = int(delta.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        duration_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        lines.append(f"*Duration:* {duration_str}  ({incident.created_at} → {incident.resolved_at})")
    elif created:
        lines.append(f"*Created at:* {incident.created_at}")

    # Timeline
    lines.append("")
    lines.append("*Timeline*")
    if incident.timeline:
        for e in incident.timeline:
            lines.append(f"[{_hhmm(e.timestamp)}] ({e.entry_type}) {e.author}: {e.content}")
    else:
        lines.append("_(no entries)_")

    # Action items
    lines.append("")
    lines.append("*Action Items*")
    if incident.actions:
        for a in incident.actions:
            status_icon = "✅" if a.status == "done" else "⬜"
            assignee_part = f" — {a.assignee}" if a.assignee else ""
            lines.append(f"{status_icon} {a.description}{assignee_part}")
    else:
        lines.append("_(none)_")

    # Summary text (last timeline entry of type "note" or a dedicated field if added later)
    summary_entries = [e for e in incident.timeline if e.entry_type == "note"]
    if summary_entries:
        lines.append("")
        lines.append("*Summary*")
        lines.append(summary_entries[-1].content)

    return "\n".join(lines)


def format_status_prompt(incident: Incident, minutes_since_last: int) -> str:
    """Return a prompt asking the commander for a status update."""
    return (
        f"⏰ Active incident: '{incident.title}' — "
        f"last update was {minutes_since_last} minutes ago. "
        "Please provide a status update."
    )
