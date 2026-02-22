"""JSONL session transcript storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


class SessionStore:
    """Append-only JSONL storage for session messages."""

    def __init__(self, sessions_dir: str):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._message_counts: Dict[str, int] = {}
        self._session_locks: Dict[str, threading.RLock] = {}
        self._session_locks_lock = threading.Lock()

    @staticmethod
    def _sanitize_session_id(session_id: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id) or "default"
        if sanitized in {".", ".."}:
            return "default"
        return sanitized

    def _session_key(self, session_id: str) -> str:
        return self._sanitize_session_id(session_id)

    def _session_path(self, session_id: str) -> Path:
        safe_id = self._session_key(session_id)
        return self.sessions_dir / f"{safe_id}.jsonl"

    def _lock_for_session(self, session_id: str) -> threading.RLock:
        key = self._session_key(session_id)
        with self._session_locks_lock:
            lock = self._session_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._session_locks[key] = lock
        return lock

    @contextmanager
    def session_lock(self, session_id: str) -> Iterator[None]:
        lock = self._lock_for_session(session_id)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

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

    @staticmethod
    def _count_messages_in_path(path: Path) -> int:
        count = 0
        for entry in SessionStore._iter_entries(path):
            if entry.get("type") == "message":
                count += 1
        return count

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
        existed_before = path.exists()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        key = path.stem
        cached_count = self._message_counts.get(key)
        if cached_count is not None:
            self._message_counts[key] = cached_count + 1
        elif existed_before:
            self._message_counts[key] = self._count_messages_in_path(path)
        else:
            self._message_counts[key] = 1

    def read_session(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        path = self._session_path(session_id)
        lines = list(self._iter_entries(path))

        if limit is not None:
            return lines[-limit:]
        return lines

    def read_messages(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Read only message entries from a session."""
        entries = [entry for entry in self.read_session(session_id) if entry.get("type") == "message"]
        if limit is None:
            self._message_counts[self._session_key(session_id)] = len(entries)
        if limit is not None:
            return entries[-limit:]
        return entries

    def read_latest_compaction(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Read the latest compaction record if present."""
        for entry in reversed(self.read_session(session_id)):
            if entry.get("type") == "compaction":
                return entry
        return None

    def rewrite_session(self, session_id: str, entries: List[Dict[str, Any]]) -> None:
        """Atomically rewrite a full JSONL session file."""
        path = self._session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp_handle.name)
        message_count = 0
        try:
            with tmp_handle as handle:
                for entry in entries:
                    if entry.get("type") == "message":
                        message_count += 1
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
        self._message_counts[path.stem] = message_count

    def message_count(self, session_id: str) -> int:
        key = self._session_key(session_id)
        cached = self._message_counts.get(key)
        if cached is not None:
            return cached

        count = self._count_messages_in_path(self._session_path(session_id))
        self._message_counts[key] = count
        return count

    def list_sessions(self) -> List[str]:
        return sorted(path.stem for path in self.sessions_dir.glob("*.jsonl"))
