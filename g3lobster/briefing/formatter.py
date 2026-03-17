"""Format gathered briefing data into a plaintext markdown digest.

Keeps the output under ~2000 characters so it fits within Google Chat's
single-message limit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

_MAX_CHARS = 1900  # leave headroom for Chat overhead


def format_briefing(
    events: List[Dict[str, Any]],
    emails: List[Dict[str, Any]],
    mentions: List[Dict[str, Any]],
    target_date: Optional[datetime] = None,
) -> str:
    """Render the three data sources into a concise daily digest."""
    parts: list[str] = []
    dt = target_date or datetime.now(tz=timezone.utc)
    date_str = dt.strftime("%A, %B %d, %Y")
    parts.append(f"*Morning Briefing — {date_str}*\n")

    # --- Schedule ---
    parts.append("*Today's Schedule*")
    if events:
        for ev in events[:8]:
            start = _short_time(ev.get("start", ""))
            end = _short_time(ev.get("end", ""))
            summary = ev.get("summary", "(no title)")
            location = ev.get("location", "")
            meet = ev.get("meet_link", "")
            line = f"• {start}–{end}  {summary}"
            if location:
                line += f"  📍 {location}"
            if meet:
                line += f"  🔗 Meet"
            parts.append(line)
    else:
        parts.append("• No meetings today — focus time!")

    parts.append("")

    # --- Priority Inbox ---
    parts.append("*Priority Inbox*")
    if emails:
        for em in emails[:5]:
            sender = _short_sender(em.get("sender", ""))
            subject = em.get("subject", "(no subject)")
            parts.append(f"• {sender}: {subject}")
    else:
        parts.append("• Inbox zero — nice!")

    parts.append("")

    # --- Mentions ---
    parts.append("*Unread Mentions*")
    if mentions:
        for m in mentions[:5]:
            sender = m.get("sender", "unknown")
            text = m.get("text", "")[:80]
            parts.append(f"• {sender}: {text}")
    else:
        parts.append("• No new mentions")

    result = "\n".join(parts)

    # Truncate if necessary
    if len(result) > _MAX_CHARS:
        result = result[:_MAX_CHARS - 3] + "..."

    return result


def _short_time(iso_str: str) -> str:
    """Extract HH:MM from an ISO datetime string, or return the date as-is."""
    if not iso_str:
        return "??:??"
    # All-day events use date-only strings like "2024-01-15" (no 'T')
    if "T" not in iso_str:
        return iso_str
    try:
        # Replace trailing Z with +00:00 for fromisoformat compatibility
        normalized = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%H:%M")
    except ValueError:
        return iso_str


def _short_sender(sender: str) -> str:
    """Shorten 'Jane Doe <jane@example.com>' to 'Jane Doe'."""
    if "<" in sender:
        return sender[: sender.index("<")].strip()
    return sender
