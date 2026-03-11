"""Tests for the Google Calendar polling bridge."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from g3lobster.chat.calendar_bridge import (
    CalendarBridge,
    MeetingInfo,
    _extract_doc_links,
    _parse_event,
)


# --- Unit tests for _extract_doc_links ---

def test_extract_doc_links_empty():
    assert _extract_doc_links("") == []
    assert _extract_doc_links(None) == []


def test_extract_doc_links_finds_docs():
    text = "See https://docs.google.com/document/d/abc123 and https://docs.google.com/spreadsheets/d/xyz789"
    links = _extract_doc_links(text)
    assert len(links) == 2
    assert "https://docs.google.com/document/d/abc123" in links
    assert "https://docs.google.com/spreadsheets/d/xyz789" in links


# --- Unit tests for _parse_event ---

def _make_event(
    event_id="evt1",
    summary="Team Standup",
    start_dt=None,
    all_day=False,
    declined=False,
    attendees=None,
):
    if start_dt is None:
        start_dt = datetime.now(tz=timezone.utc) + timedelta(minutes=10)

    event = {"id": event_id, "summary": summary, "description": ""}

    if all_day:
        event["start"] = {"date": start_dt.date().isoformat()}
        event["end"] = {"date": start_dt.date().isoformat()}
    else:
        event["start"] = {"dateTime": start_dt.isoformat()}
        event["end"] = {"dateTime": (start_dt + timedelta(hours=1)).isoformat()}

    if attendees:
        event["attendees"] = attendees
    elif declined:
        event["attendees"] = [
            {"email": "me@example.com", "self": True, "responseStatus": "declined"}
        ]

    return event


def test_parse_event_basic():
    event = _make_event()
    meeting = _parse_event(event)
    assert meeting is not None
    assert meeting.event_id == "evt1"
    assert meeting.title == "Team Standup"


def test_parse_event_all_day_skipped():
    event = _make_event(all_day=True)
    assert _parse_event(event) is None


def test_parse_event_declined_skipped():
    event = _make_event(declined=True)
    assert _parse_event(event) is None


def test_parse_event_extracts_attendees():
    attendees = [
        {"email": "alice@example.com"},
        {"email": "me@example.com", "self": True},
        {"email": "bob@example.com"},
    ]
    event = _make_event(attendees=attendees)
    meeting = _parse_event(event)
    assert meeting is not None
    assert "alice@example.com" in meeting.attendees
    assert "bob@example.com" in meeting.attendees
    assert "me@example.com" not in meeting.attendees


def test_parse_event_extracts_meet_link():
    event = _make_event()
    event["hangoutLink"] = "https://meet.google.com/abc-def-ghi"
    meeting = _parse_event(event)
    assert meeting is not None
    assert meeting.meet_link == "https://meet.google.com/abc-def-ghi"


# --- Unit tests for CalendarBridge ---

def test_dedup_key():
    bridge = CalendarBridge(
        service=MagicMock(),
        dedup_path=None,
        auth_data_dir="/tmp/test_cal_auth",
    )
    dt = datetime(2026, 3, 11, 14, 0, tzinfo=timezone.utc)
    key = bridge._dedup_key("evt1", dt)
    assert key == "evt1_2026-03-11"


def test_dedup_persistence(tmp_path):
    dedup_file = tmp_path / "briefed.json"
    bridge = CalendarBridge(
        service=MagicMock(),
        dedup_path=dedup_file,
    )
    dt = datetime(2026, 3, 11, 14, 0, tzinfo=timezone.utc)
    key = bridge._dedup_key("evt1", dt)
    bridge._briefed_events.add(key)
    bridge._save_dedup()

    # Reload
    bridge2 = CalendarBridge(
        service=MagicMock(),
        dedup_path=dedup_file,
    )
    assert key in bridge2._briefed_events


@pytest.mark.asyncio
async def test_poll_once_returns_meetings(tmp_path):
    now = datetime.now(tz=timezone.utc)
    start = now + timedelta(minutes=10)

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [_make_event(start_dt=start)]
    }

    bridge = CalendarBridge(
        service=mock_service,
        lookahead_minutes=15,
        dedup_path=tmp_path / "briefed.json",
    )

    meetings = await bridge._poll_once()
    assert len(meetings) == 1
    assert meetings[0].title == "Team Standup"


@pytest.mark.asyncio
async def test_poll_once_dedup(tmp_path):
    now = datetime.now(tz=timezone.utc)
    start = now + timedelta(minutes=10)

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [_make_event(start_dt=start)]
    }

    bridge = CalendarBridge(
        service=mock_service,
        lookahead_minutes=15,
        dedup_path=tmp_path / "briefed.json",
    )

    meetings1 = await bridge._poll_once()
    meetings2 = await bridge._poll_once()
    assert len(meetings1) == 1
    assert len(meetings2) == 0  # Deduped


@pytest.mark.asyncio
async def test_poll_once_skips_large_attendee_list(tmp_path):
    now = datetime.now(tz=timezone.utc)
    start = now + timedelta(minutes=10)

    attendees = [{"email": f"user{i}@example.com"} for i in range(20)]
    event = _make_event(start_dt=start, attendees=attendees)
    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [event]
    }

    bridge = CalendarBridge(
        service=mock_service,
        max_attendees=15,
        dedup_path=tmp_path / "briefed.json",
    )

    meetings = await bridge._poll_once()
    assert len(meetings) == 0


@pytest.mark.asyncio
async def test_on_meeting_callback(tmp_path):
    now = datetime.now(tz=timezone.utc)
    start = now + timedelta(minutes=10)

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [_make_event(start_dt=start)]
    }

    received = []

    async def on_meeting(meeting):
        received.append(meeting)

    bridge = CalendarBridge(
        service=mock_service,
        dedup_path=tmp_path / "briefed.json",
    )
    bridge.set_on_meeting(on_meeting)

    await bridge._poll_once()
    assert len(received) == 1
    assert received[0].title == "Team Standup"


def test_meeting_info_as_dict():
    dt = datetime(2026, 3, 11, 14, 0, tzinfo=timezone.utc)
    meeting = MeetingInfo(
        event_id="evt1",
        title="Test",
        description="desc",
        start_time=dt,
        attendees=["a@b.com"],
        meet_link="https://meet.google.com/x",
        doc_links=["https://docs.google.com/document/d/abc"],
    )
    d = meeting.as_dict()
    assert d["event_id"] == "evt1"
    assert d["title"] == "Test"
    assert d["attendees"] == ["a@b.com"]
