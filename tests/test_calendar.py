"""Tests for g3lobster.calendar modules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from g3lobster.calendar.types import (
    CalendarEvent,
    ConflictPair,
    FreeBusySlot,
    SchedulingProposal,
    TimeSlot,
)
from g3lobster.calendar.conflict_detector import (
    detect_conflicts,
    _parse_event_time,
    _event_to_model,
)
from g3lobster.calendar.scheduler import find_common_slots
from g3lobster.calendar.actions import reschedule_event, create_event
from g3lobster.calendar.cron_scanner import (
    format_conflict_notification,
    format_scheduling_proposal,
    scan_and_format,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(event_id: str, summary: str, start: datetime, end: datetime, attendees=None):
    """Build a Google Calendar API event dict."""
    evt = {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "status": "confirmed",
        "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
    }
    if attendees:
        evt["attendees"] = [{"email": a} for a in attendees]
    return evt


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class TestCalendarTypes:
    def test_calendar_event_creation(self):
        now = datetime.now(tz=timezone.utc)
        event = CalendarEvent(
            id="evt1",
            summary="Test Meeting",
            start=now,
            end=now + timedelta(hours=1),
        )
        assert event.id == "evt1"
        assert event.summary == "Test Meeting"
        assert event.calendar_id == "primary"
        assert event.attendees == []

    def test_conflict_pair(self):
        now = datetime.now(tz=timezone.utc)
        a = CalendarEvent(id="a", start=now, end=now + timedelta(hours=1))
        b = CalendarEvent(id="b", start=now + timedelta(minutes=30), end=now + timedelta(hours=2))
        pair = ConflictPair(event_a=a, event_b=b, overlap_minutes=30.0)
        assert pair.overlap_minutes == 30.0

    def test_time_slot(self):
        now = datetime.now(tz=timezone.utc)
        slot = TimeSlot(start=now, end=now + timedelta(minutes=30), duration_minutes=30.0)
        assert slot.duration_minutes == 30.0

    def test_scheduling_proposal(self):
        now = datetime.now(tz=timezone.utc)
        slot = TimeSlot(start=now, end=now + timedelta(minutes=30), duration_minutes=30.0)
        proposal = SchedulingProposal(slot=slot, attendees=["a@example.com", "b@example.com"])
        assert len(proposal.attendees) == 2
        assert proposal.conflicts == 0


# ---------------------------------------------------------------------------
# Conflict Detector
# ---------------------------------------------------------------------------

class TestConflictDetector:
    def test_parse_event_time_datetime(self):
        dt = _parse_event_time({"start": {"dateTime": "2026-03-10T10:00:00+00:00"}}, "start")
        assert dt is not None
        assert dt.hour == 10

    def test_parse_event_time_date_only(self):
        dt = _parse_event_time({"start": {"date": "2026-03-10"}}, "start")
        assert dt is not None
        assert dt.day == 10

    def test_parse_event_time_missing(self):
        dt = _parse_event_time({}, "start")
        assert dt is None

    def test_event_to_model(self):
        now = datetime.now(tz=timezone.utc)
        raw = _make_event("e1", "Test", now, now + timedelta(hours=1))
        model = _event_to_model(raw, "primary")
        assert model is not None
        assert model.id == "e1"
        assert model.summary == "Test"

    def test_no_events_no_conflicts(self):
        service = MagicMock()
        service.events().list().execute.return_value = {"items": []}
        result = detect_conflicts(service)
        assert result == []

    def test_detect_no_conflicts(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().list().execute.return_value = {
            "items": [
                _make_event("e1", "Meeting A", now, now + timedelta(hours=1)),
                _make_event("e2", "Meeting B", now + timedelta(hours=2), now + timedelta(hours=3)),
            ]
        }
        conflicts = detect_conflicts(service, "primary", now, now + timedelta(hours=8))
        assert len(conflicts) == 0

    def test_detect_overlap(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().list().execute.return_value = {
            "items": [
                _make_event("e1", "Meeting A", now, now + timedelta(hours=1)),
                _make_event("e2", "Meeting B", now + timedelta(minutes=30), now + timedelta(hours=2)),
            ]
        }
        conflicts = detect_conflicts(service, "primary", now, now + timedelta(hours=8))
        assert len(conflicts) == 1
        assert conflicts[0].overlap_minutes == 30.0
        assert conflicts[0].event_a.id == "e1"
        assert conflicts[0].event_b.id == "e2"

    def test_detect_multiple_conflicts(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().list().execute.return_value = {
            "items": [
                _make_event("e1", "A", now, now + timedelta(hours=2)),
                _make_event("e2", "B", now + timedelta(minutes=30), now + timedelta(hours=1, minutes=30)),
                _make_event("e3", "C", now + timedelta(hours=1), now + timedelta(hours=3)),
            ]
        }
        conflicts = detect_conflicts(service, "primary", now, now + timedelta(hours=8))
        # e1 overlaps with e2, e1 overlaps with e3, e2 overlaps with e3
        assert len(conflicts) == 3

    def test_cancelled_events_skipped(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().list().execute.return_value = {
            "items": [
                _make_event("e1", "A", now, now + timedelta(hours=1)),
                {**_make_event("e2", "B", now + timedelta(minutes=30), now + timedelta(hours=2)), "status": "cancelled"},
            ]
        }
        conflicts = detect_conflicts(service, "primary", now, now + timedelta(hours=8))
        assert len(conflicts) == 0


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class TestScheduler:
    def test_find_slots_all_free(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        time_max = now + timedelta(days=1)
        service = MagicMock()
        service.freebusy().query().execute.return_value = {
            "calendars": {
                "alice@example.com": {"busy": []},
                "bob@example.com": {"busy": []},
            }
        }
        slots = find_common_slots(
            service,
            attendee_emails=["alice@example.com", "bob@example.com"],
            time_min=now,
            time_max=time_max,
            duration_minutes=30,
            max_results=3,
        )
        assert len(slots) >= 1
        assert slots[0].slot.duration_minutes == 30.0
        assert slots[0].attendees == ["alice@example.com", "bob@example.com"]

    def test_find_slots_with_busy(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        time_max = now + timedelta(hours=4)
        service = MagicMock()
        service.freebusy().query().execute.return_value = {
            "calendars": {
                "alice@example.com": {
                    "busy": [
                        {"start": now.isoformat(), "end": (now + timedelta(hours=2)).isoformat()},
                    ]
                },
                "bob@example.com": {"busy": []},
            }
        }
        slots = find_common_slots(
            service,
            attendee_emails=["alice@example.com", "bob@example.com"],
            time_min=now,
            time_max=time_max,
            duration_minutes=30,
            max_results=3,
        )
        # Should find a slot after Alice's busy period
        assert len(slots) >= 1
        assert slots[0].slot.start >= now + timedelta(hours=2)

    def test_find_slots_with_busy_periods_multi_attendee(self):
        now = datetime(2025, 1, 15, 9, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.freebusy().query().execute.return_value = {
            "calendars": {
                "alice@example.com": {
                    "busy": [
                        {"start": now.isoformat(), "end": (now + timedelta(hours=1)).isoformat()},
                    ]
                },
                "bob@example.com": {
                    "busy": [
                        {"start": (now + timedelta(hours=2)).isoformat(), "end": (now + timedelta(hours=3)).isoformat()},
                    ]
                },
            }
        }
        result = find_common_slots(
            service,
            attendee_emails=["alice@example.com", "bob@example.com"],
            time_min=now,
            time_max=now + timedelta(hours=8),
            duration_minutes=30,
            max_results=5,
        )
        # Should find slots between busy periods
        assert len(result) >= 1
        # First slot should be after Alice's meeting ends at 10am
        assert result[0].slot.start >= now + timedelta(hours=1)

    def test_find_slots_no_availability(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        time_max = now + timedelta(minutes=30)
        service = MagicMock()
        service.freebusy().query().execute.return_value = {
            "calendars": {
                "alice@example.com": {
                    "busy": [{"start": now.isoformat(), "end": time_max.isoformat()}]
                },
            }
        }
        slots = find_common_slots(
            service,
            attendee_emails=["alice@example.com"],
            time_min=now,
            time_max=time_max,
            duration_minutes=30,
            max_results=3,
        )
        assert len(slots) == 0


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_reschedule_event(self):
        now = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        new_start = now + timedelta(hours=2)
        new_end = new_start + timedelta(hours=1)
        service = MagicMock()
        service.events().patch().execute.return_value = {
            "id": "evt1",
            "htmlLink": "https://calendar.google.com/event?eid=evt1",
        }

        result = reschedule_event(service, "evt1", "primary", new_start, new_end)
        assert result["id"] == "evt1"
        service.events().patch.assert_called()

    def test_create_event(self):
        now = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().insert().execute.return_value = {"id": "new1", "summary": "Team Sync"}

        result = create_event(
            service, "primary", "Team Sync",
            attendees=["alice@example.com"],
            start=now, end=now + timedelta(hours=1),
        )
        assert result["id"] == "new1"
        service.events().insert.assert_called()

    def test_create_event_no_attendees(self):
        now = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().insert().execute.return_value = {"id": "new2"}

        result = create_event(service, "primary", "Solo Work", start=now, end=now + timedelta(hours=1))
        assert result["id"] == "new2"

    def test_create_event_with_description(self):
        now = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().insert().execute.return_value = {"id": "evt-2"}

        create_event(
            service, "primary", "Team Sync",
            attendees=[],
            start=now, end=now + timedelta(hours=1),
            description="Weekly sync meeting",
        )
        service.events().insert.assert_called()


# ---------------------------------------------------------------------------
# Cron Scanner
# ---------------------------------------------------------------------------

class TestCronScanner:
    def test_format_conflict_notification(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        conflicts = [
            ConflictPair(
                event_a=CalendarEvent(id="a", summary="Standup", start=now, end=now + timedelta(minutes=30)),
                event_b=CalendarEvent(id="b", summary="1:1 with Alex", start=now + timedelta(minutes=15), end=now + timedelta(hours=1)),
                overlap_minutes=15.0,
            )
        ]
        text = format_conflict_notification(conflicts)
        assert "Calendar Conflicts Detected" in text
        assert "Standup" in text
        assert "1:1 with Alex" in text
        assert "15 min overlap" in text

    def test_format_scheduling_proposal(self):
        now = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        slots = [
            TimeSlot(start=now, end=now + timedelta(minutes=30), duration_minutes=30.0),
            TimeSlot(start=now + timedelta(hours=2), end=now + timedelta(hours=2, minutes=30), duration_minutes=30.0),
        ]
        text = format_scheduling_proposal(slots, ["alice@example.com", "bob@example.com"], 30)
        assert "Available slots" in text
        assert "alice@example.com" in text

    def test_scan_and_format_no_conflicts(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().list().execute.return_value = {"items": []}
        result = scan_and_format(service, "primary", 8.0)
        assert result is None

    def test_scan_and_format_with_conflicts(self):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        service = MagicMock()
        service.events().list().execute.return_value = {
            "items": [
                _make_event("e1", "A", now, now + timedelta(hours=1)),
                _make_event("e2", "B", now + timedelta(minutes=30), now + timedelta(hours=2)),
            ]
        }
        result = scan_and_format(service, "primary", 8.0)
        assert result is not None
        assert "Calendar Conflicts Detected" in result
