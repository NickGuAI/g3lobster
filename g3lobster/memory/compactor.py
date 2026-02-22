"""Session auto-compaction engine."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import re
import subprocess
import threading
from typing import Callable, Dict, List, Optional

from g3lobster.memory.procedures import CandidateStore, ProcedureStore
from g3lobster.memory.sessions import SessionStore

logger = logging.getLogger(__name__)


class CompactionEngine:
    """Compacts long JSONL sessions by summarizing old messages."""

    def __init__(
        self,
        session_store: SessionStore,
        procedure_store: ProcedureStore,
        candidate_store: Optional[CandidateStore] = None,
        compact_threshold: int = 40,
        compact_keep_ratio: float = 0.25,
        compact_chunk_size: int = 10,
        procedure_min_frequency: int = 3,
        chunk_summarizer: Optional[Callable[[List[Dict[str, object]]], str]] = None,
        gemini_command: str = "gemini",
        gemini_args: Optional[List[str]] = None,
        gemini_timeout_s: float = 45.0,
        gemini_cwd: Optional[str] = None,
    ):
        self.session_store = session_store
        self.procedure_store = procedure_store
        self.candidate_store = candidate_store
        self.compact_threshold = max(1, int(compact_threshold))
        self.compact_keep_ratio = min(0.9, max(0.05, float(compact_keep_ratio)))
        self.compact_chunk_size = max(1, int(compact_chunk_size))
        self.procedure_min_frequency = max(1, int(procedure_min_frequency))
        self.chunk_summarizer = chunk_summarizer or self._summarize_chunk_with_gemini
        self.gemini_command = (gemini_command or "gemini").strip() or "gemini"
        self.gemini_args = list(gemini_args) if gemini_args is not None else ["-y"]
        self.gemini_timeout_s = max(5.0, float(gemini_timeout_s))
        self.gemini_cwd = gemini_cwd
        self._procedure_lock = threading.Lock()

    def maybe_compact(
        self,
        session_id: str,
        after_compact: Optional[Callable[[List[Dict[str, object]]], None]] = None,
        message_count: Optional[int] = None,
    ) -> bool:
        current_count = self.session_store.message_count(session_id) if message_count is None else int(message_count)
        if current_count < self.compact_threshold:
            return False

        messages = self.session_store.read_messages(session_id)
        message_count = len(messages)
        if message_count < self.compact_threshold:
            return False

        keep_count = max(1, int(math.ceil(message_count * self.compact_keep_ratio)))
        compact_count = message_count - keep_count
        if compact_count <= 0:
            return False

        compacted = messages[:compact_count]
        kept = messages[compact_count:]

        summary = self._summarize_messages(compacted)
        record: Dict[str, object] = {
            "type": "compaction",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "summary": summary,
            "compacted_messages": len(compacted),
            "kept_messages": len(kept),
            "kept_from": f"m{compact_count + 1}",
        }

        self.session_store.rewrite_session(session_id, [record, *kept])

        # Flush highlights after the rewrite succeeds so MEMORY.md never
        # references a compaction that didn't actually persist.
        # Wrapped in try/except: the rewrite already succeeded, so a
        # callback failure must not mask the successful compaction.
        if after_compact:
            try:
                after_compact(compacted)
            except Exception as exc:
                logger.warning("after_compact callback failed (compaction persisted): %s", exc)

        # Extract procedure candidates and ingest into candidate store.
        candidates = ProcedureStore.extract_candidates(compacted)
        if candidates and self.candidate_store:
            promoted = self.candidate_store.ingest(candidates)
            # Promote newly permanent procedures to the markdown store.
            if promoted:
                self._upsert_procedures_locked(promoted)

        return True

    def _upsert_procedures_locked(self, procedures: List) -> None:
        """Write to PROCEDURES.md under a lock to prevent concurrent clobber."""
        with self._procedure_lock:
            self.procedure_store.upsert_procedures(procedures)

    def maybe_extract_candidates(
        self,
        session_id: str,
        message_count: int,
        extract_interval: int = 10,
    ) -> None:
        """Extract procedure candidates periodically (every N turns).

        Called from MemoryManager.append_message on every message.
        Only runs extraction when message_count is a multiple of extract_interval.
        """
        if message_count == 0 or message_count % extract_interval != 0:
            return
        if not self.candidate_store:
            return

        # Read recent messages (the last extract_interval messages).
        recent = self.session_store.read_messages(session_id, limit=extract_interval)
        if not recent:
            return

        candidates = ProcedureStore.extract_candidates(recent)
        if not candidates:
            return

        promoted = self.candidate_store.ingest(candidates)
        if promoted:
            self._upsert_procedures_locked(promoted)

    def _summarize_messages(self, messages: List[Dict[str, object]]) -> str:
        chunks: List[List[Dict[str, object]]] = []
        for index in range(0, len(messages), self.compact_chunk_size):
            chunks.append(messages[index : index + self.compact_chunk_size])

        chunk_summaries: List[str] = []
        for chunk in chunks:
            if not chunk:
                continue
            try:
                summary = self._normalize_summary(self.chunk_summarizer(chunk))
                if not summary:
                    summary = self._fallback_chunk_summary(chunk)
            except Exception as exc:
                logger.warning("Compaction chunk summarization failed, using fallback summary: %s", exc)
                summary = self._fallback_chunk_summary(chunk)
            chunk_summaries.append(summary)

        lines: List[str] = []
        for idx, summary in enumerate(chunk_summaries, start=1):
            lines.append(f"Chunk {idx}:")
            lines.extend(summary.splitlines())
        return "\n".join(lines).strip() or "(no compaction summary)"

    def _summarize_chunk_with_gemini(self, messages: List[Dict[str, object]]) -> str:
        prompt = self._build_chunk_prompt(messages)
        command = [self.gemini_command, *self.gemini_args, "-p", prompt]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.gemini_timeout_s,
            cwd=self.gemini_cwd,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(f"Gemini CLI exited with code {result.returncode}: {stderr}")
        return (result.stdout or "").strip()

    @staticmethod
    def _build_chunk_prompt(messages: List[Dict[str, object]]) -> str:
        lines = [
            "Summarize this transcript chunk for long-term memory compaction.",
            "Return 2-3 short bullet points only.",
            "Prioritize decisions, facts, preferences, and completed actions.",
            "Do not include markdown headers or code fences.",
            "",
            "Transcript:",
        ]

        for entry in messages:
            message = entry.get("message", {})
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip().lower() or "unknown"
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            compact = re.sub(r"\s+", " ", content)
            if len(compact) > 320:
                compact = compact[:317].rstrip() + "..."
            lines.append(f"{role}: {compact}")

        return "\n".join(lines).strip()

    @staticmethod
    def _normalize_summary(summary: str) -> str:
        cleaned = str(summary or "").replace("\r", "").strip()
        if not cleaned:
            return ""

        # Gemini can sometimes wrap answers in fences; keep only plain bullets.
        cleaned = cleaned.replace("```markdown", "").replace("```text", "").replace("```", "")
        bullets: List[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("-", "*")):
                line = line[1:].strip()
            else:
                numbered = re.match(r"^\d+[\.\)]\s*(.+)$", line)
                if numbered:
                    line = numbered.group(1).strip()
            if not line:
                continue
            bullets.append(f"- {line}")
            if len(bullets) >= 3:
                break

        if not bullets:
            one_line = re.sub(r"\s+", " ", cleaned).strip()
            if one_line:
                bullets.append(f"- {one_line[:220]}")
        return "\n".join(bullets[:3])

    @staticmethod
    def _fallback_chunk_summary(messages: List[Dict[str, object]]) -> str:
        user_count = 0
        assistant_count = 0
        for entry in messages:
            message = entry.get("message", {})
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip().lower()
            if role == "user":
                user_count += 1
            elif role == "assistant":
                assistant_count += 1

        return "\n".join(
            [
                f"- Summarized {len(messages)} messages ({user_count} user, {assistant_count} assistant).",
                "- Gemini summarizer unavailable; compacted using metadata-only fallback.",
            ]
        )
