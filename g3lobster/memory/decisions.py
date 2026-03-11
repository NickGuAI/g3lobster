"""Append-only JSONL decision log for agent decision rationale."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# Patterns that indicate a decision was made in conversation text.
_DECISION_PATTERNS = [
    re.compile(r"\b(?:I decided|I\'ve decided|we decided)\b", re.IGNORECASE),
    re.compile(r"\b(?:let\'s go with|going with|went with)\b", re.IGNORECASE),
    re.compile(r"\b(?:the approach is|the plan is|the strategy is)\b", re.IGNORECASE),
    re.compile(r"\b(?:we chose|I chose|choosing)\b", re.IGNORECASE),
    re.compile(r"\bbecause\b.*\b(?:better|best|prefer|should|will)\b", re.IGNORECASE),
    re.compile(r"\b(?:decision|rationale):\s", re.IGNORECASE),
]


def looks_like_decision(text: str) -> bool:
    """Return True if text contains decision-indicating language."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _DECISION_PATTERNS)


class DecisionLog:
    """Append-only JSONL storage for decision entries.

    Storage path: ``<base_dir>/decisions.jsonl``

    Each line is a JSON object with keys:
        timestamp, session_id, decision, reasoning, context, tags
    """

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.base_dir / "decisions.jsonl"
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        session_id: str,
        decision: str,
        context: str = "",
        reasoning: str = "",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Append a decision entry and return the stored record."""
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": session_id,
            "decision": decision,
            "reasoning": reasoning,
            "context": context,
            "tags": tags or [],
        }
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def _iter_entries(self) -> List[Dict[str, Any]]:
        """Read all entries from the JSONL file."""
        entries: List[Dict[str, Any]] = []
        if not self._path.exists():
            return entries
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
        return entries

    def query(self, query_text: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Keyword search across decision entries.

        Searches the decision, reasoning, context, and tags fields.
        Returns most recent matching entries first.
        """
        if not query_text or not query_text.strip():
            return self.list(limit=limit)

        keywords = query_text.lower().split()
        entries = self._iter_entries()
        matches: List[Dict[str, Any]] = []

        for entry in reversed(entries):
            searchable = " ".join([
                str(entry.get("decision", "")),
                str(entry.get("reasoning", "")),
                str(entry.get("context", "")),
                " ".join(entry.get("tags", [])),
            ]).lower()
            if all(kw in searchable for kw in keywords):
                matches.append(entry)
                if len(matches) >= limit:
                    break

        return matches

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent decisions."""
        entries = self._iter_entries()
        return list(reversed(entries[-limit:]))
