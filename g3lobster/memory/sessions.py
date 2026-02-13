"""JSONL session transcript storage."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class SessionStore:
    """Append-only JSONL storage for session messages."""

    def __init__(self, sessions_dir: str):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize_session_id(session_id: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id) or "default"

    def _session_path(self, session_id: str) -> Path:
        safe_id = self._sanitize_session_id(session_id)
        return self.sessions_dir / f"{safe_id}.jsonl"

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "type": "message",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "message": {"role": role, "content": content},
        }
        if metadata:
            payload["metadata"] = metadata

        path = self._session_path(session_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read_session(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        path = self._session_path(session_id)
        if not path.exists():
            return []

        lines: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        if limit is not None:
            return lines[-limit:]
        return lines

    def message_count(self, session_id: str) -> int:
        return len(self.read_session(session_id))

    def list_sessions(self) -> List[str]:
        return sorted(path.stem for path in self.sessions_dir.glob("*.jsonl"))
