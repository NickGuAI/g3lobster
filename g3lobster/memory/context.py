"""Prompt context builder from markdown memory + transcript history."""

from __future__ import annotations

from typing import List, Optional

from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.procedures import Procedure


class ContextBuilder:
    """Builds request context from MEMORY.md + recent transcript entries."""

    def __init__(
        self,
        memory_manager: MemoryManager,
        message_limit: int = 12,
        system_preamble: str = "",
        global_memory_manager: Optional[GlobalMemoryManager] = None,
        procedure_limit: int = 3,
    ):
        self.memory_manager = memory_manager
        self.message_limit = message_limit
        self.system_preamble = system_preamble.strip()
        self.global_memory_manager = global_memory_manager
        self.procedure_limit = max(1, int(procedure_limit))

    def build(self, session_id: str, prompt: str) -> str:
        memory_text = self.memory_manager.read_memory().strip()
        recent_entries = self.memory_manager.read_session_messages(session_id, limit=self.message_limit)
        compaction = self.memory_manager.read_latest_compaction(session_id) or {}

        user_memory = "(empty)"
        global_procedures: List[Procedure] = []
        if self.global_memory_manager:
            user_memory_text = self.global_memory_manager.read_user_memory().strip()
            user_memory = user_memory_text or "(empty)"
            global_procedures = self.global_memory_manager.procedures.list_procedures()

        matched = self.memory_manager.match_procedures(
            prompt,
            global_procedures=global_procedures,
            limit=self.procedure_limit,
        )

        history_lines: List[str] = []
        for entry in recent_entries:
            message = entry.get("message", {})
            role = message.get("role")
            content = str(message.get("content", "")).strip()
            if not role or not content:
                continue
            history_lines.append(f"{role}: {content}")

        parts = []
        if self.system_preamble:
            parts.extend(
                [
                    "# Agent Persona",
                    self.system_preamble,
                    "",
                ]
            )

        parts.extend(
            [
                "# User Preferences",
                user_memory,
                "",
                "# Agent Memory",
                memory_text or "(empty)",
                "",
                "# Known Procedures",
                self._format_procedures(matched),
                "",
                "# Compaction Summary",
                str(compaction.get("summary", "")).strip() or "(none)",
                "",
                "# Recent Conversation",
                "\n".join(history_lines) if history_lines else "(none)",
                "",
                "# New User Prompt",
                prompt.strip(),
            ]
        )
        return "\n".join(parts).strip() + "\n"

    @staticmethod
    def _format_procedures(procedures: List[Procedure]) -> str:
        if not procedures:
            return "(none)"

        lines: List[str] = []
        for procedure in procedures:
            lines.append(f"## {procedure.title}")
            lines.append(f"Trigger: {procedure.trigger}")
            lines.append("Steps:")
            for index, step in enumerate(procedure.steps, start=1):
                lines.append(f"{index}. {step}")
            lines.append("")
        return "\n".join(lines).rstrip()
