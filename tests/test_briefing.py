"""Unit tests for the morning briefing module."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from g3lobster.briefing.formatter import format_briefing, _short_time, _short_sender
from g3lobster.briefing.gather import (
    fetch_calendar_events,
    fetch_priority_emails,
    fetch_unread_mentions,
)
from g3lobster.chat.dm import send_dm


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------

class TestFormatter:
    def test_format_briefing_empty(self):
        result = format_briefing([], [], [])
        assert "Today's Schedule" in result
        assert "Priority Inbox" in result
        assert "Unread Mentions" in result
        assert "No meetings" in result
        assert "Inbox zero" in result
        assert "No new mentions" in result

    def test_format_briefing_with_data(self):
        events = [
            {"summary": "Standup", "start": "2026-03-11T09:00:00Z", "end": "2026-03-11T09:15:00Z", "location": "", "meet_link": ""},
            {"summary": "1:1 with PM", "start": "2026-03-11T14:00:00Z", "end": "2026-03-11T14:30:00Z", "location": "Room 42", "meet_link": "https://meet.google.com/abc"},
        ]
        emails = [
            {"subject": "Q1 Review", "sender": "Jane Doe <jane@example.com>", "snippet": "Please review...", "date": "2026-03-11"},
        ]
        mentions = [
            {"space": "spaces/123", "sender": "Bob", "text": "Hey @user check this out", "create_time": "2026-03-11T08:00:00Z"},
        ]

        result = format_briefing(events, emails, mentions)
        assert "Standup" in result
        assert "1:1 with PM" in result
        assert "Room 42" in result
        assert "Meet" in result
        assert "Q1 Review" in result
        assert "Jane Doe" in result
        assert "Bob" in result

    def test_format_briefing_truncation(self):
        # Create a lot of data to test truncation
        events = [{"summary": f"Meeting {i}" * 10, "start": "2026-03-11T09:00:00Z", "end": "2026-03-11T10:00:00Z", "location": "A" * 50, "meet_link": ""} for i in range(20)]
        result = format_briefing(events, [], [])
        assert len(result) <= 1903  # _MAX_CHARS + "..."

    def test_short_time_iso(self):
        assert _short_time("2026-03-11T09:30:00Z") == "09:30"

    def test_short_time_date_only(self):
        assert _short_time("2026-03-11") == "2026-03-11"

    def test_short_time_empty(self):
        assert _short_time("") == "??:??"

    def test_short_sender_with_email(self):
        assert _short_sender("Jane Doe <jane@example.com>") == "Jane Doe"

    def test_short_sender_plain(self):
        assert _short_sender("jane@example.com") == "jane@example.com"


# ---------------------------------------------------------------------------
# Gather tests (mocked API calls)
# ---------------------------------------------------------------------------

class TestGatherCalendar:
    @pytest.mark.asyncio
    async def test_fetch_calendar_events_success(self):
        mock_service = MagicMock()
        mock_execute = MagicMock(return_value={
            "items": [
                {
                    "summary": "Team Standup",
                    "start": {"dateTime": "2026-03-11T09:00:00Z"},
                    "end": {"dateTime": "2026-03-11T09:15:00Z"},
                    "location": "Room A",
                    "hangoutLink": "https://meet.google.com/abc",
                },
            ]
        })
        mock_service.events().list().execute = mock_execute

        events = await fetch_calendar_events(mock_service)
        assert len(events) == 1
        assert events[0]["summary"] == "Team Standup"
        assert events[0]["meet_link"] == "https://meet.google.com/abc"

    @pytest.mark.asyncio
    async def test_fetch_calendar_events_failure(self):
        mock_service = MagicMock()
        mock_service.events().list().execute.side_effect = Exception("API error")

        events = await fetch_calendar_events(mock_service)
        assert events == []


class TestGatherEmails:
    @pytest.mark.asyncio
    async def test_fetch_priority_emails_empty(self):
        mock_service = MagicMock()
        mock_service.users().messages().list().execute = MagicMock(return_value={"messages": []})

        emails = await fetch_priority_emails(mock_service)
        assert emails == []


class TestGatherMentions:
    @pytest.mark.asyncio
    async def test_fetch_unread_mentions_no_spaces(self):
        mock_service = MagicMock()
        mentions = await fetch_unread_mentions(mock_service, spaces=[], user_id="user@test.com")
        assert mentions == []


# ---------------------------------------------------------------------------
# DM helper tests
# ---------------------------------------------------------------------------

class TestSendDm:
    @pytest.mark.asyncio
    async def test_send_dm_find_existing(self):
        mock_service = MagicMock()
        mock_service.spaces().findDirectMessage().execute = MagicMock(
            return_value={"name": "spaces/dm-123"}
        )
        mock_service.spaces().messages().create().execute = MagicMock(
            return_value={"name": "spaces/dm-123/messages/456"}
        )

        result = await send_dm(mock_service, "user@example.com", "Hello!")
        assert result["name"] == "spaces/dm-123/messages/456"


# ---------------------------------------------------------------------------
# CronTask dm_target field
# ---------------------------------------------------------------------------

class TestCronTaskDmTarget:
    def test_dm_target_field_exists(self):
        from g3lobster.cron.store import CronTask
        task = CronTask(
            id="test-1",
            agent_id="morning-briefing",
            schedule="0 7 * * 1-5",
            instruction="Generate briefing",
            dm_target="user@example.com",
        )
        assert task.dm_target == "user@example.com"

    def test_dm_target_default_none(self):
        from g3lobster.cron.store import CronTask
        task = CronTask(
            id="test-2",
            agent_id="morning-briefing",
            schedule="0 7 * * 1-5",
            instruction="Generate briefing",
        )
        assert task.dm_target is None


# ---------------------------------------------------------------------------
# CalendarConfig
# ---------------------------------------------------------------------------

class TestCalendarConfig:
    def test_calendar_config_exists(self):
        from g3lobster.config import CalendarConfig
        cfg = CalendarConfig()
        assert cfg.enabled is False
        assert cfg.auth_data_dir == ""

    def test_app_config_has_calendar(self):
        from g3lobster.config import AppConfig
        app = AppConfig()
        assert hasattr(app, "calendar")
        assert app.calendar.enabled is False
