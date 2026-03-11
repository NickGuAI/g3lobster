"""Append-only JSONL decision log storage."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


class DecisionLog:
    """Append-only JSONL storage for agent decisions.

    Storage path: data/agents/{agent_id}/.memory/decisions.jsonl
    Entry schema: {"timestamp", "session_id", "decision", "reasoning", "context", "tags"}
    """

    def __init__(self, decisions_path: str):
        self.path = Path(decisions_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _iter_entries(path: Path) -> Iterator[Dict[str, Any]]:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload

    def append(
        self,
        session_id: str,
        decision: str,
        context: str = "",
        reasoning: str = "",
        tags: Optional[List[str]] = None,
    ) -> None:
        """Append a decision entry to the log."""
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": session_id,
            "decision": decision,
            "reasoning": reasoning,
            "context": context,
            "tags": tags or [],
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def query(self, query_text: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Keyword search across decision entries.

        Splits the query into words and scores each entry by how many
        query words appear (case-insensitive) in the decision, reasoning,
        context, or tags fields.  Returns the top `limit` entries sorted
        by relevance (highest score first), with ties broken by recency.
        """
        if not query_text or not query_text.strip():
            return self.list(limit=limit)

        words = [w.lower() for w in query_text.strip().split() if w]
        if not words:
            return self.list(limit=limit)

        scored: List[tuple] = []
        for idx, entry in enumerate(self._iter_entries(self.path)):
            searchable = " ".join([
                str(entry.get("decision", "")),
                str(entry.get("reasoning", "")),
                str(entry.get("context", "")),
                " ".join(str(t) for t in entry.get("tags", [])),
            ]).lower()
            score = sum(1 for w in words if w in searchable)
            if score > 0:
                scored.append((score, idx, entry))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item[2] for item in scored[:limit]]

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent decisions."""
        entries = list(self._iter_entries(self.path))
        return entries[-limit:] if limit < len(entries) else entries
