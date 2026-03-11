"""MCP tool schemas for calendar operations.

These schemas are registered alongside delegation tools so agents can
invoke calendar operations via MCP tool calls.
"""

from __future__ import annotations

from typing import Any, Dict, List


def build_calendar_tool_schemas() -> List[Dict[str, Any]]:
    """Return MCP tool schemas for all calendar operations."""
    return [
        _build_check_conflicts_schema(),
        _build_find_meeting_slots_schema(),
        _build_reschedule_event_schema(),
        _build_create_event_schema(),
    ]


def _build_check_conflicts_schema() -> Dict[str, Any]:
    return {
        "name": "check_conflicts",
        "description": (
            "Scan the user's Google Calendar for scheduling conflicts "
            "(overlapping events) within a time window. Returns pairs of "
            "conflicting events with overlap duration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID to scan (default 'primary').",
                    "default": "primary",
                },
                "hours_ahead": {
                    "type": "number",
                    "description": "How many hours ahead to scan (default 24).",
                    "default": 24.0,
                },
            },
        },
    }


def _build_find_meeting_slots_schema() -> Dict[str, Any]:
    return {
        "name": "find_meeting_slots",
        "description": (
            "Find common free time slots across multiple people's calendars. "
            "Uses the Google Calendar FreeBusy API to propose meeting times "
            "when all attendees are available."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "attendee_emails": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Email addresses of all attendees to check.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Required meeting duration in minutes (default 30).",
                    "default": 30,
                },
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to search (default 7).",
                    "default": 7,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of slot proposals to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["attendee_emails"],
        },
    }


def _build_reschedule_event_schema() -> Dict[str, Any]:
    return {
        "name": "reschedule_event",
        "description": (
            "Reschedule an existing calendar event to a new time. "
            "Requires user approval before execution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "The Google Calendar event ID to reschedule.",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default 'primary').",
                    "default": "primary",
                },
                "new_start": {
                    "type": "string",
                    "description": "New start time in ISO 8601 format.",
                },
                "new_end": {
                    "type": "string",
                    "description": "New end time in ISO 8601 format.",
                },
            },
            "required": ["event_id", "new_start", "new_end"],
        },
    }


def _build_create_event_schema() -> Dict[str, Any]:
    return {
        "name": "create_event",
        "description": (
            "Create a new calendar event with optional attendees. "
            "Requires user approval before execution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default 'primary').",
                    "default": "primary",
                },
                "summary": {
                    "type": "string",
                    "description": "Event title/summary.",
                },
                "start": {
                    "type": "string",
                    "description": "Start time in ISO 8601 format.",
                },
                "end": {
                    "type": "string",
                    "description": "End time in ISO 8601 format.",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Email addresses of attendees.",
                },
                "description": {
                    "type": "string",
                    "description": "Event description.",
                    "default": "",
                },
            },
            "required": ["summary", "start", "end"],
        },
    }
