"""Tests for the meeting prep orchestrator."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from g3lobster.chat.calendar_bridge import MeetingInfo
from g3lobster.meeting_prep.orchestrator import MeetingPrepOrchestrator


def _make_meeting(**kwargs):
    defaults = {
        "event_id": "evt1",
        "title": "Weekly Sync",
        "description": "Discuss project updates",
        "start_time": datetime(2026, 3, 11, 14, 0, tzinfo=timezone.utc),
        "attendees": ["alice@example.com", "bob@example.com"],
        "meet_link": "https://meet.google.com/abc",
        "doc_links": [],
    }
    defaults.update(kwargs)
    return MeetingInfo(**defaults)


@pytest.mark.asyncio
async def test_prepare_basic_briefing():
    orchestrator = MeetingPrepOrchestrator()
    meeting = _make_meeting()

    result = await orchestrator.prepare(meeting)

    assert "## Meeting Briefing: Weekly Sync" in result
    assert "alice@example.com" in result
    assert "bob@example.com" in result
    assert "https://meet.google.com/abc" in result


@pytest.mark.asyncio
async def test_prepare_includes_description():
    orchestrator = MeetingPrepOrchestrator()
    meeting = _make_meeting(description="Review Q1 metrics")

    result = await orchestrator.prepare(meeting)

    assert "Review Q1 metrics" in result
    assert "### Agenda / Description" in result


@pytest.mark.asyncio
async def test_prepare_no_attendees():
    orchestrator = MeetingPrepOrchestrator()
    meeting = _make_meeting(attendees=[])

    result = await orchestrator.prepare(meeting)

    assert "## Meeting Briefing: Weekly Sync" in result
    assert "Attendees" not in result


@pytest.mark.asyncio
async def test_prepare_with_doc_links():
    orchestrator = MeetingPrepOrchestrator()
    meeting = _make_meeting(
        doc_links=["https://docs.google.com/document/d/abc123"]
    )

    result = await orchestrator.prepare(meeting)

    assert "https://docs.google.com/document/d/abc123" in result
    assert "Linked docs" in result


@pytest.mark.asyncio
async def test_prepare_with_memory_search():
    mock_search = MagicMock()
    mock_hit = MagicMock()
    mock_hit.memory_type = "memory"
    mock_hit.snippet = "Alice mentioned the Q1 budget was approved"
    mock_search.search.return_value = [mock_hit]

    orchestrator = MeetingPrepOrchestrator(memory_search=mock_search)
    meeting = _make_meeting()

    result = await orchestrator.prepare(meeting, agent_id="test-agent")

    assert "Relevant Context from Memory" in result
    assert "Q1 budget was approved" in result


@pytest.mark.asyncio
async def test_prepare_with_gmail_service():
    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "msg1"}]
    }
    mock_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Re: Project update"},
                {"name": "From", "value": "alice@example.com"},
                {"name": "Date", "value": "Mon, 10 Mar 2026"},
            ]
        }
    }

    orchestrator = MeetingPrepOrchestrator(email_service=mock_service)
    meeting = _make_meeting()

    result = await orchestrator.prepare(meeting)

    assert "Recent Email Threads" in result
    assert "Re: Project update" in result


@pytest.mark.asyncio
async def test_prepare_empty_meeting():
    """Briefing with minimal meeting info should still produce output."""
    orchestrator = MeetingPrepOrchestrator()
    meeting = _make_meeting(
        title="Quick chat",
        description="",
        attendees=[],
        meet_link="",
        doc_links=[],
    )

    result = await orchestrator.prepare(meeting)

    assert "## Meeting Briefing: Quick chat" in result


# --- Integration test: calendar polling -> orchestrator -> DM delivery ---


@pytest.mark.asyncio
async def test_integration_calendar_poll_to_briefing_delivery(tmp_path):
    """End-to-end: CalendarBridge detects meeting -> orchestrator prepares briefing -> ChatBridge delivers DM."""
    from g3lobster.chat.calendar_bridge import CalendarBridge

    now = datetime.now(tz=timezone.utc)
    start = now + timedelta(minutes=10)

    # Mock Calendar API service
    mock_cal_service = MagicMock()
    mock_cal_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "id": "integration-evt-1",
                "summary": "Sprint Review",
                "description": "Review sprint deliverables",
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
                "attendees": [
                    {"email": "alice@example.com"},
                    {"email": "bob@example.com"},
                ],
            }
        ]
    }

    # Mock memory search
    mock_search = MagicMock()
    mock_hit = MagicMock()
    mock_hit.memory_type = "memory"
    mock_hit.snippet = "Sprint 23 velocity was 42 points"
    mock_search.search.return_value = [mock_hit]

    # Set up orchestrator
    orchestrator = MeetingPrepOrchestrator(memory_search=mock_search)

    # Track delivered messages
    delivered_messages = []

    async def mock_send_dm(text, dm_space_id=None):
        delivered_messages.append(text)
        return {"name": "spaces/test/messages/1"}

    # Set up CalendarBridge with callback that uses orchestrator + mock DM delivery
    bridge = CalendarBridge(
        service=mock_cal_service,
        lookahead_minutes=15,
        dedup_path=tmp_path / "briefed.json",
    )

    async def on_meeting(meeting):
        briefing = await orchestrator.prepare(meeting)
        await mock_send_dm(briefing)

    bridge.set_on_meeting(on_meeting)

    # Execute: poll should detect the meeting and trigger the full pipeline
    meetings = await bridge._poll_once()

    # Verify the full pipeline executed
    assert len(meetings) == 1
    assert meetings[0].title == "Sprint Review"

    # Verify briefing was delivered
    assert len(delivered_messages) == 1
    briefing = delivered_messages[0]
    assert "## Meeting Briefing: Sprint Review" in briefing
    assert "alice@example.com" in briefing
    assert "bob@example.com" in briefing
    assert "Sprint 23 velocity was 42 points" in briefing

    # Verify dedup prevents re-delivery
    delivered_messages.clear()
    meetings2 = await bridge._poll_once()
    assert len(meetings2) == 0
    assert len(delivered_messages) == 0
