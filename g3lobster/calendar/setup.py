"""Setup helpers for the calendar conflict-resolver agent.

Provides functions to create the conflict-resolver agent persona and
register the periodic conflict-scanning cron task.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# The SOUL.md content that instructs the conflict-resolver agent on how
# to handle calendar interactions via Chat.
CONFLICT_RESOLVER_SOUL = """\
# Conflict Resolver Agent

You are a calendar conflict-resolution assistant. Your job is to proactively
detect scheduling conflicts on the user's Google Calendar and help resolve them
through Chat conversation.

## Capabilities

You have access to these calendar tools via MCP:

- **check_conflicts** — Scan for overlapping events on the user's calendar
- **find_meeting_slots** — Find common free times across multiple people
- **reschedule_event** — Move an event to a new time (requires user approval)
- **create_event** — Create a new calendar event (requires user approval)

## Behavior

### Proactive Conflict Detection
When triggered by a cron task, scan the user's calendar for the next 8 hours.
If conflicts are found, send a notification listing each conflict with:
- Event names and times
- Overlap duration
- Numbered options for resolution

Example notification:
```
⚠️ Calendar Conflicts Detected (1 found):

1. *Standup* (09:00-09:30) overlaps with *1:1 with Alex* (09:15-10:00) — 15 min overlap

Reply with a number to reschedule that conflict, or say "reschedule <event> to <time>".
```

### Handling User Replies
When a user replies to a conflict notification:
- If they reply with a number (e.g., "1"), identify the conflict and suggest
  rescheduling the shorter/less important event to the next available slot
- If they say "reschedule <event> to <time>", use reschedule_event to move it
- ALWAYS confirm before executing any write operation: "I'll reschedule
  *Standup* to 10:00-10:30. OK?"
- Only execute after explicit approval ("yes", "ok", "do it", etc.)

### Multi-Person Scheduling
When a user asks to "find a time for me, X, and Y":
1. Use find_meeting_slots with the attendee emails
2. Present the top 3-5 available slots as numbered options
3. On selection, use create_event with all attendees
4. Confirm before creating

### Rules
- NEVER modify or delete events without explicit user approval
- Always show what you're about to do before doing it
- Keep messages concise and actionable
- If you can't resolve a conflict automatically, explain why and suggest
  manual alternatives
"""

CONFLICT_RESOLVER_AGENT_ID = "conflict-resolver"
CONFLICT_RESOLVER_AGENT_NAME = "Conflict Resolver"
CONFLICT_RESOLVER_EMOJI = "📅"
CONFLICT_SCAN_SCHEDULE = "*/15 9-17 * * 1-5"  # Every 15 min, 9am-5pm, Mon-Fri
CONFLICT_SCAN_INSTRUCTION = (
    "Scan my calendar for the next 8 hours using check_conflicts. "
    "If any conflicts are found, notify me with details and resolution options. "
    "If no conflicts, do nothing."
)


def setup_conflict_resolver(
    data_dir: str,
    cron_store: Optional[object] = None,
) -> dict:
    """Create the conflict-resolver agent persona and optional cron task.

    Args:
        data_dir: The g3lobster data directory (e.g., ~/.gemini_chat_bridge).
        cron_store: Optional CronStore instance. If provided, registers
            the periodic conflict-scanning cron task.

    Returns:
        dict with keys: agent_id, persona_created, cron_task_created
    """
    from g3lobster.agents.persona import AgentPersona, load_persona, save_persona

    result = {
        "agent_id": CONFLICT_RESOLVER_AGENT_ID,
        "persona_created": False,
        "cron_task_created": False,
    }

    # Create persona if it doesn't exist
    existing = load_persona(data_dir, CONFLICT_RESOLVER_AGENT_ID)
    if existing is None:
        persona = AgentPersona(
            id=CONFLICT_RESOLVER_AGENT_ID,
            name=CONFLICT_RESOLVER_AGENT_NAME,
            emoji=CONFLICT_RESOLVER_EMOJI,
            soul=CONFLICT_RESOLVER_SOUL,
            model="gemini",
            enabled=True,
            bridge_enabled=True,
        )
        save_persona(data_dir, persona)
        result["persona_created"] = True
        logger.info("Created conflict-resolver agent persona")
    else:
        logger.info("Conflict-resolver agent persona already exists")

    # Register cron task if store provided
    if cron_store is not None:
        existing_tasks = cron_store.list_tasks(CONFLICT_RESOLVER_AGENT_ID)
        has_scan_task = any(
            "check_conflicts" in t.instruction or "conflict" in t.instruction.lower()
            for t in existing_tasks
        )
        if not has_scan_task:
            cron_store.add_task(
                CONFLICT_RESOLVER_AGENT_ID,
                CONFLICT_SCAN_SCHEDULE,
                CONFLICT_SCAN_INSTRUCTION,
            )
            result["cron_task_created"] = True
            logger.info("Registered conflict-scan cron task")
        else:
            logger.info("Conflict-scan cron task already exists")

    return result
