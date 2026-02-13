"""Persistent markdown + JSONL memory management."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from g3lobster.memory.sessions import SessionStore


class MemoryManager:
    """Maintains MEMORY.md, daily notes, and JSONL transcripts."""

    def __init__(self, data_dir: str, summarize_threshold: int = 20):
        self.data_dir = Path(data_dir)
        self.memory_dir = self.data_dir / "memory"
        self.daily_dir = self.memory_dir / "memory"
        self.sessions_dir = self.data_dir / "sessions"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.summarize_threshold = summarize_threshold

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        if not self.memory_file.exists():
            self.memory_file.write_text("# MEMORY\n\n", encoding="utf-8")

        self.sessions = SessionStore(str(self.sessions_dir))

    def read_memory(self) -> str:
        return self.memory_file.read_text(encoding="utf-8")

    def write_memory(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_memory_section(self, section_title: str, content: str) -> None:
        existing = self.read_memory().rstrip()
        updated = f"{existing}\n\n## {section_title}\n\n{content.strip()}\n"
        self.write_memory(updated)

    def daily_note_path(self, day: Optional[date] = None) -> Path:
        target_day = day or date.today()
        return self.daily_dir / f"{target_day.isoformat()}.md"

    def append_daily_note(self, text: str, day: Optional[date] = None) -> None:
        path = self.daily_note_path(day)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text.strip() + "\n")

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.sessions.append_message(session_id, role, content, metadata=metadata)
        self._maybe_summarize(session_id)

    def read_session(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.sessions.read_session(session_id, limit=limit)

    def list_sessions(self) -> List[str]:
        return self.sessions.list_sessions()

    def _maybe_summarize(self, session_id: str) -> None:
        count = self.sessions.message_count(session_id)
        if count == 0 or count % self.summarize_threshold != 0:
            return

        recent = self.sessions.read_session(session_id, limit=self.summarize_threshold)
        snippets = []
        for item in recent:
            msg = item.get("message", {})
            role = msg.get("role", "unknown")
            content = str(msg.get("content", "")).strip()
            if content:
                snippets.append(f"- {role}: {content[:180]}")

        if not snippets:
            return

        summary = "\n".join(snippets)
        self.append_memory_section(f"Session {session_id}", summary)
