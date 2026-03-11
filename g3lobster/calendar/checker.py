"""Calendar focus-time detection with in-memory caching."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FocusEvent:
    """Represents an active focus-time or OOO calendar event."""
    event_type: str  # "focus" or "ooo"
    end_time: datetime
    summary: str = ""


class FocusTimeChecker:
    """Checks Google Calendar for active focus-time / OOO events.

    Caches results per user with a configurable TTL to avoid redundant API
    calls.  The ``refresh()`` method is intended to be called by a cron job.
    """

    def __init__(
        self,
        calendar_service,
        ttl_s: float = 300.0,
        on_focus_end: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._service = calendar_service
        self._ttl_s = ttl_s
        self._cache: Dict[str, Optional[FocusEvent]] = {}
        self._cache_times: Dict[str, float] = {}
        self._on_focus_end = on_focus_end

    def check_focus_time(self, user_email: str) -> Optional[FocusEvent]:
        """Query Calendar API for current focus/OOO events for *user_email*.

        Returns the active FocusEvent or None.  Uses cache if within TTL.
        """
        now = time.monotonic()
        if user_email in self._cache_times and (now - self._cache_times[user_email]) < self._ttl_s:
            return self._cache.get(user_email)

        event = self._fetch_focus_event(user_email)
        self._cache[user_email] = event
        self._cache_times[user_email] = now
        return event

    def is_in_focus_time(self, user_email: str) -> bool:
        return self.check_focus_time(user_email) is not None

    def get_focus_event(self, user_email: str) -> Optional[FocusEvent]:
        return self.check_focus_time(user_email)

    def refresh(self, user_emails: Optional[List[str]] = None) -> None:
        """Re-check focus state for all monitored users (or a given list).

        Called by the cron scheduler every 5 minutes.  When focus time ends
        for a user with buffered messages, fires the ``on_focus_end`` callback.
        """
        emails = user_emails or list(self._cache.keys())
        for email in emails:
            was_focused = self._cache.get(email) is not None
            event = self._fetch_focus_event(email)
            self._cache[email] = event
            self._cache_times[email] = time.monotonic()

            if was_focused and event is None and self._on_focus_end:
                try:
                    self._on_focus_end(email)
                except Exception:
                    logger.exception("on_focus_end callback failed for %s", email)

    def _fetch_focus_event(self, user_email: str) -> Optional[FocusEvent]:
        """Hit the Calendar API and return the active focus/OOO event, if any."""
        try:
            now = datetime.now(tz=timezone.utc).isoformat()
            events_result = self._service.events().list(
                calendarId=user_email,
                timeMin=now,
                timeMax=now,
                singleEvents=True,
                orderBy="startTime",
                maxResults=10,
            ).execute()

            for item in events_result.get("items", []):
                event_type = item.get("eventType", "")
                transparency = item.get("transparency", "opaque")
                status = item.get("status", "")

                if event_type == "focusTime":
                    end_str = item.get("end", {}).get("dateTime", "")
                    end_time = self._parse_dt(end_str)
                    return FocusEvent(
                        event_type="focus",
                        end_time=end_time,
                        summary=item.get("summary", "Focus Time"),
                    )

                if event_type == "outOfOffice" or status == "tentative":
                    end_str = item.get("end", {}).get("dateTime", "")
                    end_time = self._parse_dt(end_str)
                    return FocusEvent(
                        event_type="ooo",
                        end_time=end_time,
                        summary=item.get("summary", "Out of Office"),
                    )

                # Opaque (busy) events that block time
                if transparency == "opaque" and item.get("summary", "").lower() in (
                    "focus time", "do not disturb", "deep work",
                ):
                    end_str = item.get("end", {}).get("dateTime", "")
                    end_time = self._parse_dt(end_str)
                    return FocusEvent(
                        event_type="focus",
                        end_time=end_time,
                        summary=item.get("summary", "Focus Time"),
                    )

        except Exception:
            logger.exception("Failed to check calendar for %s", user_email)

        return None

    @staticmethod
    def _parse_dt(dt_str: str) -> datetime:
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return datetime.now(tz=timezone.utc)
