"""Salience-classified journal with association graph.

Persists structured journal entries as JSONL files alongside existing daily
markdown notes, and maintains an association graph linking related entries.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class SalienceLevel(str, Enum):
    """Importance classification for journal entries."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    NOISE = "noise"

    @classmethod
    def from_value(cls, value: Optional[str]) -> "SalienceLevel":
        if value is None:
            return cls.NORMAL
        normalized = str(value).strip().lower()
        for item in cls:
            if item.value == normalized:
                return item
        return cls.NORMAL

    @property
    def weight(self) -> float:
        return _SALIENCE_WEIGHTS[self]


_SALIENCE_WEIGHTS: Dict[SalienceLevel, float] = {
    SalienceLevel.CRITICAL: 5.0,
    SalienceLevel.HIGH: 3.0,
    SalienceLevel.NORMAL: 1.0,
    SalienceLevel.LOW: 0.5,
    SalienceLevel.NOISE: 0.1,
}


@dataclass
class JournalEntry:
    """A single structured journal entry."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    content: str = ""
    salience: SalienceLevel = SalienceLevel.NORMAL
    tags: List[str] = field(default_factory=list)
    source_session: str = ""
    associations: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["salience"] = self.salience.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JournalEntry":
        return cls(
            id=str(data.get("id", str(uuid.uuid4()))),
            timestamp=str(data.get("timestamp", "")),
            content=str(data.get("content", "")),
            salience=SalienceLevel.from_value(data.get("salience")),
            tags=list(data.get("tags") or []),
            source_session=str(data.get("source_session", "")),
            associations=list(data.get("associations") or []),
        )


@dataclass
class AssociationEdge:
    """An edge in the association graph."""

    source_id: str
    target_id: str
    relation_type: str = "related"
    weight: float = 1.0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssociationEdge":
        return cls(
            source_id=str(data.get("source_id", "")),
            target_id=str(data.get("target_id", "")),
            relation_type=str(data.get("relation_type", "related")),
            weight=float(data.get("weight", 1.0)),
        )


class JournalStore:
    """Persists journal entries as JSONL files under daily/.

    Each day gets a ``YYYY-MM-DD.jsonl`` file alongside the existing ``.md``
    daily note.  Entries are append-only within a file.
    """

    def __init__(self, daily_dir: str):
        self.daily_dir = Path(daily_dir)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def _jsonl_path(self, day: Optional[date] = None) -> Path:
        target = day or date.today()
        return self.daily_dir / f"{target.isoformat()}.jsonl"

    def append(self, entry: JournalEntry) -> JournalEntry:
        """Append a journal entry to the day's JSONL file."""
        if not entry.timestamp:
            entry.timestamp = datetime.now(tz=timezone.utc).isoformat()
        entry_date = self._parse_entry_date(entry.timestamp)
        path = self._jsonl_path(entry_date)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")
        return entry

    def read_day(self, day: Optional[date] = None) -> List[JournalEntry]:
        """Read all entries for a given day."""
        path = self._jsonl_path(day)
        return self._read_jsonl(path)

    def get_entry(self, entry_id: str, day: Optional[date] = None) -> Optional[JournalEntry]:
        """Find an entry by id.  If day is None, searches all days."""
        if day:
            for entry in self.read_day(day):
                if entry.id == entry_id:
                    return entry
            return None
        for path in sorted(self.daily_dir.glob("*.jsonl")):
            for entry in self._read_jsonl(path):
                if entry.id == entry_id:
                    return entry
        return None

    def query(
        self,
        *,
        salience_min: Optional[SalienceLevel] = None,
        tags: Optional[List[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 50,
    ) -> List[JournalEntry]:
        """Query entries with optional filters."""
        salience_order = list(SalienceLevel)
        min_index = salience_order.index(salience_min) if salience_min else len(salience_order) - 1

        tag_set = {t.lower() for t in tags} if tags else None
        results: List[JournalEntry] = []

        for path in sorted(self.daily_dir.glob("*.jsonl"), reverse=True):
            file_date = self._parse_stem_date(path.stem)
            if file_date:
                if start_date and file_date < start_date:
                    continue
                if end_date and file_date > end_date:
                    continue

            for entry in self._read_jsonl(path):
                if salience_order.index(entry.salience) > min_index:
                    continue
                if tag_set and not tag_set.intersection(t.lower() for t in entry.tags):
                    continue
                results.append(entry)
                if len(results) >= limit:
                    return results

        return results

    def list_days(self) -> List[date]:
        """Return sorted list of dates that have journal entries."""
        days: List[date] = []
        for path in sorted(self.daily_dir.glob("*.jsonl")):
            d = self._parse_stem_date(path.stem)
            if d:
                days.append(d)
        return days

    @staticmethod
    def _read_jsonl(path: Path) -> List[JournalEntry]:
        if not path.exists():
            return []
        entries: List[JournalEntry] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entries.append(JournalEntry.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return entries

    @staticmethod
    def _parse_entry_date(timestamp: str) -> date:
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return date.today()

    @staticmethod
    def _parse_stem_date(stem: str) -> Optional[date]:
        try:
            return date.fromisoformat(stem)
        except ValueError:
            return None


class AssociationGraph:
    """Filesystem-backed association graph stored as a single JSONL file."""

    def __init__(self, memory_dir: str):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.memory_dir / "associations.jsonl"

    def add_edge(self, edge: AssociationEdge) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(edge.as_dict(), ensure_ascii=False) + "\n")

    def add_edges_from_entry(self, entry: JournalEntry) -> None:
        """Create edges between the entry and all entries in its associations list."""
        for target_id in entry.associations:
            self.add_edge(AssociationEdge(
                source_id=entry.id,
                target_id=target_id,
                relation_type="explicit",
            ))

    def add_tag_associations(self, entry: JournalEntry, other_entries: List[JournalEntry]) -> None:
        """Create edges between entries sharing tags."""
        entry_tags = {t.lower() for t in entry.tags}
        if not entry_tags:
            return
        for other in other_entries:
            if other.id == entry.id:
                continue
            other_tags = {t.lower() for t in other.tags}
            shared = entry_tags & other_tags
            if shared:
                self.add_edge(AssociationEdge(
                    source_id=entry.id,
                    target_id=other.id,
                    relation_type="shared_tags",
                    weight=len(shared),
                ))

    def get_associations(self, entry_id: str) -> List[AssociationEdge]:
        """Return all edges where entry_id is source or target."""
        edges = self._read_all()
        return [e for e in edges if e.source_id == entry_id or e.target_id == entry_id]

    def get_neighbors(self, entry_id: str) -> List[str]:
        """Return ids of all entries connected to the given entry."""
        neighbors: List[str] = []
        for edge in self.get_associations(entry_id):
            if edge.source_id == entry_id:
                neighbors.append(edge.target_id)
            else:
                neighbors.append(edge.source_id)
        return neighbors

    def _read_all(self) -> List[AssociationEdge]:
        if not self._path.exists():
            return []
        edges: List[AssociationEdge] = []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                edges.append(AssociationEdge.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return edges
