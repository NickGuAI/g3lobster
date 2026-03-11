"""Nightly consolidation pipeline for global memory optimization.

Runs as a scheduled cron task to:
1. Extract durable facts from daily notes into MEMORY.md
2. Deduplicate near-identical memory sections
3. Compress old daily notes into weekly summaries
4. Promote candidate procedures that have sufficient weight
5. Evict stale facts from MEMORY.md (30+ days unreferenced)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from g3lobster.memory.procedures import PERMANENT_THRESHOLD, CandidateStore, ProcedureStore

if TYPE_CHECKING:
    from g3lobster.agents.registry import AgentRegistry
    from g3lobster.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return TOKEN_PATTERN.findall(text.lower())


@dataclass
class ConsolidationReport:
    """Results from a single agent's consolidation run."""

    agent_id: str
    facts_extracted: int = 0
    sections_deduped: int = 0
    entries_compressed: int = 0
    skills_promoted: int = 0
    facts_evicted: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = []
        if self.facts_extracted:
            parts.append(f"{self.facts_extracted} facts extracted")
        if self.sections_deduped:
            parts.append(f"{self.sections_deduped} sections deduped")
        if self.entries_compressed:
            parts.append(f"{self.entries_compressed} entries compressed")
        if self.skills_promoted:
            parts.append(f"{self.skills_promoted} skills promoted")
        if self.facts_evicted:
            parts.append(f"{self.facts_evicted} facts evicted")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ", ".join(parts) if parts else "no changes"


class ConsolidationPipeline:
    """Nightly memory optimization pipeline for g3lobster agents."""

    def __init__(
        self,
        gemini_command: str = "gemini",
        gemini_args: Optional[List[str]] = None,
        gemini_timeout_s: float = 45.0,
        gemini_cwd: Optional[str] = None,
        days_window: int = 7,
        stale_days: int = 30,
        dedup_threshold: float = 0.8,
    ):
        self.gemini_command = (gemini_command or "gemini").strip() or "gemini"
        self.gemini_args = list(gemini_args) if gemini_args is not None else ["-y"]
        self.gemini_timeout_s = max(5.0, float(gemini_timeout_s))
        self.gemini_cwd = gemini_cwd
        self.days_window = max(1, int(days_window))
        self.stale_days = max(1, int(stale_days))
        self.dedup_threshold = min(1.0, max(0.1, float(dedup_threshold)))

    def extract_facts(self, memory_manager: "MemoryManager") -> int:
        """Read recent daily notes, summarize into durable facts, append to MEMORY.md."""
        today = date.today()
        notes_content: List[str] = []

        for days_ago in range(self.days_window):
            day = today - timedelta(days=days_ago)
            note_path = memory_manager.daily_note_path(day)
            if note_path.exists():
                text = note_path.read_text(encoding="utf-8").strip()
                if text:
                    notes_content.append(f"## {day.isoformat()}\n{text}")

        if not notes_content:
            return 0

        combined = "\n\n".join(notes_content)
        prompt = (
            "Extract durable facts from these daily notes for long-term memory.\n"
            "Return only bullet points of key facts, decisions, and preferences.\n"
            "Omit transient details. Maximum 8 bullets.\n\n"
            f"{combined[:3000]}"
        )

        try:
            summary = self._call_gemini(prompt)
        except Exception as exc:
            logger.warning("Fact extraction failed, using fallback: %s", exc)
            summary = f"- Consolidated {len(notes_content)} daily notes from past {self.days_window} days."

        if summary.strip():
            memory_manager.append_memory_section(
                f"Consolidation {today.isoformat()}", summary.strip()
            )
            return summary.strip().count("\n") + 1

        return 0

    def dedup_memory(self, memory_manager: "MemoryManager") -> int:
        """Parse MEMORY.md sections, merge near-duplicates."""
        content = memory_manager.read_memory()
        lines = content.splitlines(keepends=True)

        # Parse into header + sections
        header_lines: List[str] = []
        sections: List[tuple] = []  # (title_line, body_lines)
        current_title = ""
        current_body: List[str] = []
        found_first = False

        for line in lines:
            if line.startswith("## "):
                if found_first and current_title:
                    sections.append((current_title, current_body))
                    current_body = []
                current_title = line
                found_first = True
                continue
            if found_first:
                current_body.append(line)
            else:
                header_lines.append(line)

        if current_title:
            sections.append((current_title, current_body))

        if len(sections) < 2:
            return 0

        # Find and merge near-duplicate sections
        merged_indices: set = set()
        dedup_count = 0

        for i in range(len(sections)):
            if i in merged_indices:
                continue
            tokens_i = set(_tokenize("".join(sections[i][1])))
            if not tokens_i:
                continue

            for j in range(i + 1, len(sections)):
                if j in merged_indices:
                    continue
                tokens_j = set(_tokenize("".join(sections[j][1])))
                if not tokens_j:
                    continue

                overlap = len(tokens_i & tokens_j) / len(tokens_i | tokens_j)
                if overlap >= self.dedup_threshold:
                    merged_indices.add(j)
                    dedup_count += 1

        if not merged_indices:
            return 0

        # Rebuild content without merged sections
        kept_sections = [s for i, s in enumerate(sections) if i not in merged_indices]
        new_content = "".join(header_lines).rstrip() + "\n\n"
        for title_line, body_lines in kept_sections:
            new_content += title_line + "".join(body_lines)
        new_content = new_content.rstrip() + "\n"

        memory_manager.write_memory(new_content)
        return dedup_count

    def compress_entries(self, memory_manager: "MemoryManager") -> int:
        """Summarize daily notes older than days_window into weekly summaries."""
        today = date.today()
        cutoff = today - timedelta(days=self.days_window)
        daily_dir = memory_manager.daily_dir

        if not daily_dir.exists():
            return 0

        # Group old daily notes by ISO week
        weekly_groups: dict = {}  # week_key -> [(day, path, content)]
        for note_file in sorted(daily_dir.glob("*.md")):
            try:
                note_date = date.fromisoformat(note_file.stem)
            except ValueError:
                continue

            if note_date >= cutoff:
                continue

            content = note_file.read_text(encoding="utf-8").strip()
            if not content:
                continue

            year, week, _ = note_date.isocalendar()
            week_key = f"{year}-W{week:02d}"
            weekly_groups.setdefault(week_key, []).append((note_date, note_file, content))

        compressed_count = 0
        for week_key, entries in weekly_groups.items():
            if len(entries) < 2:
                continue

            combined = "\n\n".join(f"## {day.isoformat()}\n{text}" for day, _, text in entries)
            prompt = (
                "Summarize this week's daily notes into a concise weekly summary.\n"
                "Keep key facts, decisions, and outcomes. 5-8 bullet points max.\n\n"
                f"{combined[:3000]}"
            )

            try:
                summary = self._call_gemini(prompt)
            except Exception as exc:
                logger.warning("Weekly compression failed for %s: %s", week_key, exc)
                summary = f"- Week {week_key}: {len(entries)} daily notes consolidated."

            # Write weekly summary
            summary_path = daily_dir / f"{week_key}-summary.md"
            _atomic_write(summary_path, f"# Weekly Summary: {week_key}\n\n{summary.strip()}\n")

            # Archive originals
            archive_dir = daily_dir / "archive"
            archive_dir.mkdir(exist_ok=True)
            for _, note_path, _ in entries:
                dest = archive_dir / note_path.name
                note_path.rename(dest)

            compressed_count += len(entries)

        return compressed_count

    def distill_skills(self, memory_manager: "MemoryManager") -> int:
        """Batch-review candidates, promote those with sufficient weight."""
        candidate_store = memory_manager.candidate_store
        procedure_store = memory_manager.procedure_store

        candidates = candidate_store.list_all()
        if not candidates:
            return 0

        promoted_count = 0
        to_promote = []
        for candidate in candidates:
            if candidate.status == "permanent":
                continue
            if candidate.effective_weight >= PERMANENT_THRESHOLD:
                candidate.status = "permanent"
                to_promote.append(candidate)
                promoted_count += 1

        if to_promote:
            procedure_store.upsert_procedures(to_promote)

        return promoted_count

    def evict_stale(self, memory_manager: "MemoryManager") -> int:
        """Remove MEMORY.md sections that reference dates older than stale_days."""
        content = memory_manager.read_memory()
        lines = content.splitlines(keepends=True)
        today = date.today()
        cutoff = today - timedelta(days=self.stale_days)

        # Parse sections
        header_lines: List[str] = []
        sections: List[tuple] = []  # (title_line, body_lines)
        current_title = ""
        current_body: List[str] = []
        found_first = False

        for line in lines:
            if line.startswith("## "):
                if found_first and current_title:
                    sections.append((current_title, current_body))
                    current_body = []
                current_title = line
                found_first = True
                continue
            if found_first:
                current_body.append(line)
            else:
                header_lines.append(line)

        if current_title:
            sections.append((current_title, current_body))

        if not sections:
            return 0

        # Identify stale sections by date in the title
        date_pattern = re.compile(r"\d{4}-\d{2}-\d{2}")
        evicted = 0
        kept_sections: List[tuple] = []

        for title_line, body_lines in sections:
            match = date_pattern.search(title_line)
            if match:
                try:
                    section_date = date.fromisoformat(match.group())
                    if section_date < cutoff:
                        evicted += 1
                        continue
                except ValueError:
                    pass
            kept_sections.append((title_line, body_lines))

        if evicted == 0:
            return 0

        new_content = "".join(header_lines).rstrip() + "\n\n"
        for title_line, body_lines in kept_sections:
            new_content += title_line + "".join(body_lines)
        new_content = new_content.rstrip() + "\n"

        memory_manager.write_memory(new_content)
        return evicted

    def run(self, agent_id: str, memory_manager: "MemoryManager") -> ConsolidationReport:
        """Execute all consolidation stages for a single agent."""
        report = ConsolidationReport(agent_id=agent_id)

        stages = [
            ("extract_facts", lambda: self.extract_facts(memory_manager)),
            ("dedup_memory", lambda: self.dedup_memory(memory_manager)),
            ("compress_entries", lambda: self.compress_entries(memory_manager)),
            ("distill_skills", lambda: self.distill_skills(memory_manager)),
            ("evict_stale", lambda: self.evict_stale(memory_manager)),
        ]

        field_map = {
            "extract_facts": "facts_extracted",
            "dedup_memory": "sections_deduped",
            "compress_entries": "entries_compressed",
            "distill_skills": "skills_promoted",
            "evict_stale": "facts_evicted",
        }

        for stage_name, stage_fn in stages:
            try:
                result = stage_fn()
                setattr(report, field_map[stage_name], result)
            except Exception as exc:
                error_msg = f"{stage_name}: {exc}"
                report.errors.append(error_msg)
                logger.exception("Consolidation stage %s failed for agent %s", stage_name, agent_id)

        logger.info("Consolidation complete for agent %s: %s", agent_id, report.summary)
        return report

    def run_all(self, registry: "AgentRegistry") -> List[ConsolidationReport]:
        """Iterate all agents and consolidate each."""
        reports: List[ConsolidationReport] = []

        for agent_runtime in registry.active_agents():
            agent_id = agent_runtime.id
            memory_manager = agent_runtime.memory_manager
            try:
                report = self.run(agent_id, memory_manager)
                reports.append(report)
            except Exception as exc:
                logger.exception("Consolidation failed entirely for agent %s", agent_id)
                reports.append(ConsolidationReport(
                    agent_id=agent_id,
                    errors=[f"pipeline failed: {exc}"],
                ))

        return reports

    def _call_gemini(self, prompt: str) -> str:
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


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f"{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
