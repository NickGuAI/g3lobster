"""Salience-classified journal entries with an association graph.

Entries are stored as daily JSONL files. Each entry carries a salience
level that controls retrieval priority. An optional association graph
links entries together via weighted, typed edges.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SalienceLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    NOISE = "noise"

    @classmethod
    def from_value(cls, value: str | None) -> "SalienceLevel":
        if value is None:
            return cls.NORMAL
        normalized = str(value).strip().lower()
        mapping = {
            "critical": cls.CRITICAL,
            "high": cls.HIGH,
            "normal": cls.NORMAL,
            "low": cls.LOW,
            "noise": cls.NOISE,
        }
        return mapping.get(normalized, cls.NORMAL)

    @property
    def weight(self) -> float:
        weights = {
            SalienceLevel.CRITICAL: 5.0,
            SalienceLevel.HIGH: 3.0,
            SalienceLevel.NORMAL: 1.0,
            SalienceLevel.LOW: 0.5,
            SalienceLevel.NOISE: 0.1,
        }
        return weights[self]


@dataclass
class JournalEntry:
    content: str
    salience: SalienceLevel = SalienceLevel.NORMAL
    tags: List[str] = field(default_factory=list)
    source_session: str = ""
    associations: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "content": self.content,
            "salience": self.salience.value,
            "tags": list(self.tags),
            "source_session": self.source_session,
            "associations": list(self.associations),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JournalEntry":
        return cls(
            id=str(data.get("id", str(uuid.uuid4()))),
            timestamp=str(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            content=str(data.get("content", "")),
            salience=SalienceLevel.from_value(data.get("salience")),
            tags=list(data.get("tags", [])),
            source_session=str(data.get("source_session", "")),
            associations=list(data.get("associations", [])),
        )


class JournalStore:
    """Filesystem-backed journal store using daily JSONL files."""

    def __init__(self, daily_dir: Path) -> None:
        self.daily_dir = daily_dir
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def _journal_path(self, day: date) -> Path:
        return self.daily_dir / f"{day.isoformat()}.jsonl"

    def append(self, entry: JournalEntry, day: Optional[date] = None) -> JournalEntry:
        target_day = day or date.today()
        path = self._journal_path(target_day)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")
        return entry

    def query(
        self,
        *,
        salience_min: Optional[SalienceLevel] = None,
        tags: Optional[List[str]] = None,
        date_start: Optional[date] = None,
        date_end: Optional[date] = None,
        limit: int = 50,
    ) -> List[JournalEntry]:
        entries: List[JournalEntry] = []

        for path in sorted(self.daily_dir.glob("*.jsonl")):
            # Extract date from filename for range filtering.
            try:
                file_date = date.fromisoformat(path.stem)
            except ValueError:
                continue

            if date_start and file_date < date_start:
                continue
            if date_end and file_date > date_end:
                continue

            entries.extend(self._read_jsonl(path))

        # Filter by minimum salience level.
        if salience_min is not None:
            min_weight = salience_min.weight
            entries = [e for e in entries if e.salience.weight >= min_weight]

        # Filter by tags (entry must have at least one matching tag).
        if tags is not None:
            tag_set = set(tags)
            entries = [e for e in entries if tag_set & set(e.tags)]

        # Sort by timestamp descending.
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]

    def get(self, entry_id: str) -> Optional[JournalEntry]:
        for path in self.daily_dir.glob("*.jsonl"):
            for entry in self._read_jsonl(path):
                if entry.id == entry_id:
                    return entry
        return None

    @staticmethod
    def _read_jsonl(path: Path) -> List[JournalEntry]:
        entries: List[JournalEntry] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(JournalEntry.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        except OSError:
            pass
        return entries


class AssociationGraph:
    """JSONL-backed association graph linking journal entries."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation_type: str = "related",
        weight: float = 1.0,
    ) -> None:
        edge = {
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": relation_type,
            "weight": weight,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(edge, ensure_ascii=False) + "\n")

    def get_associations(self, entry_id: str) -> List[dict]:
        edges: List[dict] = []
        for edge in self._read_all():
            if edge.get("source_id") == entry_id or edge.get("target_id") == entry_id:
                edges.append(edge)
        return edges

    def remove_edges(self, entry_id: str) -> None:
        remaining = [
            edge
            for edge in self._read_all()
            if edge.get("source_id") != entry_id and edge.get("target_id") != entry_id
        ]
        self._write_all(remaining)

    def _read_all(self) -> List[dict]:
        edges: List[dict] = []
        if not self.path.exists():
            return edges
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    edges.append(json.loads(line))
                except (json.JSONDecodeError, TypeError):
                    continue
        except OSError:
            pass
        return edges

    def _write_all(self, edges: List[dict]) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            for edge in edges:
                fh.write(json.dumps(edge, ensure_ascii=False) + "\n")
