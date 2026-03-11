"""MCP server exposing calendar operations as tools.

Communicates with the g3lobster REST API to perform calendar operations,
allowing agents to check conflicts, find meeting slots, reschedule, and
create events via standard MCP tool calls.

Usage (stdio transport):
    python -m g3lobster.mcp.calendar_server --base-url http://localhost:20001
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:20001"


def _build_check_conflicts_schema() -> Dict[str, Any]:
    return {
        "name": "check_conflicts",
        "description": (
            "Scan the user's Google Calendar for scheduling conflicts "
            "(double-bookings or overlapping events) within a time range."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID to scan (default 'primary').",
                    "default": "primary",
                },
                "days_ahead": {
                    "type": "number",
                    "description": "Number of days ahead to scan (default 7).",
                    "default": 7,
                },
            },
        },
    }


def _build_find_meeting_slots_schema() -> Dict[str, Any]:
    return {
        "name": "find_meeting_slots",
        "description": (
            "Find available meeting time slots for multiple attendees by "
            "checking their calendar availability via the FreeBusy API."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "attendee_emails": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses to check availability for.",
                },
                "duration_minutes": {
                    "type": "number",
                    "description": "Desired meeting duration in minutes (default 30).",
                    "default": 30,
                },
                "days_ahead": {
                    "type": "number",
                    "description": "Number of days ahead to search (default 7).",
                    "default": 7,
                },
                "max_results": {
                    "type": "number",
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
            "Requires explicit user approval before calling."
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
            "Create a new calendar event with attendees. "
            "Requires explicit user approval before calling."
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
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses.",
                },
                "start": {
                    "type": "string",
                    "description": "Event start time in ISO 8601 format.",
                },
                "end": {
                    "type": "string",
                    "description": "Event end time in ISO 8601 format.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description.",
                },
            },
            "required": ["summary", "start", "end"],
        },
    }


class CalendarMCPHandler:
    """Handles MCP JSON-RPC requests for calendar tools."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            return self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "g3lobster-calendar",
                    "version": "0.1.0",
                },
            })

        if method == "notifications/initialized":
            return {}

        if method == "tools/list":
            return self._respond(req_id, {
                "tools": [
                    _build_check_conflicts_schema(),
                    _build_find_meeting_slots_schema(),
                    _build_reschedule_event_schema(),
                    _build_create_event_schema(),
                ],
            })

        if method == "tools/call":
            return self._handle_tool_call(req_id, request.get("params", {}))

        return self._error(req_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handlers = {
            "check_conflicts": self._check_conflicts,
            "find_meeting_slots": self._find_meeting_slots,
            "reschedule_event": self._reschedule_event,
            "create_event": self._create_event,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return self._error(req_id, -32602, f"Unknown tool: {tool_name}")
        return handler(req_id, arguments)

    def _api_call(self, method: str, path: str, body: Optional[Dict] = None) -> Dict:
        import urllib.request

        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _check_conflicts(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        calendar_id = arguments.get("calendar_id", "primary")
        days_ahead = int(arguments.get("days_ahead", 7))
        try:
            result = self._api_call(
                "GET",
                f"/calendar/conflicts?calendar_id={calendar_id}&days_ahead={days_ahead}",
            )
            count = result.get("count", 0)
            if count == 0:
                text = "No scheduling conflicts found."
            else:
                lines = [f"Found {count} scheduling conflict(s):"]
                for c in result.get("conflicts", []):
                    a = c.get("event_a", {})
                    b = c.get("event_b", {})
                    lines.append(
                        f"- {a.get('summary', '?')} overlaps with {b.get('summary', '?')} "
                        f"({c.get('overlap_minutes', 0)} min overlap)"
                    )
                text = "\n".join(lines)
            return self._respond(req_id, {"content": [{"type": "text", "text": text}]})
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error checking conflicts: {exc}"}],
                "isError": True,
            })

    def _find_meeting_slots(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = self._api_call("POST", "/calendar/find-slots", {
                "attendee_emails": arguments.get("attendee_emails", []),
                "duration_minutes": int(arguments.get("duration_minutes", 30)),
                "days_ahead": int(arguments.get("days_ahead", 7)),
                "max_results": int(arguments.get("max_results", 5)),
            })
            proposals = result.get("proposals", [])
            if not proposals:
                text = "No available slots found in the requested time range."
            else:
                lines = [f"Found {len(proposals)} available slot(s):"]
                for i, p in enumerate(proposals, 1):
                    slot = p.get("slot", {})
                    lines.append(
                        f"{i}. {slot.get('start', '?')} — {slot.get('end', '?')} "
                        f"({slot.get('duration_minutes', 0)} min)"
                    )
                text = "\n".join(lines)
            return self._respond(req_id, {"content": [{"type": "text", "text": text}]})
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error finding slots: {exc}"}],
                "isError": True,
            })

    def _reschedule_event(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = self._api_call("POST", "/calendar/reschedule", {
                "event_id": arguments.get("event_id", ""),
                "calendar_id": arguments.get("calendar_id", "primary"),
                "new_start": arguments.get("new_start", ""),
                "new_end": arguments.get("new_end", ""),
            })
            text = f"Event rescheduled successfully. Link: {result.get('html_link', 'N/A')}"
            return self._respond(req_id, {"content": [{"type": "text", "text": text}]})
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error rescheduling: {exc}"}],
                "isError": True,
            })

    def _create_event(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = self._api_call("POST", "/calendar/create-event", {
                "calendar_id": arguments.get("calendar_id", "primary"),
                "summary": arguments.get("summary", ""),
                "attendees": arguments.get("attendees", []),
                "start": arguments.get("start", ""),
                "end": arguments.get("end", ""),
                "description": arguments.get("description"),
            })
            text = f"Event created successfully. Link: {result.get('html_link', 'N/A')}"
            return self._respond(req_id, {"content": [{"type": "text", "text": text}]})
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error creating event: {exc}"}],
                "isError": True,
            })

    @staticmethod
    def _respond(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_stdio(base_url: str = DEFAULT_BASE_URL) -> None:
    """Run the calendar MCP server on stdio transport."""
    handler = CalendarMCPHandler(base_url=base_url)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()
            continue

        response = handler.handle_request(request)
        if response:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="g3lobster calendar MCP server")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL for the g3lobster REST API",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    run_stdio(base_url=args.base_url)


if __name__ == "__main__":
    main()
