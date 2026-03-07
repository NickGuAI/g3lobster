"""File-based cron task storage.

Tasks are persisted as JSON at ``{data_dir}/{agent_id}/crons.json``.
Each task object matches the :class:`CronTask` schema.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class CronTask:
    id: str
    agent_id: str
    schedule: str          # cron expression e.g. "0 9 * * *"
    instruction: str       # prompt sent to the agent on each tick
    enabled: bool = True
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())


_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _safe_agent_id(agent_id: str) -> str:
    """Validate agent_id is filesystem-safe (no path traversal)."""
    safe = agent_id.strip()
    if not safe or not _ID_RE.match(safe):
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    return safe


class CronStore:
    """CRUD store for per-agent cron tasks backed by local JSON files."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)

    def _task_file(self, agent_id: str) -> Path:
        safe = _safe_agent_id(agent_id)
        return self._data_dir / safe / "crons.json"

    def _read_tasks(self, agent_id: str) -> List[CronTask]:
        path = self._task_file(agent_id)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        tasks: List[CronTask] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(CronTask(**{k: v for k, v in item.items() if k in CronTask.__dataclass_fields__}))
            except TypeError:
                continue
        return tasks

    def _write_tasks(self, agent_id: str, tasks: List[CronTask]) -> None:
        path = self._task_file(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([asdict(t) for t in tasks], indent=2, ensure_ascii=False)
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
    # Public API
    # ------------------------------------------------------------------

    def list_tasks(self, agent_id: str) -> List[CronTask]:
        return self._read_tasks(agent_id)

    def get_task(self, agent_id: str, task_id: str) -> Optional[CronTask]:
        for task in self._read_tasks(agent_id):
            if task.id == task_id:
                return task
        return None

    def add_task(self, agent_id: str, schedule: str, instruction: str) -> CronTask:
        tasks = self._read_tasks(agent_id)
        task = CronTask(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            schedule=schedule.strip(),
            instruction=instruction.strip(),
        )
        tasks.append(task)
        self._write_tasks(agent_id, tasks)
        return task

    def update_task(self, agent_id: str, task_id: str, **kwargs) -> Optional[CronTask]:
        tasks = self._read_tasks(agent_id)
        for i, task in enumerate(tasks):
            if task.id == task_id:
                allowed = {"schedule", "instruction", "enabled", "last_run", "next_run"}
                for key, value in kwargs.items():
                    if key in allowed:
                        setattr(tasks[i], key, value)
                self._write_tasks(agent_id, tasks)
                return tasks[i]
        return None

    def delete_task(self, agent_id: str, task_id: str) -> bool:
        tasks = self._read_tasks(agent_id)
        filtered = [t for t in tasks if t.id != task_id]
        if len(filtered) == len(tasks):
            return False
        self._write_tasks(agent_id, filtered)
        return True

    def list_all_enabled(self) -> List[CronTask]:
        """Return all enabled tasks across all agents in data_dir."""
        result: List[CronTask] = []
        if not self._data_dir.exists():
            return result
        for agent_dir in self._data_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            cron_file = agent_dir / "crons.json"
            if not cron_file.exists():
                continue
            try:
                agent_id = agent_dir.name
                result.extend(t for t in self._read_tasks(agent_id) if t.enabled)
            except ValueError:
                continue
        return result
