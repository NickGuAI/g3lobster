"""MCP server selection and tool pattern manager."""

from __future__ import annotations

from typing import Dict, List, Optional

from g3lobster.mcp.loader import MCPConfigLoader


class MCPManager:
    """Resolves and validates MCP server selections."""

    def __init__(self, loader: MCPConfigLoader):
        self.loader = loader

    def get_available_servers(self, env_vars: Optional[Dict[str, str]] = None) -> List[str]:
        return sorted(self.loader.load_all(env_vars=env_vars).keys())

    def resolve_server_names(
        self,
        selected_mcps: Optional[List[str]] = None,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        selected = selected_mcps or ["*"]
        if selected == ["*"]:
            return ["*"]

        available = set(self.get_available_servers(env_vars=env_vars))
        unknown = sorted(name for name in selected if name not in available)
        if unknown:
            unknown_csv = ", ".join(unknown)
            raise ValueError(f"Unknown MCP server(s): {unknown_csv}")

        return list(selected)

    def get_tool_patterns(
        self,
        selected_mcps: Optional[List[str]] = None,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        patterns_by_server = self.loader.get_tool_patterns(env_vars=env_vars)
        if selected_mcps and "*" not in selected_mcps:
            patterns_by_server = {
                key: value
                for key, value in patterns_by_server.items()
                if key in selected_mcps
            }

        patterns: List[str] = []
        for pattern_list in patterns_by_server.values():
            patterns.extend(pattern_list)
        return patterns
