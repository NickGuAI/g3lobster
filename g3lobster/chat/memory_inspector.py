"""Memory query detection and card builder for the memory inspector.

Detects natural language queries like "what do you remember about me?" and
builds a Google Chat Cards v2 response showing agent memory, procedures,
daily notes, and stats.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from g3lobster.chat.cards import build_memory_inspector_card
from g3lobster.memory.manager import MemoryManager
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.procedures import Procedure

# Patterns that indicate a memory query from the user.
MEMORY_QUERY_PATTERNS = [
    re.compile(r"what\s+do\s+you\s+(?:remember|know)\s+(?:about\s+me|about\s+us)", re.IGNORECASE),
    re.compile(r"what\s+(?:have\s+you\s+)?(?:procedures?|procs?)\s+(?:have\s+you\s+)?learned", re.IGNORECASE),
    re.compile(r"show\s+(?:me\s+)?(?:my\s+)?(?:preferences|memory|memories)", re.IGNORECASE),
    re.compile(r"(?:my|show)\s+memory", re.IGNORECASE),
    re.compile(r"what\s+do\s+you\s+know\s+about\s+me", re.IGNORECASE),
    re.compile(r"what\s+(?:have\s+you\s+)?remember(?:ed)?", re.IGNORECASE),
    re.compile(r"memory\s+inspector", re.IGNORECASE),
]


def detect_memory_query(text: str) -> Optional[str]:
    """Return the query type if text is a memory inspection request, else None.

    Returns one of: ``"memory"``, or ``None``.
    """
    cleaned = text.strip()
    # Strip leading @mention if present
    cleaned = re.sub(r"^@\S+\s*", "", cleaned).strip()
    for pattern in MEMORY_QUERY_PATTERNS:
        if pattern.search(cleaned):
            return "memory"
    return None


def _gather_preferences(
    memory_manager: MemoryManager,
    global_memory: Optional[GlobalMemoryManager],
    user_id: str,
) -> List[str]:
    """Gather user preferences from tagged memory entries."""
    prefs: List[str] = []
    # Agent-level tagged memories with "preference" or "user preference"
    for tag in ("preference", "user preference"):
        prefs.extend(memory_manager.get_memories_by_tag(tag))
    # Global per-user memory
    if global_memory:
        user_mem = global_memory.read_user_memory_for(user_id)
        # Extract non-header lines as preference items
        for line in user_mem.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                prefs.append(line)
    return prefs


def _gather_procedures(
    memory_manager: MemoryManager,
    global_memory: Optional[GlobalMemoryManager],
) -> List[Dict[str, Any]]:
    """Gather learned procedures from agent and global stores."""
    all_procs: List[Procedure] = []

    # Agent-level procedures (permanent + usable candidates)
    all_procs.extend(memory_manager.list_procedures())
    usable = memory_manager.candidate_store.list_usable()
    all_procs.extend(usable)

    # Global procedures
    if global_memory:
        all_procs.extend(global_memory.procedures.list_procedures())

    # Deduplicate by key
    seen_keys: set = set()
    unique: List[Procedure] = []
    for proc in all_procs:
        if proc.key not in seen_keys:
            seen_keys.add(proc.key)
            unique.append(proc)

    # Sort by effective weight descending
    unique.sort(key=lambda p: p.effective_weight, reverse=True)

    return [
        {
            "title": proc.title,
            "weight": proc.effective_weight,
            "status": proc.status,
            "steps_count": len(proc.steps),
        }
        for proc in unique
    ]


def _gather_daily_notes(memory_manager: MemoryManager, limit: int = 5) -> List[str]:
    """Read the most recent daily notes (up to *limit*)."""
    daily_dir = memory_manager.daily_dir
    if not daily_dir.exists():
        return []
    note_files = sorted(daily_dir.glob("*.md"), reverse=True)[:limit]
    notes: List[str] = []
    for path in note_files:
        content = path.read_text(encoding="utf-8").strip()
        if content:
            label = path.stem  # e.g. "2026-03-10"
            notes.append(f"*{label}*: {content[:250]}")
    return notes


def _gather_stats(memory_manager: MemoryManager) -> Dict[str, Any]:
    """Gather agent stats from sessions and memory files."""
    sessions = memory_manager.sessions
    session_ids = sessions.list_sessions()

    total_messages = 0
    for sid in session_ids:
        total_messages += sessions.message_count(sid)

    memory_file = memory_manager.memory_file
    memory_bytes = memory_file.stat().st_size if memory_file.exists() else 0

    daily_dir = memory_manager.daily_dir
    daily_notes_count = len(list(daily_dir.glob("*.md"))) if daily_dir.exists() else 0

    return {
        "sessions_total": len(session_ids),
        "messages_total": total_messages,
        "memory_bytes": memory_bytes,
        "daily_notes_count": daily_notes_count,
    }


async def build_memory_card(
    agent_name: str,
    agent_emoji: str,
    memory_manager: MemoryManager,
    global_memory: Optional[GlobalMemoryManager],
    user_id: str,
) -> Dict[str, Any]:
    """Build a complete memory inspector Cards v2 response.

    Returns a dict with ``cardsV2`` key ready to send via ChatBridge.
    """
    preferences = _gather_preferences(memory_manager, global_memory, user_id)
    procedures = _gather_procedures(memory_manager, global_memory)
    daily_notes = _gather_daily_notes(memory_manager)
    stats = _gather_stats(memory_manager)

    cards_v2 = build_memory_inspector_card(
        agent_name=agent_name,
        agent_emoji=agent_emoji,
        preferences=preferences,
        procedures=procedures,
        daily_notes=daily_notes,
        stats=stats,
    )

    return {"cardsV2": cards_v2}
