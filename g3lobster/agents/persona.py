"""Agent persona data model and file persistence."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

AGENT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class AgentPersona:
    """Named agent metadata and persona prompt fragment."""

    id: str
    name: str
    emoji: str = "🤖"
    soul: str = ""
    model: str = "gemini"
    mcp_servers: List[str] = field(default_factory=lambda: ["*"])
    bot_user_id: Optional[str] = None
    enabled: bool = True
    created_at: str = field(default_factory=lambda: _utc_now())
    updated_at: str = field(default_factory=lambda: _utc_now())

    def __post_init__(self) -> None:
        if not is_valid_agent_id(self.id):
            raise ValueError(
                "Agent id must be a slug using lowercase letters, numbers, and dashes"
            )
        self.id = self.id.strip()
        self.name = self.name.strip() or self.id
        self.emoji = self.emoji.strip() or "🤖"
        self.soul = self.soul.strip()
        self.model = self.model.strip() or "gemini"
        self.mcp_servers = [str(item).strip() for item in self.mcp_servers if str(item).strip()] or ["*"]
        if self.bot_user_id:
            self.bot_user_id = str(self.bot_user_id).strip() or None
        else:
            self.bot_user_id = None

    def to_agent_json(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "model": self.model,
            "mcp_servers": list(self.mcp_servers),
            "bot_user_id": self.bot_user_id,
            "enabled": bool(self.enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def is_valid_agent_id(agent_id: str) -> bool:
    return bool(AGENT_ID_PATTERN.fullmatch(str(agent_id or "").strip()))


def slugify_agent_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized or "agent"


def ensure_unique_agent_id(data_dir: str, preferred: str) -> str:
    base = preferred if is_valid_agent_id(preferred) else slugify_agent_id(preferred)
    root = _agents_root(data_dir)

    if not (root / base).exists():
        return base

    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if not (root / candidate).exists():
            return candidate
    raise RuntimeError(f"Could not generate unique agent id for '{preferred}'")


def _agents_root(data_dir: str) -> Path:
    return Path(data_dir).expanduser().resolve() / "agents"


def agent_dir(data_dir: str, agent_id: str) -> Path:
    if not is_valid_agent_id(agent_id):
        raise ValueError(f"Invalid agent id: {agent_id}")
    return _agents_root(data_dir) / agent_id


def _ensure_agent_layout(path: Path) -> None:
    (path / "memory").mkdir(parents=True, exist_ok=True)
    (path / "sessions").mkdir(parents=True, exist_ok=True)

    memory_file = path / "memory" / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("# MEMORY\n\n", encoding="utf-8")


def load_persona(data_dir: str, agent_id: str) -> Optional[AgentPersona]:
    """Load an agent persona from disk, returning None when it does not exist."""
    path = agent_dir(data_dir, agent_id)
    agent_json = path / "agent.json"
    if not agent_json.exists():
        return None

    payload = json.loads(agent_json.read_text(encoding="utf-8"))
    soul_file = path / "SOUL.md"
    soul = soul_file.read_text(encoding="utf-8") if soul_file.exists() else ""

    return AgentPersona(
        id=agent_id,
        name=str(payload.get("name", agent_id)),
        emoji=str(payload.get("emoji", "🤖")),
        soul=soul,
        model=str(payload.get("model", "gemini")),
        mcp_servers=list(payload.get("mcp_servers") or ["*"]),
        bot_user_id=payload.get("bot_user_id"),
        enabled=bool(payload.get("enabled", True)),
        created_at=str(payload.get("created_at") or _utc_now()),
        updated_at=str(payload.get("updated_at") or _utc_now()),
    )


def save_persona(data_dir: str, persona: AgentPersona) -> AgentPersona:
    """Persist persona metadata and SOUL.md to disk."""
    path = agent_dir(data_dir, persona.id)
    _ensure_agent_layout(path)

    existing = load_persona(data_dir, persona.id)
    now = _utc_now()

    saved = AgentPersona(
        id=persona.id,
        name=persona.name,
        emoji=persona.emoji,
        soul=persona.soul,
        model=persona.model,
        mcp_servers=list(persona.mcp_servers),
        bot_user_id=persona.bot_user_id,
        enabled=persona.enabled,
        created_at=(existing.created_at if existing else persona.created_at) or now,
        updated_at=now,
    )

    (path / "agent.json").write_text(
        json.dumps(saved.to_agent_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    soul_text = saved.soul.strip()
    if soul_text:
        soul_text += "\n"
    (path / "SOUL.md").write_text(soul_text, encoding="utf-8")

    return saved


def list_personas(data_dir: str) -> List[AgentPersona]:
    """List all valid persona directories under data/agents."""
    root = _agents_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)

    personas: List[AgentPersona] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        agent_id = path.name
        if not is_valid_agent_id(agent_id):
            continue
        persona = load_persona(data_dir, agent_id)
        if persona:
            personas.append(persona)
    return personas


def delete_persona(data_dir: str, agent_id: str) -> bool:
    """Delete an agent persona directory from disk."""
    path = agent_dir(data_dir, agent_id)
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True
