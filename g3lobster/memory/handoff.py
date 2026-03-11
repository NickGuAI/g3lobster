"""Structured handoff builder for cross-agent delegation.

Enriches raw task prompts with parent agent context (memory excerpts,
matching procedures, user preferences) before passing to child agents.
"""

from __future__ import annotations

import logging
from typing import Optional

from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager

logger = logging.getLogger(__name__)


class HandoffBuilder:
    """Builds budget-aware enriched prompts for delegation handoffs.

    Composes a structured prompt with sections for parent context,
    relevant procedures, and user preferences — without exceeding
    a configurable character budget for the handoff context.
    """

    def __init__(
        self,
        max_context_chars: int = 2000,
        procedure_limit: int = 3,
    ):
        self.max_context_chars = max(100, int(max_context_chars))
        self.procedure_limit = max(1, int(procedure_limit))

    def build(
        self,
        task_prompt: str,
        parent_memory: MemoryManager,
        global_memory: Optional[GlobalMemoryManager] = None,
        parent_persona_name: str = "",
    ) -> str:
        """Return an enriched prompt combining parent context with the task.

        If the parent has no relevant context to share, returns the raw
        task_prompt unchanged.
        """
        sections: list[str] = []
        budget_remaining = self.max_context_chars

        # 1. Parent memory excerpts (query-matched via section relevance)
        memory_excerpt = self._extract_memory_excerpt(
            parent_memory, task_prompt, budget_remaining
        )
        if memory_excerpt:
            sections.append(f"## Parent Memory Excerpts\n{memory_excerpt}")
            budget_remaining -= len(memory_excerpt)

        # 2. Matching procedures from parent
        procedures_text = self._extract_procedures(
            parent_memory, task_prompt, global_memory, budget_remaining
        )
        if procedures_text:
            sections.append(f"## Suggested Procedures\n{procedures_text}")
            budget_remaining -= len(procedures_text)

        # 3. User preferences from global memory
        preferences_text = self._extract_user_preferences(
            global_memory, budget_remaining
        )
        if preferences_text:
            sections.append(f"## User Preferences\n{preferences_text}")

        # If no context was gathered, return raw prompt
        if not sections:
            return task_prompt

        # Compose the enriched prompt
        delegation_header = "# DELEGATION CONTEXT"
        if parent_persona_name:
            delegation_header += f"\nDelegated by: {parent_persona_name}"

        context_block = f"{delegation_header}\n\n" + "\n\n".join(sections)

        return f"{context_block}\n\n# TASK\n{task_prompt}"

    def _extract_memory_excerpt(
        self,
        parent_memory: MemoryManager,
        task_prompt: str,
        budget: int,
    ) -> str:
        """Extract relevant sections from parent's MEMORY.md."""
        raw_memory = parent_memory.read_memory().strip()
        if not raw_memory or raw_memory == "# MEMORY":
            return ""

        # Parse sections and score by keyword overlap with task
        task_tokens = set(task_prompt.lower().split())
        sections = self._parse_memory_sections(raw_memory)

        scored: list[tuple[float, str]] = []
        for title, body in sections:
            section_tokens = set(title.lower().split()) | set(body.lower().split()[:50])
            overlap = len(task_tokens & section_tokens)
            if overlap > 0:
                scored.append((overlap, f"**{title}**: {body.strip()}"))

        if not scored:
            return ""

        scored.sort(key=lambda x: x[0], reverse=True)

        # Collect sections within budget
        lines: list[str] = []
        used = 0
        for _score, text in scored:
            if used + len(text) > budget:
                # Truncate the last fitting section
                remaining = budget - used
                if remaining > 50:
                    lines.append(text[:remaining] + "...")
                break
            lines.append(text)
            used += len(text) + 1  # +1 for newline

        return "\n".join(lines)

    @staticmethod
    def _parse_memory_sections(content: str) -> list[tuple[str, str]]:
        """Parse MEMORY.md into (title, body) pairs for ## sections."""
        sections: list[tuple[str, str]] = []
        current_title = ""
        current_lines: list[str] = []

        for line in content.splitlines():
            if line.startswith("## "):
                if current_title:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = line[3:].strip()
                current_lines = []
            elif current_title:
                current_lines.append(line)

        if current_title:
            sections.append((current_title, "\n".join(current_lines).strip()))

        return sections

    def _extract_procedures(
        self,
        parent_memory: MemoryManager,
        task_prompt: str,
        global_memory: Optional[GlobalMemoryManager],
        budget: int,
    ) -> str:
        """Extract matching procedures from parent's procedure store."""
        global_procedures = []
        if global_memory:
            global_procedures = global_memory.procedures.list_procedures()

        matched = parent_memory.match_procedures(
            task_prompt,
            global_procedures=global_procedures,
            limit=self.procedure_limit,
        )

        if not matched:
            return ""

        lines: list[str] = []
        used = 0
        for proc in matched:
            entry_lines = [f"**{proc.title}** (trigger: {proc.trigger})"]
            for i, step in enumerate(proc.steps, 1):
                entry_lines.append(f"  {i}. {step}")
            entry = "\n".join(entry_lines)

            if used + len(entry) > budget:
                break
            lines.append(entry)
            used += len(entry) + 1

        return "\n".join(lines)

    @staticmethod
    def _extract_user_preferences(
        global_memory: Optional[GlobalMemoryManager],
        budget: int,
    ) -> str:
        """Extract user preferences from global memory."""
        if not global_memory:
            return ""

        user_memory = global_memory.read_user_memory().strip()
        if not user_memory or user_memory == "# USER":
            return ""

        # Truncate to budget
        if len(user_memory) > budget:
            return user_memory[:budget] + "..."

        return user_memory
