"""Salience-classified journal system.

Entries are persisted as JSONL files organised by day (daily/YYYY-MM-DD.jsonl).
An association graph tracks relationships between entries via a separate JSONL file.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_SALIENCE_ORDER: Dict[str, int] = {
    "noise": 0,
    "low": 1,
    "normal": 2,
    "high": 3,
    "critical": 4,
}


class SalienceLevel(str, Enum):
    NOISE = "noise"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

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

    @classmethod
    def from_value(cls, value: str | None) -> SalienceLevel:
        if value is None:
            return cls.NORMAL
        normalized = str(value).strip().lower()
        mapping = {
            "noise": cls.NOISE,
            "low": cls.LOW,
            "normal": cls.NORMAL,
            "high": cls.HIGH,
            "critical": cls.CRITICAL,
        }
        return mapping.get(normalized, cls.NORMAL)

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, SalienceLevel):
            return NotImplemented
        return _SALIENCE_ORDER[self.value] >= _SALIENCE_ORDER[other.value]

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, SalienceLevel):
            return NotImplemented
        return _SALIENCE_ORDER[self.value] > _SALIENCE_ORDER[other.value]

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SalienceLevel):
            return NotImplemented
        return _SALIENCE_ORDER[self.value] <= _SALIENCE_ORDER[other.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SalienceLevel):
            return NotImplemented
        return _SALIENCE_ORDER[self.value] < _SALIENCE_ORDER[other.value]


@dataclass
class JournalEntry:
    id: str
    timestamp: str
    content: str
    salience: SalienceLevel = SalienceLevel.NORMAL
    tags: List[str] = field(default_factory=list)
    source_session: str = ""
    associations: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
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
    def from_dict(cls, data: dict) -> JournalEntry:
        return cls(
            id=str(data.get("id", "")),
            timestamp=str(data.get("timestamp", "")),
            content=str(data.get("content", "")),
            salience=SalienceLevel.from_value(data.get("salience")),
            tags=list(data.get("tags", [])),
            source_session=str(data.get("source_session", "")),
            associations=list(data.get("associations", [])),
        )


class JournalStore:
    """Persists journal entries as JSONL files in a daily directory."""

    def __init__(self, daily_dir: str):
        self.daily_dir = Path(daily_dir)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def journal_path(self, day: Optional[date] = None) -> Path:
        target = day or date.today()
        return self.daily_dir / f"{target.isoformat()}.jsonl"

    def append(self, entry: JournalEntry) -> JournalEntry:
        if not entry.id:
            entry.id = str(uuid.uuid4())
        if not entry.timestamp:
            entry.timestamp = datetime.now(tz=timezone.utc).isoformat()

        path = self.journal_path()
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
        results: List[JournalEntry] = []
        for day in self.list_dates():
            if date_start and day < date_start:
                continue
            if date_end and day > date_end:
                continue
            results.extend(self._read_file(self.journal_path(day)))

        if salience_min is not None:
            results = [e for e in results if e.salience >= salience_min]

        if tags:
            tag_set = set(tags)
            results = [e for e in results if tag_set & set(e.tags)]

        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results[:limit]

    def get(self, entry_id: str) -> Optional[JournalEntry]:
        for day in self.list_dates():
            for entry in self._read_file(self.journal_path(day)):
                if entry.id == entry_id:
                    return entry
        return None

    def list_dates(self) -> List[date]:
        dates: List[date] = []
        for path in sorted(self.daily_dir.glob("*.jsonl")):
            try:
                dates.append(date.fromisoformat(path.stem))
            except ValueError:
                continue
        return dates

    @staticmethod
    def _read_file(path: Path) -> List[JournalEntry]:
        entries: List[JournalEntry] = []
        if not path.exists():
            return entries
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(JournalEntry.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError):
                    logger.warning("Skipping malformed journal line in %s", path)
        except OSError:
            logger.warning("Could not read journal file %s", path)
        return entries


@dataclass
class AssociationEdge:
    source_id: str
    target_id: str
    relation_type: str
    weight: float = 1.0

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AssociationEdge:
        return cls(
            source_id=str(data.get("source_id", "")),
            target_id=str(data.get("target_id", "")),
            relation_type=str(data.get("relation_type", "")),
            weight=float(data.get("weight", 1.0)),
        )


class AssociationGraph:
    """JSONL-backed association graph between journal entries."""

    def __init__(self, graph_path: str):
        self.path = Path(graph_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add_edge(self, edge: AssociationEdge) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(edge.as_dict(), ensure_ascii=False) + "\n")

    def get_associations(self, entry_id: str) -> List[AssociationEdge]:
        edges: List[AssociationEdge] = []
        if not self.path.exists():
            return edges
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    edge = AssociationEdge.from_dict(json.loads(line))
                    if edge.source_id == entry_id or edge.target_id == entry_id:
                        edges.append(edge)
                except (json.JSONDecodeError, KeyError):
                    continue
        except OSError:
            logger.warning("Could not read association graph at %s", self.path)
        return edges

    def add_edges_for_entry(
        self, entry: JournalEntry, existing_entries: List[JournalEntry]
    ) -> List[AssociationEdge]:
        new_edges: List[AssociationEdge] = []
        if not entry.tags:
            return new_edges

        entry_tags = set(entry.tags)
        for other in existing_entries:
            if other.id == entry.id:
                continue
            shared = entry_tags & set(other.tags)
            if not shared:
                continue
            edge = AssociationEdge(
                source_id=entry.id,
                target_id=other.id,
                relation_type="shared_tag",
                weight=1.0,
            )
            self.add_edge(edge)
            new_edges.append(edge)

        return new_edges
