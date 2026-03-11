"""Tests for the Google Workspace MCP server."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from g3lobster.mcp.loader import MCPConfigLoader
from g3lobster.mcp.workspace_server import WorkspaceMCPHandler


# --- Handler protocol tests ---


def test_workspace_handler_initialize() -> None:
    handler = WorkspaceMCPHandler()
    resp = handler.handle_request({"method": "initialize", "id": 1})
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "g3lobster-workspace"


def test_workspace_handler_tools_list() -> None:
    handler = WorkspaceMCPHandler()
    resp = handler.handle_request({"method": "tools/list", "id": 2})
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert "search_drive" in tool_names
    assert "read_doc" in tool_names
    assert "read_sheet" in tool_names


def test_workspace_handler_unknown_method() -> None:
    handler = WorkspaceMCPHandler()
    resp = handler.handle_request({"method": "unknown/method", "id": 3})
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_workspace_handler_notification() -> None:
    handler = WorkspaceMCPHandler()
    resp = handler.handle_request({"method": "notifications/initialized", "id": None})
    assert resp == {}


def test_workspace_handler_unknown_tool() -> None:
    handler = WorkspaceMCPHandler()
    resp = handler.handle_request({
        "method": "tools/call",
        "id": 4,
        "params": {"name": "unknown_tool", "arguments": {}},
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32602


# --- Tool argument validation ---


def test_search_drive_missing_query() -> None:
    handler = WorkspaceMCPHandler()
    resp = handler.handle_request({
        "method": "tools/call",
        "id": 5,
        "params": {"name": "search_drive", "arguments": {}},
    })
    assert resp["result"]["isError"] is True
    assert "query" in resp["result"]["content"][0]["text"].lower()


def test_read_doc_missing_doc_id() -> None:
    handler = WorkspaceMCPHandler()
    resp = handler.handle_request({
        "method": "tools/call",
        "id": 6,
        "params": {"name": "read_doc", "arguments": {}},
    })
    assert resp["result"]["isError"] is True
    assert "doc_id" in resp["result"]["content"][0]["text"].lower()


def test_read_sheet_missing_sheet_id() -> None:
    handler = WorkspaceMCPHandler()
    resp = handler.handle_request({
        "method": "tools/call",
        "id": 7,
        "params": {"name": "read_sheet", "arguments": {}},
    })
    assert resp["result"]["isError"] is True
    assert "sheet_id" in resp["result"]["content"][0]["text"].lower()


# --- Tool execution with mocked APIs ---


@patch("g3lobster.mcp.workspace_server._get_workspace_credentials")
def test_search_drive_success(mock_creds) -> None:
    mock_creds.return_value = MagicMock()

    fake_files = [
        {"id": "abc123", "name": "Q1 Tracker", "mimeType": "application/vnd.google-apps.spreadsheet"}
    ]

    handler = WorkspaceMCPHandler()
    with patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": fake_files}

        resp = handler.handle_request({
            "method": "tools/call",
            "id": 8,
            "params": {"name": "search_drive", "arguments": {"query": "Q1 Tracker"}},
        })

    content = resp["result"]["content"][0]["text"]
    assert "Q1 Tracker" in content
    assert resp["result"].get("isError") is not True


@patch("g3lobster.mcp.workspace_server._get_workspace_credentials")
def test_read_doc_success(mock_creds) -> None:
    mock_creds.return_value = MagicMock()

    fake_doc = {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Hello, world!\n"}}
                        ]
                    }
                }
            ]
        }
    }

    handler = WorkspaceMCPHandler()
    with patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.documents().get().execute.return_value = fake_doc

        resp = handler.handle_request({
            "method": "tools/call",
            "id": 9,
            "params": {"name": "read_doc", "arguments": {"doc_id": "doc-abc"}},
        })

    content = resp["result"]["content"][0]["text"]
    assert "Hello, world!" in content
    assert resp["result"].get("isError") is not True


@patch("g3lobster.mcp.workspace_server._get_workspace_credentials")
def test_read_sheet_success(mock_creds) -> None:
    mock_creds.return_value = MagicMock()

    handler = WorkspaceMCPHandler()
    with patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [["Name", "Revenue"], ["Q1", "1000000"]]
        }

        resp = handler.handle_request({
            "method": "tools/call",
            "id": 10,
            "params": {"name": "read_sheet", "arguments": {"sheet_id": "sheet-xyz"}},
        })

    content = resp["result"]["content"][0]["text"]
    assert "Name\tRevenue" in content
    assert "Q1\t1000000" in content
    assert resp["result"].get("isError") is not True


@patch("g3lobster.mcp.workspace_server._get_workspace_credentials")
def test_read_sheet_empty(mock_creds) -> None:
    mock_creds.return_value = MagicMock()

    handler = WorkspaceMCPHandler()
    with patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets().values().get().execute.return_value = {"values": []}

        resp = handler.handle_request({
            "method": "tools/call",
            "id": 11,
            "params": {"name": "read_sheet", "arguments": {"sheet_id": "sheet-xyz"}},
        })

    content = resp["result"]["content"][0]["text"]
    assert "empty" in content.lower()


# --- Auth error handling ---


def test_search_drive_auth_error() -> None:
    handler = WorkspaceMCPHandler(data_dir="/nonexistent")
    resp = handler.handle_request({
        "method": "tools/call",
        "id": 12,
        "params": {"name": "search_drive", "arguments": {"query": "test"}},
    })
    assert resp["result"]["isError"] is True
    assert "error" in resp["result"]["content"][0]["text"].lower()


# --- workspace.yaml loadable ---


def test_workspace_yaml_loadable(tmp_path) -> None:
    config_dir = tmp_path / "mcp"
    config_dir.mkdir()
    (config_dir / "workspace.yaml").write_text(
        """\
name: g3lobster-workspace
enabled: true
transport:
  type: stdio
  command: python
  args:
    - -m
    - g3lobster.mcp.workspace_server
description: Google Workspace document query tools (Drive, Docs, Sheets)
tool_patterns:
  - search_drive
  - read_doc
  - read_sheet
""",
        encoding="utf-8",
    )

    loader = MCPConfigLoader(str(config_dir))
    configs = loader.load_all()

    assert "g3lobster-workspace" in configs
    assert configs["g3lobster-workspace"]["transport"]["type"] == "stdio"
    patterns = loader.get_tool_patterns()
    assert "g3lobster-workspace" in patterns
    assert "search_drive" in patterns["g3lobster-workspace"]
    assert "read_doc" in patterns["g3lobster-workspace"]
    assert "read_sheet" in patterns["g3lobster-workspace"]


def test_workspace_yaml_from_repo() -> None:
    """Verify the actual workspace.yaml in config/mcp/ is loadable."""
    config_dir = Path("config/mcp")
    loader = MCPConfigLoader(str(config_dir))
    configs = loader.load_all()
    assert "g3lobster-workspace" in configs
    patterns = loader.get_tool_patterns()
    assert "search_drive" in patterns["g3lobster-workspace"]


# --- parse_args ---


def test_workspace_parse_args_defaults() -> None:
    from g3lobster.mcp.workspace_server import parse_args
    args = parse_args([])
    assert args.data_dir is None


def test_workspace_parse_args_data_dir() -> None:
    from g3lobster.mcp.workspace_server import parse_args
    args = parse_args(["--data-dir", "/tmp/test"])
    assert args.data_dir == "/tmp/test"


# --- auth.py workspace scopes ---


def test_auth_workspace_scopes_defined() -> None:
    from g3lobster.chat.auth import WORKSPACE_SCOPES
    assert "https://www.googleapis.com/auth/drive.readonly" in WORKSPACE_SCOPES
    assert "https://www.googleapis.com/auth/documents.readonly" in WORKSPACE_SCOPES
    assert "https://www.googleapis.com/auth/spreadsheets.readonly" in WORKSPACE_SCOPES


def test_auth_get_workspace_credentials_exists() -> None:
    from g3lobster.chat.auth import get_workspace_credentials
    assert callable(get_workspace_credentials)
