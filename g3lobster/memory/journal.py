"""Salience-classified journal with association graph.

Provides structured journal entries with importance scoring and
inter-entry linking via an association graph. Persists as JSONL files
alongside existing daily markdown notes.
"""

from __future__ import annotations

import json
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

    @property
    def weight(self) -> float:
        """Search ranking weight multiplier."""
        return {
            SalienceLevel.CRITICAL: 5.0,
            SalienceLevel.HIGH: 3.0,
            SalienceLevel.NORMAL: 1.0,
            SalienceLevel.LOW: 0.5,
            SalienceLevel.NOISE: 0.1,
        }[self]


@dataclass
class JournalEntry:
    """A single structured journal entry with salience metadata."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    content: str = ""
    salience: SalienceLevel = SalienceLevel.NORMAL
    tags: List[str] = field(default_factory=list)
    source_session: str = ""
    associations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["salience"] = self.salience.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JournalEntry":
        salience = SalienceLevel.from_value(data.get("salience"))
        return cls(
            id=str(data.get("id", str(uuid.uuid4()))),
            timestamp=str(data.get("timestamp", "")),
            content=str(data.get("content", "")),
            salience=salience,
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

    def to_dict(self) -> Dict[str, Any]:
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
    """Persists journal entries as JSONL files under the daily directory.

    Each day gets a ``YYYY-MM-DD.jsonl`` file alongside the existing
    ``YYYY-MM-DD.md`` markdown daily note.
    """

    def __init__(self, daily_dir: str | Path) -> None:
        self.daily_dir = Path(daily_dir)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def _journal_path(self, day: Optional[date] = None) -> Path:
        target_day = day or date.today()
        return self.daily_dir / f"{target_day.isoformat()}.jsonl"

    def append(self, entry: JournalEntry, day: Optional[date] = None) -> None:
        """Append a journal entry to the day's JSONL file."""
        path = self._journal_path(day)
        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read_day(self, day: Optional[date] = None) -> List[JournalEntry]:
        """Read all journal entries for a given day."""
        path = self._journal_path(day)
        if not path.exists():
            return []
        return self._read_jsonl(path)

    def get_entry(self, entry_id: str) -> Optional[JournalEntry]:
        """Find an entry by ID across all daily JSONL files."""
        for path in sorted(self.daily_dir.glob("*.jsonl")):
            for entry in self._read_jsonl(path):
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
        """Query journal entries with optional filters."""
        salience_order = list(SalienceLevel)
        min_index = salience_order.index(salience_min) if salience_min else len(salience_order) - 1
        allowed_saliences = set(salience_order[: min_index + 1])

        tag_set = {t.lower() for t in tags} if tags else None

        results: List[JournalEntry] = []
        for path in sorted(self.daily_dir.glob("*.jsonl"), reverse=True):
            file_date = self._parse_date_from_stem(path.stem)
            if file_date is not None:
                if start_date and file_date < start_date:
                    continue
                if end_date and file_date > end_date:
                    continue

            for entry in self._read_jsonl(path):
                if entry.salience not in allowed_saliences:
                    continue
                if tag_set and not (tag_set & {t.lower() for t in entry.tags}):
                    continue
                results.append(entry)
                if len(results) >= limit:
                    return results

        return results

    @staticmethod
    def _parse_date_from_stem(stem: str) -> Optional[date]:
        try:
            return date.fromisoformat(stem)
        except ValueError:
            return None

    @staticmethod
    def _read_jsonl(path: Path) -> List[JournalEntry]:
        entries: List[JournalEntry] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                entries.append(JournalEntry.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return entries


class AssociationGraph:
    """Graph of associations between journal entries.

    Backed by a single ``associations.jsonl`` file under the memory dir.
    Edges are stored as ``{source_id, target_id, relation_type, weight}``.
    """

    def __init__(self, memory_dir: str | Path) -> None:
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.memory_dir / "associations.jsonl"

    def add_edge(self, edge: AssociationEdge) -> None:
        """Append an association edge."""
        line = json.dumps(edge.to_dict(), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def add_edges_from_entry(self, entry: JournalEntry, journal_store: JournalStore) -> None:
        """Auto-link entry to others sharing tags or explicit associations."""
        if not entry.tags and not entry.associations:
            return

        # Link explicit associations.
        for target_id in entry.associations:
            self.add_edge(
                AssociationEdge(
                    source_id=entry.id,
                    target_id=target_id,
                    relation_type="explicit",
                    weight=2.0,
                )
            )

        # Link by shared tags: scan recent entries.
        if entry.tags:
            entry_tags = {t.lower() for t in entry.tags}
            recent = journal_store.query(limit=100)
            for other in recent:
                if other.id == entry.id:
                    continue
                other_tags = {t.lower() for t in other.tags}
                shared = entry_tags & other_tags
                if shared:
                    self.add_edge(
                        AssociationEdge(
                            source_id=entry.id,
                            target_id=other.id,
                            relation_type="shared_tags",
                            weight=float(len(shared)),
                        )
                    )

    def get_associations(self, entry_id: str) -> List[AssociationEdge]:
        """Return all edges where entry_id is source or target."""
        edges = self._read_all()
        return [e for e in edges if e.source_id == entry_id or e.target_id == entry_id]

    def _read_all(self) -> List[AssociationEdge]:
        if not self._path.exists():
            return []
        edges: List[AssociationEdge] = []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()

        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                edges.append(AssociationEdge.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return edges
