"""Configuration loading for g3lobster."""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

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
    heartbeat_enabled: bool = False
    heartbeat_interval_s: int = 300
    health_check_interval_s: int = 30
    stuck_timeout_s: int = 0  # 0 disables stuck-agent auto-restart
    journal_salience_default: str = "normal"
    journal_association_decay_days: int = 90
    consolidation_enabled: bool = True
    consolidation_schedule: str = "0 2 * * *"
    consolidation_days_window: int = 7
    consolidation_stale_days: int = 30


@dataclass
class GeminiConfig:
    command: str = "gemini"
    args: List[str] = field(default_factory=lambda: ["-y"])
    workspace_dir: str = "."
    response_timeout_s: float = 0.0  # 0 disables per-task timeout
    idle_read_window_s: float = 0.6


@dataclass
class MCPConfig:
    config_dir: str = "./config/mcp"
    default_servers: List[str] = field(default_factory=lambda: ["*"])


@dataclass
class ChatConfig:
    enabled: bool = False
    # Legacy defaults retained for backward compatibility and migration.
    # Per-agent space assignment now lives on AgentPersona.space_id.
    space_id: Optional[str] = None
    space_name: Optional[str] = None
    poll_interval_s: float = 2.0
    concierge_enabled: bool = False
    concierge_agent_id: str = "concierge"
    debounce_window_ms: int = 2000
    stream_update_interval_s: float = 1.0


@dataclass
class EmailConfig:
    enabled: bool = False
    poll_interval_s: float = 30.0
    base_address: str = ""   # e.g. "helper@example.com"
    auth_data_dir: str = "./data/email_auth"


@dataclass
class CalendarConfig:
    enabled: bool = False
    poll_interval_s: float = 300.0
    lookahead_minutes: int = 15
    max_attendees: int = 15
    auth_data_dir: str = ""  # defaults to chat auth dir when empty
    check_interval_cron: str = "*/5 * * * *"
    auto_respond_template: str = "{emoji} {name} is in {event_type} until {end_time}. Message saved — I'll deliver a summary when they're back."


@dataclass
class CronConfig:
    enabled: bool = True


@dataclass
class AlertsConfig:
    enabled: bool = False
    chat_space_id: str = ""
    webhook_url: str = ""
    email_address: str = ""  # admin email for alerts via EmailBridge
    min_severity: str = "warning"  # warning | error | critical
    rate_limit_s: int = 300  # 1 per agent per 5 minutes


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 20001


@dataclass
class SubagentConfig:
    enabled: bool = False
    session_prefix: str = "g3lobster"
    default_timeout_s: float = 300.0
    stream_json: bool = True


@dataclass
class ControlPlaneConfig:
    enabled: bool = True
    queue_depth: int = 5
    max_tasks: int = 5000
    tmux_enabled: bool = False
    tmux_session_prefix: str = "g3l"
    tmux_idle_ttl_s: float = 1800.0
    tmux_max_sessions_per_agent: int = 2


@dataclass
class AuthConfig:
    enabled: bool = False
    api_key: str = ""


@dataclass
class TasksConfig:
    enabled: bool = True
    google_sheet_id: Optional[str] = None
    google_credentials_path: Optional[str] = None


@dataclass
class AppConfig:
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    cron: CronConfig = field(default_factory=CronConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    subagent: SubagentConfig = field(default_factory=SubagentConfig)
    control_plane: ControlPlaneConfig = field(default_factory=ControlPlaneConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    tasks: TasksConfig = field(default_factory=TasksConfig)
    debug_mode: bool = False


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_space_id(raw: object) -> Optional[str]:
    """Normalize Google Chat space ids to ``spaces/<id>`` format."""
    value = str(raw or "").strip()
    if not value:
        return None
    if value.startswith("space/") and not value.startswith("spaces/"):
        value = "spaces/" + value[len("space/"):]
    if not value.startswith("spaces/"):
        value = "spaces/" + value
    return value


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


def _filter_fields(cls: Type[_T], raw: Dict[str, Any], section: str) -> Dict[str, Any]:
    """Return only recognized fields from *raw*, logging a warning for unknown keys."""
    known = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = set(raw) - known
    if unknown:
        logger.warning("Unknown config keys in [%s]: %s — ignoring", section, sorted(unknown))
    return {k: v for k, v in raw.items() if k in known}


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
        "stuck_timeout_s": pool.get("stuck_timeout_s", 0),
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
        agents=AgentsConfig(**_filter_fields(AgentsConfig, _legacy_agents_section(data), "agents")),
        gemini=GeminiConfig(**_filter_fields(GeminiConfig, data.get("gemini") or {}, "gemini")),
        mcp=MCPConfig(**_filter_fields(MCPConfig, data.get("mcp") or {}, "mcp")),
        chat=ChatConfig(**_filter_fields(ChatConfig, data.get("chat") or {}, "chat")),
        email=EmailConfig(**_filter_fields(EmailConfig, data.get("email") or {}, "email")),
        calendar=CalendarConfig(**_filter_fields(CalendarConfig, data.get("calendar") or {}, "calendar")),
        cron=CronConfig(**_filter_fields(CronConfig, data.get("cron") or {}, "cron")),
        server=ServerConfig(**_filter_fields(ServerConfig, data.get("server") or {}, "server")),
        alerts=AlertsConfig(**_filter_fields(AlertsConfig, data.get("alerts") or {}, "alerts")),
        subagent=SubagentConfig(**_filter_fields(SubagentConfig, data.get("subagent") or {}, "subagent")),
        control_plane=ControlPlaneConfig(
            **_filter_fields(ControlPlaneConfig, data.get("control_plane") or {}, "control_plane")
        ),
        auth=AuthConfig(**_filter_fields(AuthConfig, data.get("auth") or {}, "auth")),
        tasks=TasksConfig(**_filter_fields(TasksConfig, data.get("tasks") or {}, "tasks")),
        debug_mode=bool(data.get("debug_mode", False)),
    )

    _apply_env_overrides("agents", config.agents)
    _apply_env_overrides("gemini", config.gemini)
    _apply_env_overrides("mcp", config.mcp)
    _apply_env_overrides("chat", config.chat)
    _apply_env_overrides("email", config.email)
    _apply_env_overrides("calendar", config.calendar)
    _apply_env_overrides("cron", config.cron)
    _apply_env_overrides("server", config.server)
    _apply_env_overrides("alerts", config.alerts)
    _apply_env_overrides("subagent", config.subagent)
    _apply_env_overrides("control_plane", config.control_plane)
    _apply_env_overrides("auth", config.auth)
    _apply_env_overrides("tasks", config.tasks)

    debug_env = os.environ.get("G3LOBSTER_DEBUG_MODE", "")
    if debug_env:
        config.debug_mode = _to_bool(debug_env)

    config.mcp.config_dir = _resolve_path(config.mcp.config_dir, path)
    config.agents.data_dir = _resolve_path(config.agents.data_dir, path)
    config.gemini.workspace_dir = _resolve_path(config.gemini.workspace_dir, path)
    config.email.auth_data_dir = _resolve_path(config.email.auth_data_dir, path)
    if config.calendar.auth_data_dir:
        config.calendar.auth_data_dir = _resolve_path(config.calendar.auth_data_dir, path)

    return config


def config_to_dict(config: AppConfig) -> Dict[str, Any]:
    """Convert runtime config dataclasses to a plain dict."""
    return asdict(config)


def save_chat_config(chat: ChatConfig, config_path: str) -> None:
    """Persist the chat section to YAML using an atomic write.

    The ``space_id`` and ``space_name`` keys remain for backward
    compatibility as legacy defaults for per-agent bridge migration.
    """
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
        "concierge_enabled": chat.concierge_enabled,
        "concierge_agent_id": chat.concierge_agent_id,
        "stream_update_interval_s": chat.stream_update_interval_s,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    os.replace(tmp_path, path)
