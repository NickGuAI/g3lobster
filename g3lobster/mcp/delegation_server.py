"""MCP delegation server for exposing agent-to-agent delegation as tools.

This server communicates with the g3lobster REST API to trigger delegation
runs, allowing Gemini CLI agents to delegate tasks to sibling agents via
standard MCP tool calls.

Usage (stdio transport):
    python -m g3lobster.mcp.delegation_server --base-url http://localhost:8080
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

# Default base URL for the g3lobster API
DEFAULT_BASE_URL = "http://localhost:8080"


def _build_delegate_tool_schema() -> Dict[str, Any]:
    return {
        "name": "delegate_to_agent",
        "description": (
            "Delegate a task to another agent. The child agent will execute the "
            "task and return the result. Use this when a task is better suited "
            "for a specialized sibling agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The ID of the agent to delegate the task to.",
                },
                "task": {
                    "type": "string",
                    "description": "The task prompt to send to the child agent.",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Timeout in seconds (default 300).",
                    "default": 300.0,
                },
            },
            "required": ["agent_id", "task"],
        },
    }


def _build_list_agents_tool_schema() -> Dict[str, Any]:
    return {
        "name": "list_agents",
        "description": (
            "List all available agents and their capabilities. "
            "Use this to discover which agents are available for delegation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    }


class DelegationMCPHandler:
    """Handles MCP JSON-RPC requests for agent delegation tools.

    Communicates with the g3lobster REST API via HTTP to trigger and query
    delegation runs.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, parent_agent_id: str = ""):
        self.base_url = base_url.rstrip("/")
        self.parent_agent_id = parent_agent_id

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single JSON-RPC request and return a response."""
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            return self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "g3lobster-delegation",
                    "version": "0.1.0",
                },
            })

        if method == "notifications/initialized":
            # Notification — no response needed
            return {}

        if method == "tools/list":
            return self._respond(req_id, {
                "tools": [
                    _build_delegate_tool_schema(),
                    _build_list_agents_tool_schema(),
                ],
            })

        if method == "tools/call":
            return self._handle_tool_call(req_id, request.get("params", {}))

        return self._error(req_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(
        self, req_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "delegate_to_agent":
            return self._delegate_to_agent(req_id, arguments)
        if tool_name == "list_agents":
            return self._list_agents(req_id)

        return self._error(req_id, -32602, f"Unknown tool: {tool_name}")

    def _delegate_to_agent(
        self, req_id: Any, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        agent_id = arguments.get("agent_id", "")
        task = arguments.get("task", "")
        timeout_s = float(arguments.get("timeout_s", 300.0))

        if not agent_id or not task:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: agent_id and task are required"}],
                "isError": True,
            })

        if not self.parent_agent_id:
            return self._respond(req_id, {
                "content": [{
                    "type": "text",
                    "text": (
                        "Error: parent_agent_id is not configured. "
                        "Launch the delegation MCP server with --parent-agent-id <id>."
                    ),
                }],
                "isError": True,
            })

        # Build the REST API request payload
        payload = {
            "parent_agent_id": self.parent_agent_id,
            "child_agent_id": agent_id,
            "task": task,
            "parent_session_id": "default",
            "timeout_s": timeout_s,
        }

        try:
            import urllib.request
            url = f"{self.base_url}/delegation/run"
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=int(timeout_s) + 30) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            if result.get("error"):
                text = f"Delegation failed: {result['error']}"
            else:
                text = result.get("result", "Delegation completed (no result text)")

            return self._respond(req_id, {
                "content": [{"type": "text", "text": text}],
                "isError": bool(result.get("error")),
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Delegation error: {exc}"}],
                "isError": True,
            })

    def _list_agents(self, req_id: Any) -> Dict[str, Any]:
        try:
            import urllib.request
            url = f"{self.base_url}/agents"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                agents = json.loads(resp.read().decode("utf-8"))

            lines = ["Available agents for delegation:"]
            for agent in agents:
                agent_id = agent.get("id", "unknown")
                name = agent.get("name", agent_id)
                emoji = agent.get("emoji", "")
                state = agent.get("state", "unknown")
                description = agent.get("description", "")
                entry = f"- {emoji} {name} (id: {agent_id}, state: {state})"
                if description:
                    entry += f" — {description}"
                lines.append(entry)

            return self._respond(req_id, {
                "content": [{"type": "text", "text": "\n".join(lines)}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error listing agents: {exc}"}],
                "isError": True,
            })

    @staticmethod
    def _respond(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_stdio(base_url: str = DEFAULT_BASE_URL, parent_agent_id: str = "") -> None:
    """Run the delegation MCP server on stdio transport."""
    handler = DelegationMCPHandler(base_url=base_url, parent_agent_id=parent_agent_id)

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
        if response:  # Skip empty responses (notifications)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="g3lobster delegation MCP server")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL for the g3lobster REST API",
    )
    parser.add_argument(
        "--parent-agent-id",
        required=True,
        help="Agent ID of the parent (calling) agent",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    run_stdio(base_url=args.base_url, parent_agent_id=args.parent_agent_id)


if __name__ == "__main__":
    main()
