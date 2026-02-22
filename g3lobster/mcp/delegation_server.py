"""MCP delegation server exposing agent-to-agent delegation tools.

This module provides a lightweight MCP-compatible tool server that can
be used by Gemini CLI agents to delegate tasks to sibling agents.  It
communicates with the g3lobster REST API internally.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class DelegationMCPServer:
    """Exposes ``delegate_to_agent`` and ``list_agents`` as MCP-style tools.

    The server bridges to the g3lobster REST API so that Gemini CLI
    agents can trigger delegation without direct Python access to the
    registry.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:20001") -> None:
        self.base_url = base_url.rstrip("/")

    # -- MCP tool definitions -------------------------------------------------

    def tool_definitions(self) -> List[Dict[str, Any]]:
        """Return JSON-Schema style tool definitions for MCP registration."""
        return [
            {
                "name": "delegate_to_agent",
                "description": (
                    "Delegate a task to another agent. The child agent will "
                    "execute the task and return the result."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "The ID of the agent to delegate to.",
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
            },
            {
                "name": "list_agents",
                "description": "List all available agents and their current state.",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    # -- Tool execution -------------------------------------------------------

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        caller_agent_id: str = "unknown",
        caller_session_id: str = "default",
    ) -> Dict[str, Any]:
        """Execute a tool call and return the result."""
        if tool_name == "delegate_to_agent":
            return await self._delegate_to_agent(
                caller_agent_id=caller_agent_id,
                caller_session_id=caller_session_id,
                child_agent_id=arguments["agent_id"],
                task=arguments["task"],
                timeout_s=arguments.get("timeout_s", 300.0),
            )
        elif tool_name == "list_agents":
            return await self._list_agents()
        else:
            return {"error": f"Unknown tool: {tool_name}"}

    async def _delegate_to_agent(
        self,
        caller_agent_id: str,
        caller_session_id: str,
        child_agent_id: str,
        task: str,
        timeout_s: float = 300.0,
    ) -> Dict[str, Any]:
        """Delegate task via the REST API."""
        url = f"{self.base_url}/delegation/run"
        payload = {
            "parent_agent_id": caller_agent_id,
            "child_agent_id": child_agent_id,
            "task": task,
            "parent_session_id": caller_session_id,
            "timeout_s": timeout_s,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_s + 30) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("Delegation call failed: %s", exc)
            return {"error": str(exc)}

    async def _list_agents(self) -> Dict[str, Any]:
        """List agents via the REST API."""
        url = f"{self.base_url}/agents"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                agents = resp.json()
                return {"agents": agents}
        except Exception as exc:
            logger.error("List agents call failed: %s", exc)
            return {"error": str(exc)}
