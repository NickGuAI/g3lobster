"""File-based task board storage.

Tasks are persisted in a single JSON file at ``{data_dir}/task_board.json``.
Each item is a generic ``(type, link, status)`` bundle with optional metadata.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class BoardItem:
    id: str
    type: str              # "bug", "feature", "chore", "research", etc.
    title: str
    link: str              # URL to external resource
    status: str            # "todo", "in_progress", "done", "blocked"
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())


class BoardStore:
    """CRUD store for shared task board items backed by a JSON file."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._file = self._data_dir / "task_board.json"

    def _read_items(self) -> List[BoardItem]:
        if not self._file.exists():
            return []
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        items: List[BoardItem] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                items.append(BoardItem(**{k: v for k, v in entry.items() if k in BoardItem.__dataclass_fields__}))
            except TypeError:
                continue
        return items

    def _write_items(self, items: List[BoardItem]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([asdict(i) for i in items], indent=2, ensure_ascii=False)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._data_dir,
            prefix="task_board.json.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                tmp.write(payload + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, self._file)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_items(
        self,
        type_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[BoardItem]:
        items = self._read_items()
        if type_filter:
            items = [i for i in items if i.type == type_filter]
        if status_filter:
            items = [i for i in items if i.status == status_filter]
        if agent_id:
            items = [i for i in items if i.agent_id == agent_id]
        return items

    def get_item(self, item_id: str) -> Optional[BoardItem]:
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
    ) -> BoardItem:
        items = self._read_items()
        item = BoardItem(
            id=str(uuid.uuid4()),
            type=type.strip(),
            title=title.strip(),
            link=link.strip(),
            status=status.strip(),
            agent_id=agent_id,
            metadata=metadata or {},
        )
        items.append(item)
        self._write_items(items)
        return item

    def update(self, item_id: str, **kwargs) -> Optional[BoardItem]:
        items = self._read_items()
        for i, item in enumerate(items):
            if item.id == item_id:
                allowed = {"type", "title", "link", "status", "agent_id", "metadata"}
                changed = False
                for key, value in kwargs.items():
                    if key in allowed:
                        setattr(items[i], key, value)
                        changed = True
                if changed:
                    items[i].updated_at = datetime.now(tz=timezone.utc).isoformat()
                self._write_items(items)
                return items[i]
        return None

    def delete(self, item_id: str) -> bool:
        items = self._read_items()
        filtered = [i for i in items if i.id != item_id]
        if len(filtered) == len(items):
            return False
        self._write_items(filtered)
        return True
