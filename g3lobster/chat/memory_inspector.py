"""Memory-query intent detection and card builder.

Intercepts natural-language queries like "what do you remember about me?"
and builds a structured memory inspector response.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from g3lobster.chat.cards import (
    build_memory_inspector_card,
    build_memory_inspector_text,
)

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

MEMORY_QUERY_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"what\s+do\s+you\s+remember", re.IGNORECASE),
    re.compile(r"what\s+(?:have\s+you|do\s+you)\s+(?:learned|know)\s+about\s+me", re.IGNORECASE),
    re.compile(r"what\s+procedures?\s+(?:have\s+you|did\s+you)\s+learn", re.IGNORECASE),
    re.compile(r"show\s+(?:my\s+)?(?:preferences|memory|memories)", re.IGNORECASE),
    re.compile(r"(?:my|your)\s+memory", re.IGNORECASE),
    re.compile(r"what\s+do\s+you\s+know\s+about\s+me", re.IGNORECASE),
    re.compile(r"memory\s+inspector", re.IGNORECASE),
]


def detect_memory_query(text: str) -> Optional[str]:
    """Return a query-type string if *text* is a memory inspection request, else ``None``.

    The returned string is a short label like ``"memory_query"`` used for
    logging / metrics.  If the text does not match any known pattern, returns
    ``None`` so the caller can fall through to normal routing.
    """
    cleaned = text.strip()
    if not cleaned:
        return None
    for pattern in MEMORY_QUERY_PATTERNS:
        if pattern.search(cleaned):
            return "memory_query"
    return None


# ---------------------------------------------------------------------------
# Data gathering and card building
# ---------------------------------------------------------------------------

def _read_daily_notes(daily_dir: Path, limit: int = 5) -> List[str]:
    """Read the most recent daily note summaries."""
    if not daily_dir.exists():
        return []
    note_files = sorted(daily_dir.glob("*.md"), reverse=True)[:limit]
    summaries: List[str] = []
    for note_file in note_files:
        content = note_file.read_text(encoding="utf-8").strip()
        if content:
            # Take first meaningful line as summary
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    summaries.append(f"{note_file.stem}: {line}")
                    break
    return summaries


def _gather_stats(memory_manager: Any) -> Dict[str, Any]:
    """Gather agent stats from a MemoryManager instance."""
    sessions = memory_manager.sessions
    session_ids = sessions.list_sessions()

    total_messages = 0
    for sid in session_ids:
        total_messages += sessions.message_count(sid)

    memory_bytes = 0
    if memory_manager.memory_file.exists():
        memory_bytes = memory_manager.memory_file.stat().st_size

    daily_notes_count = len(list(memory_manager.daily_dir.glob("*.md")))
    procedures_count = len(memory_manager.list_procedures())

    return {
        "total_sessions": len(session_ids),
        "total_messages": total_messages,
        "memory_bytes": memory_bytes,
        "daily_notes": daily_notes_count,
        "procedures_count": procedures_count,
    }


def build_memory_response(
    agent_name: str,
    agent_emoji: str,
    agent_id: str,
    memory_manager: Any,
    global_memory: Any = None,
    user_id: Optional[str] = None,
    use_cards: bool = True,
) -> Dict[str, Any]:
    """Gather all memory data and return a card payload (or text fallback).

    Parameters
    ----------
    agent_name, agent_emoji : str
        Persona display info.
    agent_id : str
        The agent whose memory to inspect.
    memory_manager : MemoryManager
        Agent-level memory manager.
    global_memory : GlobalMemoryManager | None
        Cross-agent memory (for per-user preferences).
    user_id : str | None
        Google Chat user ID for per-user memory lookup.
    use_cards : bool
        If True, return a Cards v2 dict; otherwise return a text dict.

    Returns
    -------
    dict
        Either ``{"cardsV2": [...]}`` or ``{"text": "..."}`` suitable for
        passing directly to the Google Chat API.
    """
    # User preferences from tagged memory
    preferences = memory_manager.get_memories_by_tag("user preference")

    # Also pull per-user memory from global if available
    if global_memory and user_id:
        user_mem = global_memory.read_user_memory_for(user_id)
        # Extract non-header lines as preference context
        for line in user_mem.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                preferences.append(line)

    # Procedures
    raw_procedures = memory_manager.list_procedures()
    procedures: List[Dict[str, Any]] = [
        {
            "title": p.title,
            "trigger": p.trigger,
            "steps": p.steps,
            "weight": p.effective_weight,
            "status": p.status,
        }
        for p in raw_procedures
    ]

    # Daily notes
    daily_notes = _read_daily_notes(memory_manager.daily_dir)

    # Stats
    stats = _gather_stats(memory_manager)

    if use_cards:
        return build_memory_inspector_card(
            agent_name=agent_name,
            agent_emoji=agent_emoji,
            preferences=preferences,
            procedures=procedures,
            daily_notes=daily_notes,
            stats=stats,
        )

    text = build_memory_inspector_text(
        agent_name=agent_name,
        agent_emoji=agent_emoji,
        preferences=preferences,
        procedures=procedures,
        daily_notes=daily_notes,
        stats=stats,
    )
    return {"text": text}
