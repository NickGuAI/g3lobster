"""File-based standup storage.

Configs are persisted at ``{data_dir}/{agent_id}/standup.json``.
Daily entries live at ``{data_dir}/{agent_id}/standup_entries/{date}.json``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class TeamMember:
    user_id: str            # internal user identifier
    display_name: str
    chat_user_id: str = ""  # Google Chat user ID for @-mentions


@dataclass
class StandupConfig:
    agent_id: str
    team_members: List[dict]                # List of TeamMember dicts
    prompt_schedule: str = "0 9 * * 1-5"    # cron expr for prompts (weekdays 9am)
    summary_schedule: str = "0 17 * * 1-5"  # cron expr for summary (weekdays 5pm)
    prompt_template: str = "What did you do yesterday? What are you doing today? Any blockers?"
    summary_space_id: str = ""              # Google Chat space to post summary
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())


@dataclass
class StandupEntry:
    user_id: str
    display_name: str
    date: str               # YYYY-MM-DD
    response: str
    blockers: List[str]     # extracted blocker strings
    timestamp: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())


_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _safe_agent_id(agent_id: str) -> str:
    """Validate agent_id is filesystem-safe (no path traversal)."""
    safe = agent_id.strip()
    if not safe or not _ID_RE.match(safe):
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    return safe


class StandupStore:
    """CRUD store for per-agent standup configs and entries backed by local JSON files."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)

    # ------------------------------------------------------------------
    # Private helpers — paths
    # ------------------------------------------------------------------

    def _config_file(self, agent_id: str) -> Path:
        safe = _safe_agent_id(agent_id)
        return self._data_dir / safe / "standup.json"

    def _entries_dir(self, agent_id: str) -> Path:
        safe = _safe_agent_id(agent_id)
        return self._data_dir / safe / "standup_entries"

    def _entries_file(self, agent_id: str, date: str) -> Path:
        return self._entries_dir(agent_id) / f"{date}.json"

    # ------------------------------------------------------------------
    # Private helpers — config I/O
    # ------------------------------------------------------------------

    def _read_config(self, agent_id: str) -> Optional[StandupConfig]:
        path = self._config_file(agent_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            return StandupConfig(**{k: v for k, v in raw.items() if k in StandupConfig.__dataclass_fields__})
        except TypeError:
            return None

    def _write_config(self, agent_id: str, config: StandupConfig) -> None:
        path = self._config_file(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(config), indent=2, ensure_ascii=False)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                tmp.write(payload + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # Private helpers — entries I/O
    # ------------------------------------------------------------------

    def _read_entries(self, agent_id: str, date: str) -> List[StandupEntry]:
        path = self._entries_file(agent_id, date)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        entries: List[StandupEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(StandupEntry(**{k: v for k, v in item.items() if k in StandupEntry.__dataclass_fields__}))
            except TypeError:
                continue
        return entries

    def _write_entries(self, agent_id: str, date: str, entries: List[StandupEntry]) -> None:
        path = self._entries_file(agent_id, date)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                tmp.write(payload + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # Public API — config
    # ------------------------------------------------------------------

    def get_config(self, agent_id: str) -> Optional[StandupConfig]:
        return self._read_config(agent_id)

    def save_config(self, agent_id: str, config: StandupConfig) -> StandupConfig:
        config.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._write_config(agent_id, config)
        return config

    def delete_config(self, agent_id: str) -> bool:
        path = self._config_file(agent_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    # ------------------------------------------------------------------
    # Public API — entries
    # ------------------------------------------------------------------

    def add_entry(self, agent_id: str, entry: StandupEntry) -> StandupEntry:
        entries = self._read_entries(agent_id, entry.date)
        entries.append(entry)
        self._write_entries(agent_id, entry.date, entries)
        return entry

    def get_entries(self, agent_id: str, date: str) -> List[StandupEntry]:
        return self._read_entries(agent_id, date)

    def get_entries_range(self, agent_id: str, start_date: str, end_date: str) -> Dict[str, List[StandupEntry]]:
        """Return {date: entries} for all dates in [start_date, end_date] that have data."""
        entries_dir = self._entries_dir(agent_id)
        result: Dict[str, List[StandupEntry]] = {}
        if not entries_dir.exists():
            return result
        for entry_file in sorted(entries_dir.iterdir()):
            if not entry_file.is_file() or not entry_file.name.endswith(".json"):
                continue
            date = entry_file.name.removesuffix(".json")
            if start_date <= date <= end_date:
                day_entries = self._read_entries(agent_id, date)
                if day_entries:
                    result[date] = day_entries
        return result

    # ------------------------------------------------------------------
    # Public API — discovery
    # ------------------------------------------------------------------

    def list_configured_agents(self) -> List[str]:
        """Scan data_dir for agents that have a standup.json config."""
        result: List[str] = []
        if not self._data_dir.exists():
            return result
        for agent_dir in self._data_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            standup_file = agent_dir / "standup.json"
            if not standup_file.exists():
                continue
            try:
                _safe_agent_id(agent_dir.name)
                result.append(agent_dir.name)
            except ValueError:
                continue
        return result
