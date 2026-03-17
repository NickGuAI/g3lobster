"""MCP server exposing self-scoped task-board management tools.

Usage (stdio transport):
    python -m g3lobster.mcp.tasks_server --base-url http://localhost:20001
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Optional, Sequence
from urllib import parse, request

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:20001"


def _build_create_task_schema() -> Dict[str, Any]:
    return {
        "name": "create_task",
        "description": "Create a task for the calling agent on the unified task board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title."},
                "type": {
                    "type": "string",
                    "enum": ["bug", "feature", "chore", "research", "reminder"],
                    "description": "Task category (default chore).",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "critical"],
                    "description": "Task priority (default normal).",
                },
                "metadata": {"type": "object", "description": "Optional task metadata dictionary."},
            },
            "required": ["title"],
        },
    }


def _build_update_task_schema() -> Dict[str, Any]:
    return {
        "name": "update_task",
        "description": "Update one of your own board tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to update."},
                "title": {"type": "string"},
                "type": {"type": "string", "enum": ["bug", "feature", "chore", "research", "reminder"]},
                "status": {"type": "string", "enum": ["todo", "in_progress", "done", "blocked"]},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
                "metadata": {"type": "object"},
                "result": {"type": "string"},
            },
            "required": ["task_id"],
        },
    }


def _build_complete_task_schema() -> Dict[str, Any]:
    return {
        "name": "complete_task",
        "description": "Mark one of your own board tasks as done.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to complete."},
                "result": {"type": "string", "description": "Optional completion result summary."},
            },
            "required": ["task_id"],
        },
    }


def _build_list_tasks_schema() -> Dict[str, Any]:
    return {
        "name": "list_tasks",
        "description": "List your own board tasks with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["todo", "in_progress", "done", "blocked"]},
                "type": {"type": "string", "enum": ["bug", "feature", "chore", "research", "reminder"]},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
                "limit": {"type": "number", "description": "Max rows to return (default 50)."},
            },
        },
    }


def _build_delete_task_schema() -> Dict[str, Any]:
    return {
        "name": "delete_task",
        "description": "Delete one of your own board tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to delete."},
            },
            "required": ["task_id"],
        },
    }


class TasksMCPHandler:
    """Handle MCP JSON-RPC for self-managed task-board CRUD."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, agent_id: str = ""):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id or os.environ.get("G3LOBSTER_AGENT_ID", "").strip()

    def handle_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        method = payload.get("method", "")
        req_id = payload.get("id")

        if method == "initialize":
            return self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "g3lobster-tasks", "version": "0.1.0"},
            })

        if method == "notifications/initialized":
            return {}

        if method == "tools/list":
            return self._respond(req_id, {
                "tools": [
                    _build_create_task_schema(),
                    _build_update_task_schema(),
                    _build_complete_task_schema(),
                    _build_list_tasks_schema(),
                    _build_delete_task_schema(),
                ],
            })

        if method == "tools/call":
            params = payload.get("params", {})
            return self._handle_tool_call(req_id, params)

        return self._error(req_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        if name == "create_task":
            return self._create_task(req_id, arguments)
        if name == "update_task":
            return self._update_task(req_id, arguments)
        if name == "complete_task":
            return self._complete_task(req_id, arguments)
        if name == "list_tasks":
            return self._list_tasks(req_id, arguments)
        if name == "delete_task":
            return self._delete_task(req_id, arguments)
        return self._error(req_id, -32602, f"Unknown tool: {name}")

    def _require_agent_id(self, req_id: Any) -> Optional[Dict[str, Any]]:
        if self.agent_id:
            return None
        return self._respond(req_id, {
            "content": [{
                "type": "text",
                "text": (
                    "Error: agent identity is not configured. Set G3LOBSTER_AGENT_ID "
                    "or launch tasks_server with --agent-id <id>."
                ),
            }],
            "isError": True,
        })

    def _api_call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout_s: int = 30) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        with request.urlopen(req, timeout=timeout_s) as resp:
            text = resp.read().decode("utf-8")
            if not text:
                return {}
            return json.loads(text)

    def _get_owned_task_or_error(self, req_id: Any, task_id: str) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not task_id:
            return None, self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: task_id is required"}],
                "isError": True,
            })
        try:
            task = self._api_call("GET", f"/tasks/{parse.quote(task_id)}")
        except Exception as exc:
            return None, self._respond(req_id, {
                "content": [{"type": "text", "text": f"Task lookup failed: {exc}"}],
                "isError": True,
            })
        if str(task.get("agent_id") or "") != self.agent_id:
            return None, self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: cannot mutate tasks owned by another agent"}],
                "isError": True,
            })
        return task, None

    def _create_task(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if (err := self._require_agent_id(req_id)) is not None:
            return err

        title = str(arguments.get("title") or "").strip()
        if not title:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: title is required"}],
                "isError": True,
            })

        payload: Dict[str, Any] = {
            "title": title,
            "type": str(arguments.get("type") or "chore"),
            "priority": str(arguments.get("priority") or "normal"),
            "status": "todo",
            "agent_id": self.agent_id,
            "created_by": "agent",
            "metadata": arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {},
        }
        try:
            item = self._api_call("POST", "/tasks", body=payload)
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"create_task failed: {exc}"}],
                "isError": True,
            })
        return self._respond(req_id, {
            "content": [{"type": "text", "text": json.dumps(item, indent=2)}],
        })

    def _update_task(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if (err := self._require_agent_id(req_id)) is not None:
            return err

        task_id = str(arguments.get("task_id") or "").strip()
        _task, lookup_error = self._get_owned_task_or_error(req_id, task_id)
        if lookup_error is not None:
            return lookup_error

        payload: Dict[str, Any] = {}
        for key in ("title", "type", "status", "priority", "result"):
            if key in arguments and arguments[key] is not None:
                payload[key] = arguments[key]
        if "metadata" in arguments and isinstance(arguments["metadata"], dict):
            payload["metadata"] = arguments["metadata"]
        if not payload:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: no updatable fields were provided"}],
                "isError": True,
            })

        try:
            updated = self._api_call("PUT", f"/tasks/{parse.quote(task_id)}", body=payload)
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"update_task failed: {exc}"}],
                "isError": True,
            })

        return self._respond(req_id, {"content": [{"type": "text", "text": json.dumps(updated, indent=2)}]})

    def _complete_task(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if (err := self._require_agent_id(req_id)) is not None:
            return err

        task_id = str(arguments.get("task_id") or "").strip()
        _task, lookup_error = self._get_owned_task_or_error(req_id, task_id)
        if lookup_error is not None:
            return lookup_error

        payload = {"result": arguments.get("result")} if "result" in arguments else {"result": None}
        try:
            completed = self._api_call("POST", f"/tasks/{parse.quote(task_id)}/complete", body=payload)
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"complete_task failed: {exc}"}],
                "isError": True,
            })
        return self._respond(req_id, {"content": [{"type": "text", "text": json.dumps(completed, indent=2)}]})

    def _list_tasks(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if (err := self._require_agent_id(req_id)) is not None:
            return err

        query: Dict[str, str] = {"agent_id": self.agent_id}
        for field in ("status", "type", "priority", "limit"):
            value = arguments.get(field)
            if value is not None and str(value).strip():
                query[field] = str(value).strip()
        qs = parse.urlencode(query)

        try:
            items = self._api_call("GET", f"/tasks?{qs}")
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"list_tasks failed: {exc}"}],
                "isError": True,
            })

        return self._respond(req_id, {"content": [{"type": "text", "text": json.dumps(items, indent=2)}]})

    def _delete_task(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if (err := self._require_agent_id(req_id)) is not None:
            return err

        task_id = str(arguments.get("task_id") or "").strip()
        _task, lookup_error = self._get_owned_task_or_error(req_id, task_id)
        if lookup_error is not None:
            return lookup_error

        try:
            result = self._api_call("DELETE", f"/tasks/{parse.quote(task_id)}")
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"delete_task failed: {exc}"}],
                "isError": True,
            })
        return self._respond(req_id, {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]})

    @staticmethod
    def _respond(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_stdio(base_url: str = DEFAULT_BASE_URL, agent_id: str = "") -> None:
    handler = TasksMCPHandler(base_url=base_url, agent_id=agent_id)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()
            continue

        response = handler.handle_request(payload)
        if response:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="g3lobster task-board MCP server")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="g3lobster API base URL")
    parser.add_argument("--agent-id", default="", help="Override caller agent id (default from G3LOBSTER_AGENT_ID)")
    parser.add_argument("--log-level", default="WARNING", help="Logging level")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.WARNING))
    run_stdio(base_url=str(args.base_url), agent_id=str(args.agent_id))


if __name__ == "__main__":
    main()
