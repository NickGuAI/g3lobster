"""Full-text memory search across agent and global memory sources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set


from g3lobster.memory.journal import JournalEntry, SalienceLevel

SUPPORTED_MEMORY_TYPES = {"memory", "procedures", "daily", "session", "knowledge", "journal"}


@dataclass
class MemorySearchHit:
    agent_id: str
    memory_type: str
    source: str
    snippet: str
    line_number: int
    timestamp: Optional[str] = None
    salience_weight: float = 1.0

    def as_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "memory_type": self.memory_type,
            "source": self.source,
            "snippet": self.snippet,
            "line_number": self.line_number,
            "timestamp": self.timestamp,
        }


class MemorySearchEngine:
    """Searches markdown and session transcripts under the runtime data dir."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.agents_dir = self.data_dir / "agents"
        self.global_memory_dir = self.data_dir / ".memory"

    @staticmethod
    def _parse_date(value: Optional[object]) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _date_in_range(target: Optional[date], start: Optional[date], end: Optional[date]) -> bool:
        if target is None:
            return start is None and end is None
        if start and target < start:
            return False
        if end and target > end:
            return False
        return True

    @staticmethod
    def _matches(text: str, query: str, query_terms: Sequence[str]) -> bool:
        body = str(text or "").lower()
        if query in body:
            return True
        return bool(query_terms) and all(term in body for term in query_terms)

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")

    def _relative_source(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.data_dir))
        except ValueError:
            return str(path)

    @staticmethod
    def _context_snippet(lines: List[str], line_index: int, window: int = 1) -> str:
        start = max(0, line_index - window)
        end = min(len(lines), line_index + window + 1)
        return "\n".join(lines[start:end]).strip()

    def _iter_agent_ids(self, requested: Optional[Iterable[str]]) -> List[str]:
        if requested:
            return [str(agent_id).strip() for agent_id in requested if str(agent_id).strip()]
        if not self.agents_dir.exists():
            return []
        return sorted(path.name for path in self.agents_dir.iterdir() if path.is_dir())

    @staticmethod
    def _sort_key(hit: MemorySearchHit) -> float:
        base = 0.0
        if hit.timestamp:
            text = hit.timestamp.replace("Z", "+00:00")
            try:
                base = datetime.fromisoformat(text).timestamp()
            except ValueError:
                pass
        return base * hit.salience_weight

    def _search_text_file(
        self,
        *,
        path: Path,
        agent_id: str,
        memory_type: str,
        query: str,
        query_terms: Sequence[str],
        file_date: Optional[date],
    ) -> List[MemorySearchHit]:
        if not path.exists() or not path.is_file():
            return []

        content = self._read_text(path)
        lines = content.splitlines()
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        hits: List[MemorySearchHit] = []
        for index, line in enumerate(lines):
            if not self._matches(line, query, query_terms):
                continue
            snippet = self._context_snippet(lines, index)
            hits.append(
                MemorySearchHit(
                    agent_id=agent_id,
                    memory_type=memory_type,
                    source=self._relative_source(path),
                    snippet=snippet,
                    line_number=index + 1,
                    timestamp=timestamp if file_date is None else datetime.combine(
                        file_date,
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    ).isoformat(),
                )
            )
        return hits

    def _search_sessions(
        self,
        *,
        sessions_dir: Path,
        agent_id: str,
        query: str,
        query_terms: Sequence[str],
        start: Optional[date],
        end: Optional[date],
    ) -> List[MemorySearchHit]:
        if not sessions_dir.exists():
            return []

        hits: List[MemorySearchHit] = []
        for path in sorted(sessions_dir.glob("*.jsonl")):
            line_number = 0
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

            for raw in lines:
                line_number += 1
                if not raw.strip():
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                message = payload.get("message")
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "")
                content = str(message.get("content") or "")
                if not self._matches(content, query, query_terms):
                    continue

                timestamp = payload.get("timestamp")
                entry_date = None
                if isinstance(timestamp, str) and timestamp:
                    try:
                        entry_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
                    except ValueError:
                        entry_date = None
                if not self._date_in_range(entry_date, start, end):
                    continue

                snippet = f"{role}: {content}".strip()
                hits.append(
                    MemorySearchHit(
                        agent_id=agent_id,
                        memory_type="session",
                        source=self._relative_source(path),
                        snippet=snippet,
                        line_number=line_number,
                        timestamp=timestamp if isinstance(timestamp, str) else None,
                    )
                )
        return hits

    def _search_journal_files(
        self,
        *,
        daily_dir: Path,
        agent_id: str,
        query: str,
        query_terms: Sequence[str],
        start: Optional[date],
        end: Optional[date],
    ) -> List[MemorySearchHit]:
        """Search JSONL journal files with per-entry salience weighting."""
        hits: List[MemorySearchHit] = []
        for path in sorted(daily_dir.glob("*.jsonl")):
            file_date = self._parse_date(path.stem)
            if not self._date_in_range(file_date, start, end):
                continue

            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

            for line_number, raw in enumerate(lines, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                content = str(data.get("content", ""))
                tags_str = " ".join(data.get("tags") or [])
                searchable = f"{content} {tags_str}"

                if not self._matches(searchable, query, query_terms):
                    continue

                salience = SalienceLevel.from_value(data.get("salience"))
                timestamp = data.get("timestamp")

                hits.append(
                    MemorySearchHit(
                        agent_id=agent_id,
                        memory_type="journal",
                        source=self._relative_source(path),
                        snippet=content[:300],
                        line_number=line_number,
                        timestamp=timestamp if isinstance(timestamp, str) else None,
                        salience_weight=salience.weight,
                    )
                )
        return hits

    def search(
        self,
        query: str,
        *,
        agent_ids: Optional[Iterable[str]] = None,
        memory_types: Optional[Iterable[str]] = None,
        start_date: Optional[object] = None,
        end_date: Optional[object] = None,
        limit: int = 20,
    ) -> List[MemorySearchHit]:
        normalized_query = str(query or "").strip().lower()
        if not normalized_query:
            return []

        query_terms = [token for token in normalized_query.split() if token]
        selected_types_raw = {str(item).strip().lower() for item in (memory_types or SUPPORTED_MEMORY_TYPES)}
        selected_types: Set[str] = selected_types_raw & SUPPORTED_MEMORY_TYPES
        if not selected_types:
            selected_types = set(SUPPORTED_MEMORY_TYPES)

        start = self._parse_date(start_date)
        end = self._parse_date(end_date)
        capped_limit = max(1, int(limit))

        hits: List[MemorySearchHit] = []
        for agent_id in self._iter_agent_ids(agent_ids):
            runtime_dir = self.agents_dir / agent_id
            memory_dir = runtime_dir / ".memory"
            daily_dir = memory_dir / "daily"
            sessions_dir = runtime_dir / "sessions"

            if "memory" in selected_types:
                memory_path = memory_dir / "MEMORY.md"
                memory_date = date.fromtimestamp(memory_path.stat().st_mtime) if memory_path.exists() else None
                if self._date_in_range(memory_date, start, end):
                    hits.extend(
                        self._search_text_file(
                            path=memory_path,
                            agent_id=agent_id,
                            memory_type="memory",
                            query=normalized_query,
                            query_terms=query_terms,
                            file_date=memory_date,
                        )
                    )

            if "procedures" in selected_types:
                procedures_path = memory_dir / "PROCEDURES.md"
                procedures_date = (
                    date.fromtimestamp(procedures_path.stat().st_mtime) if procedures_path.exists() else None
                )
                if self._date_in_range(procedures_date, start, end):
                    hits.extend(
                        self._search_text_file(
                            path=procedures_path,
                            agent_id=agent_id,
                            memory_type="procedures",
                            query=normalized_query,
                            query_terms=query_terms,
                            file_date=procedures_date,
                        )
                    )

            if "daily" in selected_types and daily_dir.exists():
                for daily_path in sorted(daily_dir.glob("*.md")):
                    file_date = self._parse_date(daily_path.stem)
                    if not self._date_in_range(file_date, start, end):
                        continue
                    hits.extend(
                        self._search_text_file(
                            path=daily_path,
                            agent_id=agent_id,
                            memory_type="daily",
                            query=normalized_query,
                            query_terms=query_terms,
                            file_date=file_date,
                        )
                    )

            if ("journal" in selected_types or "daily" in selected_types) and daily_dir.exists():
                hits.extend(
                    self._search_journal_files(
                        daily_dir=daily_dir,
                        agent_id=agent_id,
                        query=normalized_query,
                        query_terms=query_terms,
                        start=start,
                        end=end,
                    )
                )

            if "session" in selected_types:
                hits.extend(
                    self._search_sessions(
                        sessions_dir=sessions_dir,
                        agent_id=agent_id,
                        query=normalized_query,
                        query_terms=query_terms,
                        start=start,
                        end=end,
                    )
                )

        if "knowledge" in selected_types and self.global_memory_dir.exists():
            knowledge_dir = self.global_memory_dir / "knowledge"
            if knowledge_dir.exists():
                for path in sorted(knowledge_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    file_date = date.fromtimestamp(path.stat().st_mtime)
                    if not self._date_in_range(file_date, start, end):
                        continue
                    hits.extend(
                        self._search_text_file(
                            path=path,
                            agent_id="_global",
                            memory_type="knowledge",
                            query=normalized_query,
                            query_terms=query_terms,
                            file_date=file_date,
                        )
                    )

        hits.sort(key=self._sort_key, reverse=True)
        return hits[:capped_limit]
