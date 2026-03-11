"""Salience-classified journal with association graph.

Provides structured journal entries with salience levels, tags, and an
association graph that links related entries across time and sessions.
Data is stored as JSONL files alongside existing daily markdown notes.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


class SalienceLevel(str, Enum):
    """Importance classification for journal entries."""

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
        for member in cls:
            if member.value == normalized:
                return member
        return cls.NORMAL

    @property
    def weight(self) -> float:
        """Search ranking multiplier for this salience level."""
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
    timestamp: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    content: str = ""
    salience: str = SalienceLevel.NORMAL.value
    tags: List[str] = field(default_factory=list)
    source_session: str = ""
    associations: List[str] = field(default_factory=list)

    @property
    def salience_level(self) -> SalienceLevel:
        return SalienceLevel.from_value(self.salience)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JournalEntry":
        return cls(
            id=str(data.get("id", str(uuid.uuid4()))),
            timestamp=str(data.get("timestamp", "")),
            content=str(data.get("content", "")),
            salience=str(data.get("salience", SalienceLevel.NORMAL.value)),
            tags=list(data.get("tags") or []),
            source_session=str(data.get("source_session", "")),
            associations=list(data.get("associations") or []),
        )


@dataclass
class AssociationEdge:
    """A directed edge in the association graph."""

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
    """Persists journal entries as JSONL files under daily/<date>.jsonl.

    Existing daily/*.md files are preserved for backward compatibility.
    """

    def __init__(self, daily_dir: str | Path):
        self.daily_dir = Path(daily_dir)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _jsonl_path(self, day: Optional[date] = None) -> Path:
        target_day = day or date.today()
        return self.daily_dir / f"{target_day.isoformat()}.jsonl"

    def append(self, entry: JournalEntry, day: Optional[date] = None) -> JournalEntry:
        """Append a journal entry to the JSONL file for the given day."""
        path = self._jsonl_path(day)
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")
        return entry

    def read_day(self, day: Optional[date] = None) -> List[JournalEntry]:
        """Read all journal entries for a given day."""
        path = self._jsonl_path(day)
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
            except json.JSONDecodeError:
                continue
        return entries

    def get_entry(self, entry_id: str, day: Optional[date] = None) -> Optional[JournalEntry]:
        """Find a specific entry by ID. If day is None, searches all days."""
        if day:
            for entry in self.read_day(day):
                if entry.id == entry_id:
                    return entry
            return None
        # Search all JSONL files
        for path in sorted(self.daily_dir.glob("*.jsonl")):
            for entry in self._read_jsonl(path):
                if entry.id == entry_id:
                    return entry
        return None

    def query(
        self,
        *,
        salience_min: Optional[str] = None,
        tags: Optional[List[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 50,
    ) -> List[JournalEntry]:
        """Query journal entries with optional filters."""
        salience_order = [e.value for e in SalienceLevel]
        if salience_min:
            level = SalienceLevel.from_value(salience_min)
            min_index = salience_order.index(level.value)
        else:
            min_index = len(salience_order) - 1  # include all levels

        tag_set = set(tags) if tags else None
        results: List[JournalEntry] = []

        for path in sorted(self.daily_dir.glob("*.jsonl")):
            file_date = self._parse_date_from_stem(path.stem)
            if start_date and file_date and file_date < start_date:
                continue
            if end_date and file_date and file_date > end_date:
                continue

            for entry in self._read_jsonl(path):
                entry_index = salience_order.index(
                    SalienceLevel.from_value(entry.salience).value
                )
                if entry_index > min_index:
                    continue
                if tag_set and not tag_set.intersection(entry.tags):
                    continue
                results.append(entry)

        # Sort by timestamp descending (most recent first)
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results[:limit]

    def list_days(self) -> List[date]:
        """List all days that have journal entries."""
        days: List[date] = []
        for path in sorted(self.daily_dir.glob("*.jsonl")):
            d = self._parse_date_from_stem(path.stem)
            if d:
                days.append(d)
        return days

    @staticmethod
    def _parse_date_from_stem(stem: str) -> Optional[date]:
        try:
            return date.fromisoformat(stem)
        except ValueError:
            return None

    def _read_jsonl(self, path: Path) -> List[JournalEntry]:
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
            except json.JSONDecodeError:
                continue
        return entries


class AssociationGraph:
    """Graph of associations between journal entries.

    Backed by a single JSONL file under .memory/associations.jsonl.
    """

    def __init__(self, memory_dir: str | Path):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.memory_dir / "associations.jsonl"
        self._lock = threading.Lock()

    def add_edge(self, edge: AssociationEdge) -> AssociationEdge:
        """Add an association edge."""
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(edge.as_dict(), ensure_ascii=False) + "\n")
        return edge

    def add_edges_from_entry(self, entry: JournalEntry, journal_store: JournalStore) -> List[AssociationEdge]:
        """Auto-link an entry to existing entries that share tags."""
        if not entry.tags:
            return []

        created: List[AssociationEdge] = []
        entry_tags = set(entry.tags)

        for day in journal_store.list_days():
            for other in journal_store.read_day(day):
                if other.id == entry.id:
                    continue
                shared = entry_tags.intersection(other.tags)
                if shared:
                    edge = AssociationEdge(
                        source_id=entry.id,
                        target_id=other.id,
                        relation_type="shared_tags",
                        weight=len(shared),
                    )
                    self.add_edge(edge)
                    created.append(edge)
        return created

    def get_associations(self, entry_id: str) -> List[AssociationEdge]:
        """Get all edges where entry_id is source or target."""
        edges = self._read_all()
        return [
            e for e in edges if e.source_id == entry_id or e.target_id == entry_id
        ]

    def get_connected_ids(self, entry_id: str, max_depth: int = 1) -> List[str]:
        """BFS traversal from entry_id up to max_depth hops."""
        all_edges = self._read_all()
        adjacency: Dict[str, List[str]] = {}
        for edge in all_edges:
            adjacency.setdefault(edge.source_id, []).append(edge.target_id)
            adjacency.setdefault(edge.target_id, []).append(edge.source_id)

        visited: set[str] = {entry_id}
        frontier = [entry_id]
        for _ in range(max_depth):
            next_frontier: List[str] = []
            for node in frontier:
                for neighbor in adjacency.get(node, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
            if not frontier:
                break

        visited.discard(entry_id)
        return sorted(visited)

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
            except json.JSONDecodeError:
                continue
        return edges
