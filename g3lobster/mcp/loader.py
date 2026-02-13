"""Load YAML MCP definitions."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


class MCPConfigLoader:
    """Loads YAML MCP configs and performs env variable substitution."""

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)

    def _substitute(self, value: Any, variables: Dict[str, str]) -> Any:
        if isinstance(value, str):
            def replace(match: re.Match) -> str:
                key = match.group(1)
                return variables.get(key, os.environ.get(key, ""))

            return _ENV_PATTERN.sub(replace, value)

        if isinstance(value, list):
            return [self._substitute(item, variables) for item in value]

        if isinstance(value, dict):
            return {k: self._substitute(v, variables) for k, v in value.items()}

        return value

    def load_all(self, env_vars: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, Any]]:
        if not self.config_dir.exists():
            return {}

        variables = env_vars or {}
        loaded: Dict[str, Dict[str, Any]] = {}

        for path in sorted(self.config_dir.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}

            if not isinstance(raw, dict):
                continue
            if not raw.get("enabled", True):
                continue

            substituted = self._substitute(raw, variables)
            name = substituted.get("name")
            if not name:
                continue
            loaded[str(name)] = substituted

        return loaded

    def get_tool_patterns(self, env_vars: Optional[Dict[str, str]] = None) -> Dict[str, List[str]]:
        configs = self.load_all(env_vars=env_vars)
        patterns: Dict[str, List[str]] = {}
        for name, config in configs.items():
            if "tool_patterns" in config and isinstance(config["tool_patterns"], list):
                patterns[name] = [str(item) for item in config["tool_patterns"]]
        return patterns
