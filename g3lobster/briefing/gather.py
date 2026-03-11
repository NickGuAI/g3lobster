"""Data-gathering helpers for the morning briefing.

Each function accepts an authenticated Google API service client and returns
a list of simplified dicts ready for the formatter.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------

async def fetch_calendar_events(
    calendar_service,
    date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return today's events from the user's primary Google Calendar.

    Each event is simplified to::

        {"summary", "start", "end", "location", "meet_link"}
    """
    if date is None:
        date = datetime.now(tz=timezone.utc)

    # Build time range for the target day (midnight to midnight UTC)
    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    try:
        response = await asyncio.to_thread(
            calendar_service.events()
            .list(
                calendarId="primary",
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=25,
            )
            .execute
        )
    except Exception:
        logger.exception("Failed to fetch calendar events")
        return []

    events: List[Dict[str, Any]] = []
    for item in response.get("items", []):
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})
        events.append(
            {
                "summary": item.get("summary", "(no title)"),
                "start": start_raw.get("dateTime") or start_raw.get("date", ""),
                "end": end_raw.get("dateTime") or end_raw.get("date", ""),
                "location": item.get("location", ""),
                "meet_link": item.get("hangoutLink", ""),
            }
        )
    return events


# ---------------------------------------------------------------------------
# Gmail — priority / unread
# ---------------------------------------------------------------------------

async def fetch_priority_emails(
    gmail_service,
    since_iso: Optional[str] = None,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """Return unread important emails since *since_iso*.

    Each email is simplified to::

        {"subject", "sender", "snippet", "date"}
    """
    if since_iso:
        # Gmail search uses epoch seconds
        try:
            dt = datetime.fromisoformat(since_iso)
            after_epoch = int(dt.timestamp())
            query = f"is:unread is:important after:{after_epoch}"
        except ValueError:
            query = "is:unread is:important"
    else:
        query = "is:unread is:important"

    try:
        response = await asyncio.to_thread(
            gmail_service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute
        )
    except Exception:
        logger.exception("Failed to list priority emails")
        return []

    messages = response.get("messages", [])
    emails: List[Dict[str, Any]] = []

    for stub in messages:
        try:
            msg = await asyncio.to_thread(
                gmail_service.users()
                .messages()
                .get(userId="me", id=stub["id"], format="metadata",
                     metadataHeaders=["From", "Subject", "Date"])
                .execute
            )
        except Exception:
            logger.warning("Failed to fetch email %s", stub["id"])
            continue

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        emails.append(
            {
                "subject": headers.get("subject", "(no subject)"),
                "sender": headers.get("from", "unknown"),
                "snippet": msg.get("snippet", ""),
                "date": headers.get("date", ""),
            }
        )
    return emails


# ---------------------------------------------------------------------------
# Google Chat — unread @-mentions
# ---------------------------------------------------------------------------

async def fetch_unread_mentions(
    chat_service,
    spaces: List[str],
    user_id: str,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Scan *spaces* for messages that @-mention *user_id* since *since*.

    Each mention is simplified to::

        {"space", "sender", "text", "create_time"}
    """
    mentions: List[Dict[str, Any]] = []

    for space_name in spaces:
        try:
            response = await asyncio.to_thread(
                chat_service.spaces()
                .messages()
                .list(parent=space_name, pageSize=50)
                .execute
            )
        except Exception:
            logger.warning("Failed to list messages in %s", space_name)
            continue

        for msg in response.get("messages", []):
            create_time = msg.get("createTime", "")
            if since and create_time < since:
                continue

            text = msg.get("text", "")
            # Check if the message mentions the user
            annotations = msg.get("annotations", [])
            mentioned = any(
                ann.get("userMention", {}).get("user", {}).get("name", "") == user_id
                for ann in annotations
            )
            if not mentioned and f"<users/{user_id}>" not in text:
                continue

            sender = msg.get("sender", {}).get("displayName", "unknown")
            mentions.append(
                {
                    "space": space_name,
                    "sender": sender,
                    "text": text[:200],
                    "create_time": create_time,
                }
            )

    return mentions
