"""Configuration loading for g3lobster."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class AgentsConfig:
    data_dir: str = "./data"
    compact_threshold: int = 40
    compact_keep_ratio: float = 0.25
    compact_chunk_size: int = 10
    procedure_min_frequency: int = 3
    memory_max_sections: int = 50
    context_messages: int = 12
    health_check_interval_s: int = 30
    stuck_timeout_s: int = 300


@dataclass
class GeminiConfig:
    command: str = "gemini"
    args: List[str] = field(default_factory=lambda: ["-y"])
    workspace_dir: str = "."
    response_timeout_s: float = 120.0
    idle_read_window_s: float = 0.6


@dataclass
class MCPConfig:
    config_dir: str = "./config/mcp"
    default_servers: List[str] = field(default_factory=lambda: ["*"])


@dataclass
class ChatConfig:
    enabled: bool = False
    space_id: Optional[str] = None
    space_name: Optional[str] = None
    poll_interval_s: float = 2.0


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 20001


@dataclass
class AppConfig:
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _coerce_value(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        return _to_bool(raw)
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        # Comma-separated list override.
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def _apply_env_overrides(section_name: str, section: Any) -> None:
    prefix = f"G3LOBSTER_{section_name.upper()}_"
    for key, value in vars(section).items():
        env_key = f"{prefix}{key.upper()}"
        if env_key in os.environ:
            setattr(section, key, _coerce_value(os.environ[env_key], value))


def _resolve_path(path: str, config_path: Path) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str((config_path.parent / p).resolve())


def _legacy_agents_section(data: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if data.get("agents"):
        return data.get("agents") or {}

    pool = data.get("pool") or {}
    memory = data.get("memory") or {}
    if not pool and not memory:
        return {}

    return {
        "data_dir": memory.get("data_dir", "./data"),
        "compact_threshold": memory.get("compact_threshold", 40),
        "compact_keep_ratio": memory.get("compact_keep_ratio", 0.25),
        "compact_chunk_size": memory.get("compact_chunk_size", 10),
        "procedure_min_frequency": memory.get("procedure_min_frequency", 3),
        "memory_max_sections": memory.get("memory_max_sections", 50),
        "context_messages": memory.get("context_messages", 12),
        "health_check_interval_s": pool.get("health_check_interval_s", 30),
        "stuck_timeout_s": pool.get("stuck_timeout_s", 300),
    }


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load config from YAML and environment variables."""
    path = Path(config_path or "config.yaml").expanduser().resolve()
    data: Dict[str, Dict[str, Any]] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
            if isinstance(loaded, dict):
                data = loaded

    config = AppConfig(
        agents=AgentsConfig(**_legacy_agents_section(data)),
        gemini=GeminiConfig(**(data.get("gemini") or {})),
        mcp=MCPConfig(**(data.get("mcp") or {})),
        chat=ChatConfig(**(data.get("chat") or {})),
        server=ServerConfig(**(data.get("server") or {})),
    )

    _apply_env_overrides("agents", config.agents)
    _apply_env_overrides("gemini", config.gemini)
    _apply_env_overrides("mcp", config.mcp)
    _apply_env_overrides("chat", config.chat)
    _apply_env_overrides("server", config.server)

    config.mcp.config_dir = _resolve_path(config.mcp.config_dir, path)
    config.agents.data_dir = _resolve_path(config.agents.data_dir, path)
    config.gemini.workspace_dir = _resolve_path(config.gemini.workspace_dir, path)

    return config


def config_to_dict(config: AppConfig) -> Dict[str, Any]:
    """Convert runtime config dataclasses to a plain dict."""
    return asdict(config)


def save_chat_config(chat: ChatConfig, config_path: str) -> None:
    """Persist only the chat section to YAML using an atomic write."""
    path = Path(config_path).expanduser().resolve()
    payload: Dict[str, Any] = {}

    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
            if isinstance(loaded, dict):
                payload = loaded

    payload["chat"] = {
        "enabled": chat.enabled,
        "space_id": chat.space_id,
        "space_name": chat.space_name,
        "poll_interval_s": chat.poll_interval_s,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    os.replace(tmp_path, path)
