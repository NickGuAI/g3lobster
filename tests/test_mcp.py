from __future__ import annotations

import os

import pytest

from g3lobster.mcp.delegation_server import DelegationMCPHandler
from g3lobster.mcp.loader import MCPConfigLoader
from g3lobster.mcp.manager import MCPManager


def test_mcp_loader_substitution_and_patterns(tmp_path) -> None:
    config_dir = tmp_path / "mcp"
    config_dir.mkdir()
    (config_dir / "gmail.yaml").write_text(
        """
name: gmail
enabled: true
transport:
  type: sse
  url: ${URL}
  headers:
    Authorization: Bearer ${TOKEN}
tool_patterns:
  - mcp__gmail__*
""".strip()
        + "\n",
        encoding="utf-8",
    )

    loader = MCPConfigLoader(str(config_dir))
    all_configs = loader.load_all(env_vars={"URL": "https://example.test", "TOKEN": "abc"})

    assert all_configs["gmail"]["transport"]["url"] == "https://example.test"
    assert all_configs["gmail"]["transport"]["headers"]["Authorization"] == "Bearer abc"
    assert loader.get_tool_patterns(env_vars={"URL": "https://example.test", "TOKEN": "abc"}) == {
        "gmail": ["mcp__gmail__*"]
    }


def test_mcp_manager_resolve_server_names(tmp_path) -> None:
    config_dir = tmp_path / "mcp"
    config_dir.mkdir()
    (config_dir / "gmail.yaml").write_text(
        """
name: gmail
enabled: true
transport:
  type: sse
  url: https://example.test
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (config_dir / "calendar.yaml").write_text(
        """
name: calendar
enabled: true
transport:
  type: sse
  url: https://example.test
""".strip()
        + "\n",
        encoding="utf-8",
    )

    manager = MCPManager(loader=MCPConfigLoader(str(config_dir)))

    assert manager.resolve_server_names(["*"]) == ["*"]
    assert manager.resolve_server_names(["calendar", "gmail"]) == ["calendar", "gmail"]
    with pytest.raises(ValueError, match="Unknown MCP server"):
        manager.resolve_server_names(["unknown"])


# --- DelegationMCPHandler tests ---


def test_delegation_handler_uses_cli_parent_id() -> None:
    """P0: CLI --parent-agent-id takes precedence over env var."""
    handler = DelegationMCPHandler(parent_agent_id="athena")
    assert handler.parent_agent_id == "athena"
    assert handler._resolve_parent_agent_id() == "athena"


def test_delegation_handler_falls_back_to_env_var(monkeypatch) -> None:
    """P0: Falls back to G3LOBSTER_AGENT_ID env var when CLI flag is empty."""
    monkeypatch.setenv("G3LOBSTER_AGENT_ID", "hermes")
    handler = DelegationMCPHandler(parent_agent_id="")
    # __init__ reads env immediately
    assert handler.parent_agent_id == "hermes"


def test_delegation_handler_env_fallback_in_resolve(monkeypatch) -> None:
    """P0: _resolve_parent_agent_id reads env at call time."""
    handler = DelegationMCPHandler(parent_agent_id="")
    monkeypatch.setenv("G3LOBSTER_AGENT_ID", "zeus")
    assert handler._resolve_parent_agent_id() == "zeus"


def test_delegation_handler_no_identity_error() -> None:
    """P0: Error when neither CLI flag nor env var provides agent ID."""
    handler = DelegationMCPHandler(parent_agent_id="")
    # Ensure env var is not set in the handler
    response = handler._delegate_to_agent(
        req_id=1,
        arguments={"agent_id": "hephaestus", "task": "do work"},
    )
    result = response["result"]
    assert result["isError"] is True
    assert "G3LOBSTER_AGENT_ID" in result["content"][0]["text"]


def test_delegation_handler_session_id_from_env(monkeypatch) -> None:
    """P1: _resolve_parent_session_id reads G3LOBSTER_SESSION_ID."""
    monkeypatch.setenv("G3LOBSTER_SESSION_ID", "session-42")
    handler = DelegationMCPHandler(parent_agent_id="athena")
    assert handler._resolve_parent_session_id() == "session-42"


def test_delegation_handler_session_id_defaults_to_default(monkeypatch) -> None:
    """P1: Falls back to 'default' when env var is not set."""
    monkeypatch.delenv("G3LOBSTER_SESSION_ID", raising=False)
    handler = DelegationMCPHandler(parent_agent_id="athena")
    assert handler._resolve_parent_session_id() == "default"


def test_delegation_handler_initialize_response() -> None:
    handler = DelegationMCPHandler(parent_agent_id="athena")
    resp = handler.handle_request({"method": "initialize", "id": 1})
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "g3lobster-delegation"


def test_delegation_handler_tools_list() -> None:
    handler = DelegationMCPHandler(parent_agent_id="athena")
    resp = handler.handle_request({"method": "tools/list", "id": 2})
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert "delegate_to_agent" in tool_names
    assert "list_agents" in tool_names


def test_delegation_handler_unknown_method() -> None:
    handler = DelegationMCPHandler(parent_agent_id="athena")
    resp = handler.handle_request({"method": "unknown/method", "id": 3})
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_delegation_handler_missing_args() -> None:
    """delegate_to_agent with missing required args returns tool error."""
    handler = DelegationMCPHandler(parent_agent_id="athena")
    resp = handler.handle_request({
        "method": "tools/call",
        "id": 4,
        "params": {"name": "delegate_to_agent", "arguments": {}},
    })
    result = resp["result"]
    assert result["isError"] is True
    assert "required" in result["content"][0]["text"].lower()


def test_delegation_handler_parse_args_optional_parent_id() -> None:
    """--parent-agent-id is optional (not required) for env-var fallback."""
    from g3lobster.mcp.delegation_server import parse_args
    args = parse_args(["--base-url", "http://localhost:9999"])
    assert args.parent_agent_id == ""
    assert args.base_url == "http://localhost:9999"
