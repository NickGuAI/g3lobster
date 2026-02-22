"""MCP tool bridge that delegates tasks through g3lobster's REST API."""

from __future__ import annotations

import argparse
import json
from typing import Dict, List
from urllib import error, request

from g3lobster.memory.context import summarize_agent_soul

try:  # pragma: no cover - optional dependency used in production integration
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - keep import optional for tests/dev
    FastMCP = None


class DelegationAPIClient:
    """Simple internal API client used by delegation MCP tools."""

    def __init__(self, base_url: str, parent_agent_id: str, parent_session_id: str):
        self.base_url = base_url.rstrip("/")
        self.parent_agent_id = str(parent_agent_id).strip()
        self.parent_session_id = str(parent_session_id).strip()
        if not self.parent_agent_id:
            raise ValueError("parent_agent_id is required")
        if not self.parent_session_id:
            raise ValueError("parent_session_id is required")

    def delegate_to_agent(self, agent_id: str, task: str, timeout_s: float = 300.0) -> Dict[str, object]:
        normalized_agent_id = str(agent_id).strip()
        normalized_task = str(task).strip()
        normalized_timeout_s = float(timeout_s)
        if not normalized_agent_id:
            raise ValueError("agent_id is required")
        if not normalized_task:
            raise ValueError("task is required")
        if normalized_timeout_s <= 0:
            raise ValueError("timeout_s must be greater than 0")
        payload = {
            "parent_agent_id": self.parent_agent_id,
            "child_agent_id": normalized_agent_id,
            "task": normalized_task,
            "parent_session_id": self.parent_session_id,
            "timeout_s": normalized_timeout_s,
        }
        return self._request_json("POST", "/delegation/run", payload)

    def list_agents(self) -> List[Dict[str, str]]:
        agents_payload = self._request_json("GET", "/agents")
        if isinstance(agents_payload, dict):
            agents = agents_payload.get("agents", [])
        elif isinstance(agents_payload, list):
            agents = agents_payload
        else:
            agents = []

        results: List[Dict[str, str]] = []
        for item in agents:
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("id", "")).strip()
            if not agent_id:
                continue
            if not bool(item.get("enabled", True)):
                continue
            if agent_id == self.parent_agent_id:
                continue
            detail = self._request_json("GET", f"/agents/{agent_id}")
            soul = str(detail.get("soul", "")).strip()
            results.append(
                {
                    "id": agent_id,
                    "name": str(item.get("name", "")).strip() or agent_id,
                    "description": summarize_agent_soul(soul),
                }
            )
        return results

    def _request_json(self, method: str, path: str, body: Dict[str, object] = None):
        req_body = None
        headers = {"Accept": "application/json"}
        if body is not None:
            req_body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(
            f"{self.base_url}{path}",
            data=req_body,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=30.0) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Delegation API error {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Delegation API unavailable: {exc.reason}") from exc

        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Delegation API returned invalid JSON") from exc


def create_mcp_server(base_url: str, parent_agent_id: str, parent_session_id: str):
    """Create a FastMCP server exposing delegation tools."""
    if FastMCP is None:  # pragma: no cover - runtime guard
        raise RuntimeError("mcp.server.fastmcp is not installed")

    client = DelegationAPIClient(
        base_url=base_url,
        parent_agent_id=parent_agent_id,
        parent_session_id=parent_session_id,
    )
    server = FastMCP("g3lobster-delegation")

    @server.tool()
    def delegate_to_agent(agent_id: str, task: str, timeout_s: float = 300.0) -> Dict[str, object]:
        """Delegate a task to another g3lobster agent and return the run result."""
        return client.delegate_to_agent(agent_id=agent_id, task=task, timeout_s=timeout_s)

    @server.tool()
    def list_agents() -> List[Dict[str, str]]:
        """List available g3lobster agents and short capability summaries."""
        return client.list_agents()

    return server


def parse_args(argv: List[str] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run g3lobster delegation MCP bridge")
    parser.add_argument("--base-url", default="http://127.0.0.1:20001", help="g3lobster API base URL")
    parser.add_argument("--parent-agent-id", required=True, help="Requesting parent agent id")
    parser.add_argument("--parent-session-id", required=True, help="Parent session id for routing")
    return parser.parse_args(argv)


def main(argv: List[str] = None) -> None:  # pragma: no cover - CLI wrapper
    args = parse_args(argv)
    server = create_mcp_server(
        base_url=args.base_url,
        parent_agent_id=args.parent_agent_id,
        parent_session_id=args.parent_session_id,
    )
    server.run()


if __name__ == "__main__":  # pragma: no cover - module CLI entrypoint
    main()
