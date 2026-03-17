"""Unified persistent task-board store.

Canonical data is stored per agent at ``{data_dir}/agents/{agent_id}/task_board.json``.
A shared aggregate view is also written to ``{data_dir}/task_board.json``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


TASK_TYPES = {"bug", "feature", "chore", "research", "reminder"}
TASK_STATUSES = {"todo", "in_progress", "done", "blocked"}
TASK_PRIORITIES = {"low", "normal", "high", "critical"}
TASK_CREATORS = {"agent", "human", "cron", "heartbeat"}
AGENT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

STATUS_ALIASES = {
    "pending": "todo",
    "running": "in_progress",
    "completed": "done",
    "failed": "blocked",
    "canceled": "blocked",
}
PRIORITY_ALIASES = {
    "30": "low",
    "50": "normal",
    "70": "high",
    "90": "critical",
}


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _normalize_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return _utc_now()
    try:
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            return datetime.fromtimestamp(float(text), tz=timezone.utc).isoformat()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return _utc_now()


def _normalize_agent_id(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "unassigned"
    if AGENT_ID_PATTERN.fullmatch(normalized):
        return normalized
    return "unassigned"


@dataclass
class TaskItem:
    id: str
    title: str
    type: str = "chore"
    status: str = "todo"
    priority: str = "normal"
    agent_id: str = "unassigned"
    created_by: str = "human"
    result: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    # Legacy compatibility for old board rows and Sheets sync.
    link: str = ""


# Backward-compatible alias kept for existing imports.
BoardItem = TaskItem


class BoardStore:
    """CRUD store for unified task board items backed by JSON files."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir).expanduser().resolve()
        self._shared_file = self._data_dir / "task_board.json"
        self._agents_dir = self._data_dir / "agents"
        self._io_lock = threading.Lock()
        self._migrated = False

    # ------------------------------------------------------------------
    # Internal IO helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_value(value: object, *, allowed: set[str], default: str, aliases: Optional[Dict[str, str]] = None) -> str:
        normalized = str(value or "").strip().lower()
        if aliases and normalized in aliases:
            normalized = aliases[normalized]
        if normalized not in allowed:
            return default
        return normalized

    @staticmethod
    def _coerce_metadata(value: object) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _agent_file(self, agent_id: str) -> Path:
        normalized = _normalize_agent_id(agent_id)
        return self._agents_dir / normalized / "task_board.json"

    @staticmethod
    def _read_json_list(path: Path) -> List[dict]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def _coerce_item(self, raw: dict) -> TaskItem:
        metadata = self._coerce_metadata(raw.get("metadata"))
        link = str(raw.get("link") or metadata.get("link") or "").strip()
        if link and "link" not in metadata:
            metadata["link"] = link

        raw_agent_id = raw.get("agent_id") or metadata.get("agent_id") or ""
        item = TaskItem(
            id=str(raw.get("id") or uuid.uuid4()),
            title=str(raw.get("title") or raw.get("prompt") or "").strip() or "Untitled task",
            type=self._coerce_value(raw.get("type"), allowed=TASK_TYPES, default="chore"),
            status=self._coerce_value(
                raw.get("status"),
                allowed=TASK_STATUSES,
                default="todo",
                aliases=STATUS_ALIASES,
            ),
            priority=self._coerce_value(
                raw.get("priority") or metadata.get("priority"),
                allowed=TASK_PRIORITIES,
                default="normal",
                aliases=PRIORITY_ALIASES,
            ),
            agent_id=_normalize_agent_id(raw_agent_id),
            created_by=self._coerce_value(
                raw.get("created_by") or metadata.get("created_by") or ("agent" if raw_agent_id else "human"),
                allowed=TASK_CREATORS,
                default="human",
            ),
            result=None if raw.get("result") is None else str(raw.get("result")),
            metadata=metadata,
            created_at=_normalize_timestamp(raw.get("created_at")),
            updated_at=_normalize_timestamp(raw.get("updated_at") or raw.get("created_at")),
            link=link,
        )
        return item

    @staticmethod
    def _write_json_atomic(path: Path, payload: List[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
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
                tmp.write(data)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _sort_key(item: TaskItem) -> tuple[float, float]:
        try:
            updated = datetime.fromisoformat(item.updated_at.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            updated = 0.0
        try:
            created = datetime.fromisoformat(item.created_at.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            created = 0.0
        return (updated, created)

    def _merge_items(self, items: Iterable[TaskItem]) -> List[TaskItem]:
        by_id: Dict[str, TaskItem] = {}
        for item in items:
            existing = by_id.get(item.id)
            if existing is None or self._sort_key(item) >= self._sort_key(existing):
                by_id[item.id] = item
        merged = list(by_id.values())
        merged.sort(key=self._sort_key, reverse=True)
        return merged

    def _load_all_sources(self) -> List[TaskItem]:
        items: List[TaskItem] = []

        for path in sorted(self._agents_dir.glob("*/task_board.json")):
            for raw in self._read_json_list(path):
                items.append(self._coerce_item(raw))

        shared_rows = self._read_json_list(self._shared_file)
        if shared_rows:
            existing_ids = {item.id for item in items}
            for raw in shared_rows:
                item = self._coerce_item(raw)
                if item.id not in existing_ids:
                    items.append(item)

        return self._merge_items(items)

    def _write_items(self, items: List[TaskItem], *, mark_migrated: bool = True) -> None:
        normalized = self._merge_items(self._coerce_item(asdict(item)) for item in items)

        grouped: Dict[str, List[TaskItem]] = {}
        for item in normalized:
            grouped.setdefault(_normalize_agent_id(item.agent_id), []).append(item)

        existing_agents = {path.parent.name for path in self._agents_dir.glob("*/task_board.json")}
        for agent_id in sorted(existing_agents | set(grouped.keys())):
            agent_path = self._agent_file(agent_id)
            rows = [asdict(item) for item in grouped.get(agent_id, [])]
            if rows:
                self._write_json_atomic(agent_path, rows)
            elif agent_path.exists():
                agent_path.unlink(missing_ok=True)

        self._write_json_atomic(self._shared_file, [asdict(item) for item in normalized])
        if mark_migrated:
            self._migrated = True

    def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        with self._io_lock:
            if self._migrated:
                return
            merged = self._load_all_sources()
            if merged:
                self._write_items(merged, mark_migrated=True)
            else:
                self._migrated = True

    def _read_items(self) -> List[TaskItem]:
        self._ensure_migrated()
        with self._io_lock:
            return self._load_all_sources()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_items(
        self,
        type_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        agent_id: Optional[str] = None,
        priority_filter: Optional[str] = None,
        created_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[TaskItem]:
        items = self._read_items()
        if type_filter:
            normalized_type = str(type_filter).strip().lower()
            if normalized_type not in TASK_TYPES:
                return []
            items = [i for i in items if i.type == normalized_type]
        if status_filter:
            normalized_status = str(status_filter).strip().lower()
            status = STATUS_ALIASES.get(normalized_status, normalized_status)
            if status not in TASK_STATUSES:
                return []
            items = [i for i in items if i.status == status]
        if priority_filter:
            normalized_priority = str(priority_filter).strip().lower()
            priority = PRIORITY_ALIASES.get(normalized_priority, normalized_priority)
            if priority not in TASK_PRIORITIES:
                return []
            items = [i for i in items if i.priority == priority]
        if created_by:
            creator = str(created_by).strip().lower()
            if creator not in TASK_CREATORS:
                return []
            items = [i for i in items if i.created_by == creator]
        if agent_id:
            target_agent = _normalize_agent_id(agent_id)
            items = [i for i in items if i.agent_id == target_agent]
        if limit is not None:
            items = items[: max(1, int(limit))]
        return items

    def get_item(self, item_id: str) -> Optional[TaskItem]:
        for item in self._read_items():
            if item.id == item_id:
                return item
        return None

    def insert(
        self,
        type: str,
        title: str,
        link: str = "",
        status: str = "todo",
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        priority: str = "normal",
        created_by: str = "human",
        result: Optional[str] = None,
    ) -> TaskItem:
        with self._io_lock:
            items = self._load_all_sources()
            meta = dict(metadata or {})
            normalized_link = str(link or "").strip()
            if normalized_link:
                meta.setdefault("link", normalized_link)
            item = TaskItem(
                id=str(uuid.uuid4()),
                title=str(title or "").strip() or "Untitled task",
                type=self._coerce_value(type, allowed=TASK_TYPES, default="chore"),
                status=self._coerce_value(status, allowed=TASK_STATUSES, default="todo", aliases=STATUS_ALIASES),
                priority=self._coerce_value(
                    priority or meta.get("priority"),
                    allowed=TASK_PRIORITIES,
                    default="normal",
                    aliases=PRIORITY_ALIASES,
                ),
                agent_id=_normalize_agent_id(agent_id or meta.get("agent_id")),
                created_by=self._coerce_value(
                    created_by or meta.get("created_by"),
                    allowed=TASK_CREATORS,
                    default="human",
                ),
                result=None if result is None else str(result),
                metadata=meta,
                created_at=_utc_now(),
                updated_at=_utc_now(),
                link=normalized_link,
            )
            items.append(item)
            self._write_items(items)
            return item

    def update(self, item_id: str, **kwargs) -> Optional[TaskItem]:
        with self._io_lock:
            items = self._load_all_sources()
            for idx, item in enumerate(items):
                if item.id != item_id:
                    continue

                changed = False
                if "title" in kwargs and kwargs["title"] is not None:
                    item.title = str(kwargs["title"]).strip() or item.title
                    changed = True
                if "type" in kwargs and kwargs["type"] is not None:
                    item.type = self._coerce_value(kwargs["type"], allowed=TASK_TYPES, default=item.type)
                    changed = True
                if "status" in kwargs and kwargs["status"] is not None:
                    item.status = self._coerce_value(
                        kwargs["status"],
                        allowed=TASK_STATUSES,
                        default=item.status,
                        aliases=STATUS_ALIASES,
                    )
                    changed = True
                if "priority" in kwargs and kwargs["priority"] is not None:
                    item.priority = self._coerce_value(
                        kwargs["priority"],
                        allowed=TASK_PRIORITIES,
                        default=item.priority,
                        aliases=PRIORITY_ALIASES,
                    )
                    changed = True
                if "agent_id" in kwargs and kwargs["agent_id"] is not None:
                    item.agent_id = _normalize_agent_id(kwargs["agent_id"])
                    changed = True
                if "created_by" in kwargs and kwargs["created_by"] is not None:
                    item.created_by = self._coerce_value(
                        kwargs["created_by"],
                        allowed=TASK_CREATORS,
                        default=item.created_by,
                    )
                    changed = True
                if "result" in kwargs:
                    value = kwargs.get("result")
                    item.result = None if value is None else str(value)
                    changed = True
                if "metadata" in kwargs and kwargs["metadata"] is not None:
                    item.metadata = self._coerce_metadata(kwargs["metadata"])
                    changed = True
                if "link" in kwargs and kwargs["link"] is not None:
                    item.link = str(kwargs["link"]).strip()
                    if item.link:
                        item.metadata["link"] = item.link
                    changed = True

                if not changed:
                    return item

                item.updated_at = _utc_now()
                items[idx] = item
                self._write_items(items)
                return item
        return None

    def complete(self, item_id: str, result: Optional[str] = None) -> Optional[TaskItem]:
        return self.update(item_id, status="done", result=result)

    def delete(self, item_id: str) -> bool:
        with self._io_lock:
            items = self._load_all_sources()
            filtered = [item for item in items if item.id != item_id]
            if len(filtered) == len(items):
                return False
            self._write_items(filtered)
            return True
