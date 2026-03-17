"""Orchestrate a full morning briefing: authenticate → gather → format → send DM.

This module is the main entry point invoked by cron tasks or manual triggers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from g3lobster.briefing.formatter import format_briefing
from g3lobster.briefing.gather import (
    fetch_calendar_events,
    fetch_priority_emails,
    fetch_unread_mentions,
)
from g3lobster.chat.dm import send_dm

logger = logging.getLogger(__name__)


def _build_calendar_service(creds):
    """Build a Google Calendar API service from existing credentials."""
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _build_gmail_service(creds):
    """Build a Gmail API service from existing credentials."""
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _build_chat_service(creds):
    """Build a Google Chat API service from existing credentials."""
    from googleapiclient.discovery import build
    return build("chat", "v1", credentials=creds, cache_discovery=False)


def _load_credentials(auth_data_dir: Optional[str] = None):
    """Load OAuth credentials that include Calendar + Gmail + Chat scopes."""
    from g3lobster.chat.auth import _load_saved_credentials
    return _load_saved_credentials(data_dir=auth_data_dir)


async def run_briefing(
    auth_data_dir: Optional[str] = None,
    user_email: Optional[str] = None,
    chat_spaces: Optional[List[str]] = None,
    since_iso: Optional[str] = None,
    target_date: Optional[datetime] = None,
) -> str:
    """Run the full morning briefing pipeline.

    Parameters
    ----------
    auth_data_dir:
        Directory containing OAuth credentials (token.json, credentials.json).
    user_email:
        Email address of the DM recipient. If ``None``, the briefing is
        returned as text but not delivered.
    chat_spaces:
        List of Chat space names to scan for @-mentions. Defaults to empty.
    since_iso:
        ISO-8601 timestamp — only gather emails/mentions newer than this.
    target_date:
        Target date for calendar events. Defaults to today (UTC).

    Returns
    -------
    str
        The formatted briefing text.
    """
    creds = await asyncio.to_thread(_load_credentials, auth_data_dir)

    # Build service clients concurrently
    calendar_svc, gmail_svc, chat_svc = await asyncio.gather(
        asyncio.to_thread(_build_calendar_service, creds),
        asyncio.to_thread(_build_gmail_service, creds),
        asyncio.to_thread(_build_chat_service, creds),
    )

    # Gather data concurrently
    now = datetime.now(tz=timezone.utc)
    events_coro = fetch_calendar_events(calendar_svc, date=target_date or now)
    emails_coro = fetch_priority_emails(gmail_svc, since_iso=since_iso)
    mentions_coro = fetch_unread_mentions(
        chat_svc,
        spaces=chat_spaces or [],
        user_id=user_email or "",
        since=since_iso,
    )

    events, emails, mentions = await asyncio.gather(
        events_coro, emails_coro, mentions_coro
    )

    briefing_text = format_briefing(events, emails, mentions, target_date=target_date)

    # Deliver via DM if a target email is provided
    if user_email:
        try:
            await send_dm(chat_svc, user_email, briefing_text)
            logger.info("Morning briefing delivered to %s", user_email)
        except Exception:
            logger.exception("Failed to deliver briefing DM to %s", user_email)

    return briefing_text
