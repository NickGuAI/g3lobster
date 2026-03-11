"""Tests for the meeting prep orchestrator."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

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
