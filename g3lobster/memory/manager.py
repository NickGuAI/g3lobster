"""Persistent markdown + JSONL memory management."""

from __future__ import annotations

import re
import threading
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from g3lobster.memory.compactor import CompactionEngine
from g3lobster.memory.journal import (
    AssociationGraph,
    JournalEntry,
    JournalStore,
    SalienceLevel,
)
from g3lobster.memory.procedures import (
    CandidateStore,
    Procedure,
    ProcedureStore,
    is_empty_procedure_document,
)
from g3lobster.memory.sessions import SessionStore


class MemoryManager:
    """Maintains MEMORY.md, daily notes, and JSONL transcripts."""

    def __init__(
        self,
        data_dir: str,
        compact_threshold: int = 40,
        compact_keep_ratio: float = 0.25,
        compact_chunk_size: int = 10,
        procedure_min_frequency: int = 3,
        memory_max_sections: int = 50,
        procedure_extract_interval: int = 10,
        gemini_command: str = "gemini",
        gemini_args: Optional[List[str]] = None,
        gemini_timeout_s: float = 45.0,
        gemini_cwd: Optional[str] = None,
        # Legacy parameter kept for backward compatibility; ignored.
        summarize_threshold: int = 20,
    ):
        self.data_dir = Path(data_dir)

        self.memory_dir = self.data_dir / ".memory"
        self.daily_dir = self.memory_dir / "daily"
        self.sessions_dir = self.data_dir / "sessions"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.procedures_file = self.memory_dir / "PROCEDURES.md"
        self.candidates_file = self.memory_dir / "CANDIDATES.json"

        self.compact_threshold = max(1, int(compact_threshold))
        self.procedure_min_frequency = max(1, int(procedure_min_frequency))
        self.memory_max_sections = max(5, int(memory_max_sections))
        self.procedure_extract_interval = max(2, int(procedure_extract_interval))
        self._memory_lock = threading.Lock()

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        if not self.memory_file.exists():
            self.memory_file.write_text("# MEMORY\n\n", encoding="utf-8")
        if not self.procedures_file.exists():
            self.procedures_file.write_text("# PROCEDURES\n\n", encoding="utf-8")

        self.sessions = SessionStore(str(self.sessions_dir))
        self.procedure_store = ProcedureStore(
            str(self.procedures_file),
            min_frequency=self.procedure_min_frequency,
        )
        self.candidate_store = CandidateStore(str(self.candidates_file))
        self.journal_store = JournalStore(str(self.daily_dir))
        self.association_graph = AssociationGraph(str(self.memory_dir))

        self.compactor = CompactionEngine(
            session_store=self.sessions,
            procedure_store=self.procedure_store,
            candidate_store=self.candidate_store,
            compact_threshold=self.compact_threshold,
            compact_keep_ratio=compact_keep_ratio,
            compact_chunk_size=compact_chunk_size,
            procedure_min_frequency=self.procedure_min_frequency,
            gemini_command=gemini_command,
            gemini_args=gemini_args,
            gemini_timeout_s=gemini_timeout_s,
            gemini_cwd=gemini_cwd,
        )

    def read_memory(self) -> str:
        return self.memory_file.read_text(encoding="utf-8")

    def write_memory(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def read_procedures(self) -> str:
        return self.procedures_file.read_text(encoding="utf-8")

    def write_procedures(self, content: str) -> None:
        procedures = self.procedure_store.parse_markdown(content)
        if not procedures and not is_empty_procedure_document(content):
            raise ValueError("Invalid procedures format. Provide markdown sections with Trigger and Steps.")
        self.procedure_store.save_procedures(procedures)

    def list_procedures(self) -> List[Procedure]:
        return self.procedure_store.list_procedures()

    def match_procedures(
        self,
        query: str,
        global_procedures: Optional[List[Procedure]] = None,
        limit: int = 3,
    ) -> List[Procedure]:
        # Permanent procedures from the markdown store.
        permanent = self.procedure_store.list_procedures()
        # Usable candidates (weight >= 3) from the candidate store.
        usable = self.candidate_store.list_usable()
        # Merge: permanent + usable + global, with agent-level overriding.
        all_local = ProcedureStore.merge_procedures(usable, permanent)
        merged = ProcedureStore.merge_procedures(global_procedures or [], all_local)
        return ProcedureStore.match_query(merged, query=query, limit=limit)

    def append_memory_section(self, section_title: str, content: str) -> None:
        with self._memory_lock:
            existing = self.read_memory().rstrip()
            updated = f"{existing}\n\n## {section_title}\n\n{content.strip()}\n"
            self.write_memory(self._trim_memory(updated))

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        text = str(tag or "").strip()
        text = re.sub(r"[\[\]\n\r]+", " ", text)
        return " ".join(text.split())

    def append_tagged_memory(self, tag: str, content: str) -> None:
        normalized_tag = self._normalize_tag(tag)
        if not normalized_tag:
            raise ValueError("Tag must be non-empty")
        if not str(content or "").strip():
            raise ValueError("Tagged memory content must be non-empty")
        self.append_memory_section(f"[{normalized_tag}]", content)

    def get_memories_by_tag(self, tag: str) -> List[str]:
        normalized_tag = self._normalize_tag(tag).lower()
        if not normalized_tag:
            return []

        content = self.read_memory()
        lines = content.splitlines()

        entries: List[str] = []
        current_tag: Optional[str] = None
        buffer: List[str] = []

        def _flush() -> None:
            if current_tag != normalized_tag:
                return
            text = "\n".join(buffer).strip()
            if text:
                entries.append(text)

        for line in lines:
            if line.startswith("## "):
                _flush()
                match = re.fullmatch(r"##\s+\[(.+)\]\s*", line.strip())
                current_tag = self._normalize_tag(match.group(1)).lower() if match else None
                buffer = []
                continue
            if current_tag is not None:
                buffer.append(line)

        _flush()
        return entries

    def _trim_memory(self, content: str) -> str:
        """Keep at most ``memory_max_sections`` ## sections.

        Preserves the header (everything before the first ``##``) and keeps
        the most recent sections.  Older sections are dropped since daily
        notes serve as the long-term archive.
        """
        lines = content.splitlines(keepends=True)

        # Split into header + list of sections.
        all_sections: list[str] = []
        buf: list[str] = []
        found_first = False
        for line in lines:
            if line.startswith("## "):
                if found_first and buf:
                    all_sections.append("".join(buf))
                    buf = []
                found_first = True
            if found_first:
                buf.append(line)
        if buf:
            all_sections.append("".join(buf))

        if len(all_sections) <= self.memory_max_sections:
            return content

        # Rebuild header (everything before first ##).
        header = ""
        for line in lines:
            if line.startswith("## "):
                break
            header += line

        kept = all_sections[-self.memory_max_sections :]
        return header.rstrip() + "\n\n" + "\n".join(s.rstrip() for s in kept) + "\n"

    def daily_note_path(self, day: Optional[date] = None) -> Path:
        target_day = day or date.today()
        return self.daily_dir / f"{target_day.isoformat()}.md"

    def append_daily_note(self, text: str, day: Optional[date] = None) -> None:
        path = self.daily_note_path(day)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text.strip() + "\n")

    def append_journal_entry(self, entry: JournalEntry) -> JournalEntry:
        """Write a structured journal entry and also append to the daily .md for backward compat."""
        saved = self.journal_store.append(entry)
        # Append human-readable line to existing markdown daily note.
        self.append_daily_note(
            f"[{entry.salience.value}] {entry.content[:200]}",
            day=self.journal_store._parse_entry_date(entry.timestamp),
        )
        # Build association graph edges.
        self.association_graph.add_edges_from_entry(entry)
        return saved

    def query_journal(
        self,
        *,
        salience_min: Optional[SalienceLevel] = None,
        tags: Optional[List[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 50,
    ) -> List[JournalEntry]:
        return self.journal_store.query(
            salience_min=salience_min,
            tags=tags,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self.sessions.session_lock(session_id):
            self.sessions.append_message(session_id, role, content, metadata=metadata)
            count = self.sessions.message_count(session_id)
            compacted = self._maybe_compact(session_id, message_count=count)
            if not compacted:
                # Extract procedure candidates periodically (every N turns).
                self.compactor.maybe_extract_candidates(
                    session_id,
                    message_count=count,
                    extract_interval=self.procedure_extract_interval,
                )

    def read_session(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.sessions.read_session(session_id, limit=limit)

    def read_session_messages(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.sessions.read_messages(session_id, limit=limit)

    def read_latest_compaction(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.sessions.read_latest_compaction(session_id)

    def list_sessions(self) -> List[str]:
        return self.sessions.list_sessions()

    # Pattern matching user preference statements: requires "I" or "you"
    # before the keyword, or the keyword at the start of the sentence.
    # This avoids false positives like "I never said that".
    _PREFERENCE_PATTERN = re.compile(
        r"(?:^|(?:i|you|we)\s+)"
        r"(?:always|never|prefer|must always|must never|must|should always|should never)"
        r"\s",
        re.IGNORECASE,
    )
    _REMEMBER_PATTERN = re.compile(
        r"(?:^|[\.\!]\s+)(?:remember\s+(?:to|that|this)|please\s+remember)",
        re.IGNORECASE,
    )
    _IMPORTANT_PATTERN = re.compile(
        r"(?:^|[\.\!]\s+)(?:(?:this|that|it)\s+is\s+important|important\s*:)",
        re.IGNORECASE,
    )

    @classmethod
    def _is_user_preference(cls, text: str) -> bool:
        """Return True if text looks like a user preference statement."""
        return bool(
            cls._PREFERENCE_PATTERN.search(text)
            or cls._REMEMBER_PATTERN.search(text)
            or cls._IMPORTANT_PATTERN.search(text)
        )

    @staticmethod
    def _classify_compaction_salience(role: str, content: str, is_preference: bool) -> SalienceLevel:
        """Classify a compacted message by salience level."""
        if is_preference:
            return SalienceLevel.HIGH
        if role == "user":
            return SalienceLevel.NORMAL
        # Tool outputs and assistant responses default to normal.
        return SalienceLevel.NORMAL

    def _maybe_compact(self, session_id: str, message_count: Optional[int] = None) -> bool:
        def _flush_compacted_messages(messages: List[Dict[str, object]]) -> None:
            highlights: List[str] = []
            for entry in messages:
                message = entry.get("message", {})
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role", "")).strip().lower()
                content = str(message.get("content", "")).strip()
                if not role or not content:
                    continue

                is_pref = role == "user" and self._is_user_preference(content)
                salience = self._classify_compaction_salience(role, content, is_pref)

                # Write structured journal entry for each compacted highlight.
                journal_entry = JournalEntry(
                    content=content[:300],
                    salience=salience,
                    tags=["compaction"],
                    source_session=session_id,
                )
                self.journal_store.append(journal_entry)

                if is_pref:
                    highlights.append(f"- user preference: {content[:180]}")
                elif len(highlights) < 6:
                    highlights.append(f"- {role}: {content[:180]}")

            if highlights:
                self.append_memory_section(f"Compaction {session_id}", "\n".join(highlights[:8]))

        return self.compactor.maybe_compact(
            session_id,
            after_compact=_flush_compacted_messages,
            message_count=message_count,
        )
