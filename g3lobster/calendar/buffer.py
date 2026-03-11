"""Buffered message storage for focus-time interception."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List


@dataclass
class BufferedMessage:
    """A chat message captured during focus time."""
    sender_name: str
    text: str
    thread_id: str
    timestamp: str


class MessageBuffer:
    """Append-only buffer for messages received during focus time.

    Persists to ``{data_dir}/{agent_id}/focus_buffer.json`` using the same
    atomic-write pattern as ``cron/store.py``.
    """

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)

    def _buffer_path(self, agent_id: str) -> Path:
        return self._data_dir / agent_id / "focus_buffer.json"

    def add(self, agent_id: str, message: BufferedMessage) -> None:
        """Append a message to the agent's focus buffer."""
        messages = self._read(agent_id)
        messages.append(message)
        self._write(agent_id, messages)

    def drain(self, agent_id: str) -> List[BufferedMessage]:
        """Return all buffered messages and clear the buffer file."""
        messages = self._read(agent_id)
        if messages:
            self._write(agent_id, [])
        return messages

    def has_messages(self, agent_id: str) -> bool:
        return bool(self._read(agent_id))

    def _read(self, agent_id: str) -> List[BufferedMessage]:
        path = self._buffer_path(agent_id)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        result: List[BufferedMessage] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                result.append(BufferedMessage(
                    sender_name=item.get("sender_name", ""),
                    text=item.get("text", ""),
                    thread_id=item.get("thread_id", ""),
                    timestamp=item.get("timestamp", ""),
                ))
            except (TypeError, KeyError):
                continue
        return result

    def _write(self, agent_id: str, messages: List[BufferedMessage]) -> None:
        path = self._buffer_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([asdict(m) for m in messages], indent=2, ensure_ascii=False)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                tmp.write(payload + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
