"""Salience-classified journal with association graph.

Journal entries are structured records stored as JSONL files alongside
the existing daily markdown notes.  Each entry carries a salience level
that drives search-result ranking and a set of tags used to build an
explicit association graph between related entries.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Salience classification
# ---------------------------------------------------------------------------

class SalienceLevel(str, Enum):
    """Importance tier for journal entries."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    NOISE = "noise"

    # Search-result weight multipliers.
    @property
    def weight(self) -> float:
        return _SALIENCE_WEIGHTS[self]

    @classmethod
    def from_value(cls, value: str | None) -> "SalienceLevel":
        if value is None:
            return cls.NORMAL
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.NORMAL


_SALIENCE_WEIGHTS: Dict[SalienceLevel, float] = {
    SalienceLevel.CRITICAL: 5.0,
    SalienceLevel.HIGH: 3.0,
    SalienceLevel.NORMAL: 1.0,
    SalienceLevel.LOW: 0.5,
    SalienceLevel.NOISE: 0.1,
}


# ---------------------------------------------------------------------------
# JournalEntry
# ---------------------------------------------------------------------------

@dataclass
class JournalEntry:
    """A single structured journal entry."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat(),
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
            id=str(data.get("id") or uuid.uuid4()),
            timestamp=str(data.get("timestamp", "")),
            content=str(data.get("content", "")),
            salience=SalienceLevel.from_value(data.get("salience")),
            tags=list(data.get("tags") or []),
            source_session=str(data.get("source_session", "")),
            associations=list(data.get("associations") or []),
        )


# ---------------------------------------------------------------------------
# JournalStore — JSONL-backed per-day journal
# ---------------------------------------------------------------------------

class JournalStore:
    """Persists journal entries as ``daily/YYYY-MM-DD.jsonl`` files.

    Lives alongside the existing ``.md`` daily notes inside the agent's
    ``.memory/daily/`` directory.
    """

    def __init__(self, daily_dir: str | Path):
        self.daily_dir = Path(daily_dir)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def _path_for_day(self, day: Optional[date] = None) -> Path:
        target = day or date.today()
        return self.daily_dir / f"{target.isoformat()}.jsonl"

    # -- write ---------------------------------------------------------------

    def append(self, entry: JournalEntry, day: Optional[date] = None) -> JournalEntry:
        """Append a journal entry to the day's JSONL file."""
        path = self._path_for_day(day)
        line = json.dumps(entry.as_dict(), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return entry

    # -- read ----------------------------------------------------------------

    def read_day(self, day: Optional[date] = None) -> List[JournalEntry]:
        """Read all journal entries for a given day."""
        path = self._path_for_day(day)
        if not path.exists():
            return []
        return self._read_jsonl(path)

    def get_entry(self, entry_id: str) -> Optional[JournalEntry]:
        """Find a single entry by id across all daily files."""
        for path in sorted(self.daily_dir.glob("*.jsonl"), reverse=True):
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
        salience_order = list(SalienceLevel)  # ordered by declaration
        min_index = salience_order.index(salience_min) if salience_min else len(salience_order) - 1
        allowed_salience = set(salience_order[: min_index + 1])

        tag_set = {t.lower() for t in tags} if tags else None

        results: List[JournalEntry] = []
        for path in sorted(self.daily_dir.glob("*.jsonl"), reverse=True):
            file_date = self._parse_date(path.stem)
            if file_date is not None:
                if start_date and file_date < start_date:
                    continue
                if end_date and file_date > end_date:
                    continue

            for entry in self._read_jsonl(path):
                if entry.salience not in allowed_salience:
                    continue
                if tag_set and not tag_set.intersection(t.lower() for t in entry.tags):
                    continue
                results.append(entry)
                if len(results) >= limit:
                    return results

        return results

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _read_jsonl(path: Path) -> List[JournalEntry]:
        entries: List[JournalEntry] = []
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return entries
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entries.append(JournalEntry.from_dict(data))
            except (json.JSONDecodeError, Exception):
                continue
        return entries

    @staticmethod
    def _parse_date(stem: str) -> Optional[date]:
        try:
            return date.fromisoformat(stem)
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# AssociationGraph — JSONL-backed edge store
# ---------------------------------------------------------------------------

@dataclass
class AssociationEdge:
    """A directed edge between two journal entries."""

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


class AssociationGraph:
    """Persists edges as ``associations.jsonl`` under ``.memory/``."""

    def __init__(self, memory_dir: str | Path):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.memory_dir / "associations.jsonl"

    def add_edge(self, edge: AssociationEdge) -> None:
        line = json.dumps(edge.as_dict(), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def add_edges_for_entry(self, entry: JournalEntry, store: JournalStore) -> List[AssociationEdge]:
        """Auto-link *entry* to existing entries sharing tags and explicit refs."""
        new_edges: List[AssociationEdge] = []

        # Link by shared tags.
        if entry.tags:
            entry_tags = {t.lower() for t in entry.tags}
            for path in sorted(store.daily_dir.glob("*.jsonl"), reverse=True):
                for other in store._read_jsonl(path):
                    if other.id == entry.id:
                        continue
                    other_tags = {t.lower() for t in other.tags}
                    shared = entry_tags & other_tags
                    if not shared:
                        continue
                    edge = AssociationEdge(
                        source_id=entry.id,
                        target_id=other.id,
                        relation_type="shared_tags",
                        weight=len(shared),
                    )
                    self.add_edge(edge)
                    new_edges.append(edge)

        # Add explicit association references.
        for assoc_id in entry.associations:
            edge = AssociationEdge(
                source_id=entry.id,
                target_id=assoc_id,
                relation_type="explicit",
                weight=2.0,
            )
            self.add_edge(edge)
            new_edges.append(edge)

        return new_edges

    def get_associations(self, entry_id: str) -> List[AssociationEdge]:
        """Return all edges where *entry_id* is source or target."""
        edges = self._read_all()
        return [e for e in edges if e.source_id == entry_id or e.target_id == entry_id]

    def _read_all(self) -> List[AssociationEdge]:
        if not self.path.exists():
            return []
        edges: List[AssociationEdge] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return edges
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                edges.append(AssociationEdge.from_dict(json.loads(line)))
            except (json.JSONDecodeError, Exception):
                continue
        return edges
