"""MCP server exposing per-agent cron management tools.

This stdio MCP server proxies to g3lobster cron REST endpoints and enforces
self-scope so an agent can only manage its own cron jobs.

Usage:
    python -m g3lobster.mcp.cron_server --base-url http://localhost:20001
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:20001"


def _build_list_cron_jobs_schema() -> Dict[str, Any]:
    return {
        "name": "list_cron_jobs",
        "description": "List cron jobs owned by the calling agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": (
                        "Optional agent ID. Must match the calling agent ID when provided."
                    ),
                },
            },
        },
    }


def _build_create_cron_job_schema() -> Dict[str, Any]:
    return {
        "name": "create_cron_job",
        "description": "Create a cron job for the calling agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "schedule": {
                    "type": "string",
                    "description": "5-field cron schedule (example: '0 9 * * *').",
                },
                "instruction": {
                    "type": "string",
                    "description": "Task instruction to run on each cron tick.",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Whether the cron job is enabled.",
                    "default": True,
                },
                "dm_target": {
                    "type": "string",
                    "description": "Optional DM target address for result delivery.",
                },
            },
            "required": ["schedule", "instruction"],
        },
    }


def _build_update_cron_job_schema() -> Dict[str, Any]:
    return {
        "name": "update_cron_job",
        "description": "Update a cron job owned by the calling agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Cron task ID.",
                },
                "schedule": {
                    "type": "string",
                    "description": "Optional replacement cron schedule.",
                },
                "instruction": {
                    "type": "string",
                    "description": "Optional replacement instruction.",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Optional enabled flag.",
                },
            },
            "required": ["task_id"],
        },
    }


def _build_delete_cron_job_schema() -> Dict[str, Any]:
    return {
        "name": "delete_cron_job",
        "description": "Delete a cron job owned by the calling agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Cron task ID.",
                },
            },
            "required": ["task_id"],
        },
    }


def _build_run_cron_job_schema() -> Dict[str, Any]:
    return {
        "name": "run_cron_job",
        "description": "Manually trigger a cron job owned by the calling agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Cron task ID.",
                },
            },
            "required": ["task_id"],
        },
    }


def _build_get_cron_history_schema() -> Dict[str, Any]:
    return {
        "name": "get_cron_history",
        "description": "Get last 20 run records for one of the calling agent's cron jobs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Cron task ID.",
                },
            },
            "required": ["task_id"],
        },
    }


class CronMCPHandler:
    """Handles MCP JSON-RPC requests for cron tools."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, parent_agent_id: str = ""):
        self.base_url = base_url.rstrip("/")
        # CLI flag takes precedence; fall back to env var set by GeminiProcess.
        self.parent_agent_id = parent_agent_id or os.environ.get("G3LOBSTER_AGENT_ID", "")
        self.max_jobs_per_agent = self._resolve_int(
            env_var="G3LOBSTER_CRON_MAX_JOBS_PER_AGENT",
            default=20,
            minimum=1,
        )

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            return self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "g3lobster-cron",
                    "version": "0.1.0",
                },
            })

        if method == "notifications/initialized":
            return {}

        if method == "tools/list":
            return self._respond(req_id, {
                "tools": [
                    _build_list_cron_jobs_schema(),
                    _build_create_cron_job_schema(),
                    _build_update_cron_job_schema(),
                    _build_delete_cron_job_schema(),
                    _build_run_cron_job_schema(),
                    _build_get_cron_history_schema(),
                ],
            })

        if method == "tools/call":
            return self._handle_tool_call(req_id, request.get("params", {}))

        return self._error(req_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handlers = {
            "list_cron_jobs": self._list_cron_jobs,
            "create_cron_job": self._create_cron_job,
            "update_cron_job": self._update_cron_job,
            "delete_cron_job": self._delete_cron_job,
            "run_cron_job": self._run_cron_job,
            "get_cron_history": self._get_cron_history,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return self._error(req_id, -32602, f"Unknown tool: {tool_name}")
        return handler(req_id, arguments)

    def _resolve_parent_agent_id(self) -> str:
        return self.parent_agent_id or os.environ.get("G3LOBSTER_AGENT_ID", "")

    @staticmethod
    def _resolve_int(env_var: str, default: int, minimum: int) -> int:
        raw = os.environ.get(env_var)
        if raw in (None, ""):
            return max(default, minimum)
        try:
            return max(int(raw), minimum)
        except (TypeError, ValueError):
            logger.warning("Invalid %s=%r; using default=%d", env_var, raw, default)
            return max(default, minimum)

    def _require_parent_agent_id(self, req_id: Any) -> str | Dict[str, Any]:
        parent_id = self._resolve_parent_agent_id()
        if parent_id:
            return parent_id
        return self._respond(req_id, {
            "content": [{
                "type": "text",
                "text": (
                    "Error: parent_agent_id is not configured. "
                    "Set G3LOBSTER_AGENT_ID env var or launch with --parent-agent-id <id>."
                ),
            }],
            "isError": True,
        })

    def _resolve_target_agent(self, req_id: Any, arguments: Dict[str, Any]) -> str | Dict[str, Any]:
        parent = self._require_parent_agent_id(req_id)
        if isinstance(parent, dict):
            return parent

        requested = str(arguments.get("agent_id", "")).strip()
        if requested and requested != parent:
            return self._respond(req_id, {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Error: cross-agent access denied. "
                        f"Caller can only manage cron jobs for agent_id={parent!r}."
                    ),
                }],
                "isError": True,
            })
        return requested or parent

    def _api_call(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        actor_agent_id: str = "",
    ) -> Any:
        import urllib.error
        import urllib.request

        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers: Dict[str, str] = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if actor_agent_id:
            headers["X-G3LOBSTER-AGENT-SOURCE"] = "mcp"
            headers["X-G3LOBSTER-ACTOR-AGENT-ID"] = actor_agent_id
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail_text = ""
            try:
                payload = exc.read().decode("utf-8", errors="replace")
                if payload:
                    parsed = json.loads(payload)
                    detail = parsed.get("detail", parsed)
                    detail_text = detail if isinstance(detail, str) else json.dumps(detail)
            except Exception:
                detail_text = ""
            suffix = f": {detail_text}" if detail_text else ""
            raise RuntimeError(f"HTTP {exc.code}{suffix}") from exc

    @staticmethod
    def _validate_schedule(schedule: str) -> Optional[str]:
        if len((schedule or "").strip().split()) > 5:
            return "Sub-minute cron schedules are not allowed"
        return None

    @staticmethod
    def _validate_instruction(instruction: str) -> Optional[str]:
        value = (instruction or "").strip()
        if not value:
            return "Instruction must be non-empty"
        if len(value) > 2000:
            return "Instruction must be 2000 characters or fewer"
        return None

    def _list_cron_jobs(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve_target_agent(req_id, arguments)
        if isinstance(target, dict):
            return target
        try:
            result = self._api_call("GET", f"/agents/{target}/crons", actor_agent_id=target)
            return self._respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error listing cron jobs: {exc}"}],
                "isError": True,
            })

    def _create_cron_job(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve_target_agent(req_id, arguments)
        if isinstance(target, dict):
            return target

        schedule = str(arguments.get("schedule", "")).strip()
        instruction = str(arguments.get("instruction", "")).strip()
        enabled = bool(arguments.get("enabled", True))
        dm_target = arguments.get("dm_target")

        if not schedule:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: schedule is required"}],
                "isError": True,
            })
        if not instruction:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: instruction is required"}],
                "isError": True,
            })
        schedule_error = self._validate_schedule(schedule)
        if schedule_error:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error: {schedule_error}"}],
                "isError": True,
            })
        instruction_error = self._validate_instruction(instruction)
        if instruction_error:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error: {instruction_error}"}],
                "isError": True,
            })

        body: Dict[str, Any] = {
            "schedule": schedule,
            "instruction": instruction,
            "enabled": enabled,
            "dm_target": dm_target,
        }

        try:
            existing = self._api_call("GET", f"/agents/{target}/crons", actor_agent_id=target)
            if isinstance(existing, list) and len(existing) >= self.max_jobs_per_agent:
                return self._respond(req_id, {
                    "content": [{
                        "type": "text",
                        "text": (
                            "Error: maximum cron jobs per agent exceeded "
                            f"(limit={self.max_jobs_per_agent})"
                        ),
                    }],
                    "isError": True,
                })
            result = self._api_call(
                "POST",
                f"/agents/{target}/crons",
                body=body,
                actor_agent_id=target,
            )
            return self._respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error creating cron job: {exc}"}],
                "isError": True,
            })

    def _update_cron_job(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve_target_agent(req_id, arguments)
        if isinstance(target, dict):
            return target

        task_id = str(arguments.get("task_id", "")).strip()
        if not task_id:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: task_id is required"}],
                "isError": True,
            })

        body: Dict[str, Any] = {}
        if "schedule" in arguments:
            body["schedule"] = arguments.get("schedule")
        if "instruction" in arguments:
            body["instruction"] = arguments.get("instruction")
        if "enabled" in arguments:
            body["enabled"] = bool(arguments.get("enabled"))

        if not body:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: no fields provided to update"}],
                "isError": True,
            })
        if "schedule" in body:
            schedule_error = self._validate_schedule(str(body["schedule"]))
            if schedule_error:
                return self._respond(req_id, {
                    "content": [{"type": "text", "text": f"Error: {schedule_error}"}],
                    "isError": True,
                })
        if "instruction" in body:
            instruction_error = self._validate_instruction(str(body["instruction"]))
            if instruction_error:
                return self._respond(req_id, {
                    "content": [{"type": "text", "text": f"Error: {instruction_error}"}],
                    "isError": True,
                })

        try:
            result = self._api_call(
                "PUT",
                f"/agents/{target}/crons/{task_id}",
                body=body,
                actor_agent_id=target,
            )
            return self._respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error updating cron job: {exc}"}],
                "isError": True,
            })

    def _delete_cron_job(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve_target_agent(req_id, arguments)
        if isinstance(target, dict):
            return target

        task_id = str(arguments.get("task_id", "")).strip()
        if not task_id:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: task_id is required"}],
                "isError": True,
            })

        try:
            result = self._api_call(
                "DELETE",
                f"/agents/{target}/crons/{task_id}",
                actor_agent_id=target,
            )
            return self._respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error deleting cron job: {exc}"}],
                "isError": True,
            })

    def _run_cron_job(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve_target_agent(req_id, arguments)
        if isinstance(target, dict):
            return target

        task_id = str(arguments.get("task_id", "")).strip()
        if not task_id:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: task_id is required"}],
                "isError": True,
            })

        try:
            result = self._api_call(
                "POST",
                f"/agents/{target}/crons/{task_id}/run",
                actor_agent_id=target,
            )
            return self._respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error running cron job: {exc}"}],
                "isError": True,
            })

    def _get_cron_history(self, req_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve_target_agent(req_id, arguments)
        if isinstance(target, dict):
            return target

        task_id = str(arguments.get("task_id", "")).strip()
        if not task_id:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": "Error: task_id is required"}],
                "isError": True,
            })

        try:
            result = self._api_call(
                "GET",
                f"/agents/{target}/crons/{task_id}/history",
                actor_agent_id=target,
            )
            return self._respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"Error getting cron history: {exc}"}],
                "isError": True,
            })

    @staticmethod
    def _respond(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_stdio(base_url: str = DEFAULT_BASE_URL, parent_agent_id: str = "") -> None:
    """Run the cron MCP server on stdio transport."""
    handler = CronMCPHandler(base_url=base_url, parent_agent_id=parent_agent_id)

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
    parser = argparse.ArgumentParser(description="g3lobster cron MCP server")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL for the g3lobster REST API",
    )
    parser.add_argument(
        "--parent-agent-id",
        default="",
        help="Agent ID of the calling agent. Falls back to G3LOBSTER_AGENT_ID env var.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    run_stdio(base_url=args.base_url, parent_agent_id=args.parent_agent_id)


if __name__ == "__main__":
    main()
