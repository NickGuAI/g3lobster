"""Google Calendar polling bridge for meeting prep.

Polls Google Calendar for upcoming meetings within a configurable lookahead
window and triggers meeting prep briefings.

Setup
-----
1. Enable the Google Calendar API (same Google project as Chat/Gmail).
2. Set ``calendar.enabled: true`` in ``config.yaml``.
3. On first run, OAuth credentials are reused from the email auth dir.
   Calendar token is cached at ``{auth_data_dir}/calendar_token.json``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class MeetingInfo:
    """Parsed meeting metadata from a Calendar event."""
    event_id: str
    title: str
    description: str
    start_time: datetime
    attendees: List[str] = field(default_factory=list)
    meet_link: str = ""
    doc_links: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "description": self.description,
            "start_time": self.start_time.isoformat(),
            "attendees": self.attendees,
            "meet_link": self.meet_link,
            "doc_links": self.doc_links,
        }


def _extract_doc_links(text: str) -> List[str]:
    """Extract Google Docs/Sheets/Slides links from text."""
    import re
    pattern = r"https://docs\.google\.com/(?:document|spreadsheets|presentation)/d/[a-zA-Z0-9_-]+"
    return re.findall(pattern, text or "")


def _get_calendar_service(auth_data_dir: Optional[str]):
    """Authenticate and return a Google Calendar API service object."""
    from google.auth.transport.requests import Request  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    from googleapiclient.discovery import build  # type: ignore

    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    token_path = Path(auth_data_dir or ".") / "calendar_token.json"
    creds_path = Path(auth_data_dir or ".") / "credentials.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("calendar", "v3", credentials=creds)


def _parse_event(event: dict) -> Optional[MeetingInfo]:
    """Parse a Google Calendar event into a MeetingInfo, skipping all-day events."""
    start = event.get("start", {})
    # Skip all-day events (they use 'date' instead of 'dateTime')
    if "dateTime" not in start:
        return None

    # Skip declined events
    for attendee in event.get("attendees", []):
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return None

    try:
        start_time = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
    except (ValueError, KeyError):
        return None

    description = event.get("description", "")
    hangout_link = event.get("hangoutLink", "")
    conference = event.get("conferenceData", {})
    meet_link = hangout_link
    if not meet_link:
        for entry in conference.get("entryPoints", []):
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri", "")
                break

    attendee_emails = [
        a["email"]
        for a in event.get("attendees", [])
        if "email" in a and not a.get("self")
    ]

    doc_links = _extract_doc_links(description)

    return MeetingInfo(
        event_id=event["id"],
        title=event.get("summary", "(no title)"),
        description=description,
        start_time=start_time,
        attendees=attendee_emails,
        meet_link=meet_link,
        doc_links=doc_links,
    )


class CalendarBridge:
    """Polls Google Calendar for upcoming meetings and triggers briefing prep."""

    def __init__(
        self,
        lookahead_minutes: int = 15,
        poll_interval_s: float = 300.0,
        max_attendees: int = 15,
        auth_data_dir: Optional[str] = None,
        service=None,
        dedup_path: Optional[Path] = None,
    ) -> None:
        self.lookahead_minutes = lookahead_minutes
        self.poll_interval_s = poll_interval_s
        self.max_attendees = max_attendees
        self.auth_data_dir = auth_data_dir
        self.service = service

        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._briefed_events: Set[str] = set()
        self._on_meeting: Optional[object] = None  # callback

        # Persist dedup state to disk
        self._dedup_path = dedup_path or Path(auth_data_dir or ".") / "calendar_briefed.json"
        self._load_dedup()

    def set_on_meeting(self, callback) -> None:
        """Register a callback: async def callback(meeting: MeetingInfo) -> None."""
        self._on_meeting = callback

    def _load_dedup(self) -> None:
        if self._dedup_path.exists():
            try:
                data = json.loads(self._dedup_path.read_text(encoding="utf-8"))
                self._briefed_events = set(data.get("briefed", []))
            except (json.JSONDecodeError, OSError):
                self._briefed_events = set()

    def _save_dedup(self) -> None:
        self._dedup_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep only last 500 entries to avoid unbounded growth
        entries = list(self._briefed_events)
        if len(entries) > 500:
            entries = entries[-500:]
            self._briefed_events = set(entries)
        self._dedup_path.write_text(
            json.dumps({"briefed": entries}), encoding="utf-8"
        )

    def _dedup_key(self, event_id: str, start_time: datetime) -> str:
        return f"{event_id}_{start_time.date().isoformat()}"

    async def start(self) -> None:
        if self.service is None:
            try:
                self.service = await asyncio.to_thread(
                    _get_calendar_service, self.auth_data_dir
                )
            except Exception:
                logger.exception(
                    "CalendarBridge: failed to authenticate — calendar bridge disabled"
                )
                return

        if self._poll_task and not self._poll_task.done():
            return

        self._stop_event.clear()
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="g3lobster-calendar-poll"
        )
        logger.info("CalendarBridge started (lookahead=%dm)", self.lookahead_minutes)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    @property
    def is_running(self) -> bool:
        return bool(self._poll_task and not self._poll_task.done())

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except Exception:
                logger.exception("CalendarBridge poll error")
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(self.poll_interval_s)

    async def _poll_once(self) -> List[MeetingInfo]:
        """Poll Calendar API for upcoming events and trigger briefings."""
        if not self.service:
            return []

        now = datetime.now(tz=timezone.utc)
        time_max = now + timedelta(minutes=self.lookahead_minutes)

        response = await asyncio.to_thread(
            self.service.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=10,
            )
            .execute
        )

        meetings: List[MeetingInfo] = []
        for event in response.get("items", []):
            meeting = _parse_event(event)
            if meeting is None:
                continue

            dedup_key = self._dedup_key(meeting.event_id, meeting.start_time)
            if dedup_key in self._briefed_events:
                continue

            if len(meeting.attendees) > self.max_attendees:
                logger.info(
                    "CalendarBridge: skipping %r — %d attendees exceeds max %d",
                    meeting.title,
                    len(meeting.attendees),
                    self.max_attendees,
                )
                continue

            self._briefed_events.add(dedup_key)
            self._save_dedup()
            meetings.append(meeting)

            if self._on_meeting:
                try:
                    await self._on_meeting(meeting)
                except Exception:
                    logger.exception(
                        "CalendarBridge: error in on_meeting callback for %r",
                        meeting.title,
                    )

        return meetings
