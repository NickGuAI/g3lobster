from __future__ import annotations

from pathlib import Path

import pytest

from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.manager import MemoryManager
from g3lobster.mcp.loader import MCPConfigLoader
from g3lobster.mcp.manager import MCPManager


@pytest.fixture
def mcp_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "mcp"
    config_dir.mkdir()
    (config_dir / "gmail.yaml").write_text(
        """
name: gmail
enabled: true
transport:
  type: sse
  url: ${MCP_GMAIL_URL}
  headers:
    Authorization: Bearer ${MCP_GMAIL_TOKEN}
description: Gmail access
tool_patterns:
  - mcp__gmail__*
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_dir


@pytest.fixture
def mcp_manager(tmp_path: Path, mcp_config_dir: Path) -> MCPManager:
    loader = MCPConfigLoader(config_dir=str(mcp_config_dir))
    return MCPManager(loader=loader)


@pytest.fixture
def memory_manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(data_dir=str(tmp_path / "data"), compact_threshold=40)


@pytest.fixture
def context_builder(memory_manager: MemoryManager) -> ContextBuilder:
    return ContextBuilder(memory_manager=memory_manager, message_limit=6)
