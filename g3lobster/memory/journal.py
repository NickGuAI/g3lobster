"""Salience-classified journal with association graph.

Provides structured journal entries with salience levels, tags, and an
explicit association graph linking related entries across time and sessions.
All data is filesystem-based using JSONL files.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


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
        for item in cls:
            if item.value == normalized:
                return item
        return cls.NORMAL


# Salience weights for search ranking.
SALIENCE_WEIGHTS: Dict[SalienceLevel, float] = {
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
    salience: SalienceLevel = SalienceLevel.NORMAL
    tags: List[str] = field(default_factory=list)
    source_session: str = ""
    associations: List[str] = field(default_factory=list)

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
            id=str(data.get("id", uuid.uuid4())),
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
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssociationEdge":
        return cls(
            source_id=str(data.get("source_id", "")),
            target_id=str(data.get("target_id", "")),
            relation_type=str(data.get("relation_type", "related")),
            weight=float(data.get("weight", 1.0)),
        )


class JournalStore:
    """Persists journal entries as JSONL files alongside existing daily .md files."""

    def __init__(self, daily_dir: str | Path):
        self.daily_dir = Path(daily_dir)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _journal_path(self, day: Optional[date] = None) -> Path:
        target_day = day or date.today()
        return self.daily_dir / f"{target_day.isoformat()}.jsonl"

    def append(self, entry: JournalEntry, day: Optional[date] = None) -> JournalEntry:
        """Append a journal entry to the daily JSONL file."""
        path = self._journal_path(day)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")
        return entry

    def read_day(self, day: Optional[date] = None) -> List[JournalEntry]:
        """Read all entries for a given day."""
        path = self._journal_path(day)
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
            except (json.JSONDecodeError, TypeError):
                continue
        return entries

    def get_entry(self, entry_id: str, day: Optional[date] = None) -> Optional[JournalEntry]:
        """Find a specific entry by ID. Searches given day or all days."""
        if day:
            for entry in self.read_day(day):
                if entry.id == entry_id:
                    return entry
            return None
        # Search all days
        for path in sorted(self.daily_dir.glob("*.jsonl")):
            stem = path.stem
            try:
                file_date = date.fromisoformat(stem)
            except ValueError:
                continue
            for entry in self.read_day(file_date):
                if entry.id == entry_id:
                    return entry
        return None

    def query(
        self,
        *,
        salience_min: Optional[SalienceLevel] = None,
        tags: Optional[Sequence[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 50,
    ) -> List[JournalEntry]:
        """Query journal entries with filters."""
        salience_order = [
            SalienceLevel.CRITICAL,
            SalienceLevel.HIGH,
            SalienceLevel.NORMAL,
            SalienceLevel.LOW,
            SalienceLevel.NOISE,
        ]
        min_index = salience_order.index(salience_min) if salience_min else len(salience_order) - 1
        allowed_salience = set(salience_order[: min_index + 1])

        tag_set = set(tags) if tags else None

        results: List[JournalEntry] = []
        for path in sorted(self.daily_dir.glob("*.jsonl"), reverse=True):
            stem = path.stem
            try:
                file_date = date.fromisoformat(stem)
            except ValueError:
                continue
            if start_date and file_date < start_date:
                continue
            if end_date and file_date > end_date:
                continue

            for entry in self.read_day(file_date):
                if entry.salience not in allowed_salience:
                    continue
                if tag_set and not tag_set.intersection(entry.tags):
                    continue
                results.append(entry)
                if len(results) >= limit:
                    return results

        return results

    def list_days(self) -> List[date]:
        """Return sorted list of days with journal entries."""
        days: List[date] = []
        for path in sorted(self.daily_dir.glob("*.jsonl")):
            try:
                days.append(date.fromisoformat(path.stem))
            except ValueError:
                continue
        return days


class AssociationGraph:
    """Graph of related journal entries backed by a single JSONL file."""

    def __init__(self, memory_dir: str | Path):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.memory_dir / "associations.jsonl"
        self._lock = threading.Lock()

    def add_edge(self, edge: AssociationEdge) -> None:
        """Append an association edge."""
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(edge.as_dict(), ensure_ascii=False) + "\n")

    def add_edges_for_shared_tags(self, entries: Sequence[JournalEntry]) -> List[AssociationEdge]:
        """Auto-link entries that share tags."""
        tag_index: Dict[str, List[str]] = {}
        for entry in entries:
            for tag in entry.tags:
                tag_index.setdefault(tag, []).append(entry.id)

        seen: set = set()
        new_edges: List[AssociationEdge] = []
        for tag, ids in tag_index.items():
            for i, source_id in enumerate(ids):
                for target_id in ids[i + 1 :]:
                    pair = (min(source_id, target_id), max(source_id, target_id))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    edge = AssociationEdge(
                        source_id=source_id,
                        target_id=target_id,
                        relation_type=f"shared_tag:{tag}",
                    )
                    self.add_edge(edge)
                    new_edges.append(edge)
        return new_edges

    def _read_all_edges(self) -> List[AssociationEdge]:
        """Read all edges from the JSONL file."""
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
            except (json.JSONDecodeError, TypeError):
                continue
        return edges

    def get_associations(self, entry_id: str) -> List[AssociationEdge]:
        """Get all edges connected to a given entry."""
        return [
            edge
            for edge in self._read_all_edges()
            if edge.source_id == entry_id or edge.target_id == entry_id
        ]

    def get_related_ids(self, entry_id: str, depth: int = 1) -> List[str]:
        """Traverse the graph to find related entry IDs up to given depth."""
        all_edges = self._read_all_edges()
        visited: set = {entry_id}
        frontier: set = {entry_id}

        for _ in range(depth):
            next_frontier: set = set()
            for edge in all_edges:
                if edge.source_id in frontier and edge.target_id not in visited:
                    next_frontier.add(edge.target_id)
                    visited.add(edge.target_id)
                elif edge.target_id in frontier and edge.source_id not in visited:
                    next_frontier.add(edge.source_id)
                    visited.add(edge.source_id)
            if not next_frontier:
                break
            frontier = next_frontier

        visited.discard(entry_id)
        return sorted(visited)
