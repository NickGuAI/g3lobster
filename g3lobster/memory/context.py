"""Prompt context builder from markdown memory + transcript history."""

from __future__ import annotations

from typing import List

from g3lobster.memory.manager import MemoryManager


class ContextBuilder:
    """Builds request context from MEMORY.md + recent transcript entries."""

    def __init__(
        self,
        memory_manager: MemoryManager,
        message_limit: int = 12,
        system_preamble: str = "",
    ):
        self.memory_manager = memory_manager
        self.message_limit = message_limit
        self.system_preamble = system_preamble.strip()

    def build(self, session_id: str, prompt: str) -> str:
        memory_text = self.memory_manager.read_memory().strip()
        recent_entries = self.memory_manager.read_session(session_id, limit=self.message_limit)

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
                "# Persistent Memory",
                memory_text or "(empty)",
                "",
                "# Recent Conversation",
                "\n".join(history_lines) if history_lines else "(none)",
                "",
                "# New User Prompt",
                prompt.strip(),
            ]
        )
        return "\n".join(parts).strip() + "\n"
