from __future__ import annotations

import pytest

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
