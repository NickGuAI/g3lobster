"""Slash-command interception for Google Chat messages.

When a user sends ``@agent /cron list`` (or any ``/<cmd>`` prefix), this module
intercepts the message *before* it reaches the AI, handles it locally, and
returns a formatted text reply.  If the command is unrecognised, ``None`` is
returned so the caller can fall through to normal AI routing.

Supported commands
------------------
``/help``                              — list available commands
``/cron list``                         — list scheduled tasks for this agent
``/cron add "<schedule>" "<task>"``    — create a cron task
``/cron delete <id>``                  — delete a cron task
``/cron enable <id>``                  — enable a disabled task
``/cron disable <id>``                 — disable a task without deleting it
"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from g3lobster.cron.store import CronStore

# Matches a leading slash command token anywhere after optional whitespace /
# (handles both "/cron list" and "@robo /cron list" after the mention is stripped).
_SLASH_RE = re.compile(r"(?:^|\s)/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+(.*))?", re.DOTALL)

HELP_TEXT = """\
*Available commands*
• `/help` — show this message
• `/memory` — show what this agent remembers about you
• `/forget <type> <id>` — forget a specific memory item
  _type_: `preference` or `procedure`
  _id_: item index (for preferences) or title (for procedures)
• `/cron list` — list scheduled tasks for this agent
• `/cron add "<schedule>" "<instruction>"` — create a new cron task
  _schedule_: standard 5-field cron expression, e.g. `0 9 * * *`
• `/cron delete <id>` — delete a task by its ID
• `/cron enable <id>` — enable a disabled task
• `/cron disable <id>` — disable a task (keeps it)
• `/sleep <seconds>` — put the agent to sleep for a duration
"""


def detect_command(text: str) -> Optional[tuple[str, str]]:
    """Return ``(command, rest)`` if text contains a ``/`` command, else ``None``.

    Recognised commands: ``help``, ``memory``, ``forget``, ``cron``, ``sleep``.
    """
    m = _SLASH_RE.search(text)
    if not m:
        return None
    cmd = m.group(1).lower()
    rest = (m.group(2) or "").strip()
    return cmd, rest


def handle(
    text: str,
    agent_id: str,
    cron_store: "CronStore",
    memory_manager: Any = None,
    global_memory: Any = None,
    persona: Any = None,
) -> Optional[str]:
    """Intercept and handle a slash command.

    Returns a reply string when the command is handled, or ``None`` when
    the message should be forwarded to the AI agent instead.
    """
    result = detect_command(text)
    if result is None:
        return None

    cmd, rest = result

    if cmd == "help":
        return HELP_TEXT

    if cmd == "memory":
        return _handle_memory(agent_id, memory_manager, global_memory, persona)

    if cmd == "forget":
        return _handle_forget(rest, agent_id, memory_manager)

    if cmd == "cron":
        return _handle_cron(rest, agent_id, cron_store)

    if cmd == "sleep":
        return _handle_sleep(rest, agent_id)

    # Unknown command — fall through to AI
    return None


def _handle_memory(
    agent_id: str,
    memory_manager: Any = None,
    global_memory: Any = None,
    persona: Any = None,
) -> str:
    """Handle /memory — return a text-format memory inspector."""
    if memory_manager is None:
        return "Memory system not available for this agent."

    from g3lobster.chat.memory_inspector import build_memory_response

    name = getattr(persona, "name", agent_id) if persona else agent_id
    emoji = getattr(persona, "emoji", "") if persona else ""

    result = build_memory_response(
        agent_name=name,
        agent_emoji=emoji,
        agent_id=agent_id,
        memory_manager=memory_manager,
        global_memory=global_memory,
        use_cards=False,
    )
    return result.get("text", "No memory data available.")


def _handle_forget(args: str, agent_id: str, memory_manager: Any = None) -> str:
    """Handle /forget <type> <id> — remove a specific memory item."""
    if memory_manager is None:
        return "Memory system not available for this agent."

    parts = args.split(None, 1)
    if len(parts) < 2:
        return "Usage: `/forget <type> <id>`\n_type_: `preference` or `procedure`\n_id_: item index or title"

    item_type, item_id = parts[0].lower(), parts[1].strip()

    if item_type == "preference":
        try:
            index = int(item_id)
        except ValueError:
            return f"Invalid preference index: `{item_id}`. Must be a number."
        deleted = memory_manager.delete_tagged_memory("user preference", index)
        if deleted:
            return f"Forgot preference #{index}."
        return f"Preference #{index} not found."

    if item_type == "procedure":
        procedures = memory_manager.list_procedures()
        matched = [p for p in procedures if p.title.lower() == item_id.lower()]
        if not matched:
            matched = [p for p in procedures if item_id.lower() in p.title.lower()]
        if not matched:
            return f"No procedure found matching `{item_id}`."
        # Remove from PROCEDURES.md by rewriting without the matched procedure
        remaining = [p for p in procedures if p not in matched]
        memory_manager.procedure_store.save_procedures(remaining)
        return f"Forgot procedure: *{matched[0].title}*."

    return f"Unknown memory type: `{item_type}`. Use `preference` or `procedure`."


def _handle_sleep(args: str, agent_id: str) -> str:
    """Handle /sleep command. Returns instruction text — actual sleep is triggered by the caller."""
    args = args.strip()
    if not args:
        return "Usage: `/sleep <seconds>` — put agent to sleep.\nExample: `/sleep 3600` (sleep for 1 hour)"
    try:
        duration = float(args)
    except ValueError:
        return f"Invalid duration: `{args}`. Must be a number (seconds)."
    if duration <= 0:
        return "Duration must be positive."
    if duration > 86400:
        return "Maximum sleep duration is 86400 seconds (24 hours)."
    # Return a special marker that the bridge will detect and act on
    return f"__SLEEP__:{duration}:{agent_id}"


def _handle_cron(args: str, agent_id: str, cron_store: "CronStore") -> str:
    parts = args.split(None, 1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "list":
        return _cron_list(agent_id, cron_store)
    if sub == "add":
        return _cron_add(rest, agent_id, cron_store)
    if sub == "delete":
        return _cron_delete(rest.strip(), agent_id, cron_store)
    if sub == "enable":
        return _cron_toggle(rest.strip(), agent_id, cron_store, enabled=True)
    if sub == "disable":
        return _cron_toggle(rest.strip(), agent_id, cron_store, enabled=False)

    return f"Unknown cron sub-command: `{sub}`. Try `/cron list`."


def _cron_list(agent_id: str, cron_store: "CronStore") -> str:
    tasks = cron_store.list_tasks(agent_id)
    if not tasks:
        return f"No cron tasks for `{agent_id}`. Add one with `/cron add`."
    lines = [f"*Cron tasks for {agent_id}:*"]
    for t in tasks:
        status = "✅" if t.enabled else "⏸"
        last = t.last_run[:19].replace("T", " ") if t.last_run else "never"
        lines.append(f"{status} `{t.id[:8]}` | `{t.schedule}` | last: {last}\n  _{t.instruction}_")
    return "\n".join(lines)


def _cron_add(args: str, agent_id: str, cron_store: "CronStore") -> str:
    try:
        tokens = shlex.split(args)
    except ValueError as exc:
        return f"Parse error: {exc}. Usage: `/cron add \"<schedule>\" \"<instruction>\"`"
    if len(tokens) < 2:
        return "Usage: `/cron add \"<schedule>\" \"<instruction>\"`\nExample: `/cron add \"0 9 * * *\" \"Send morning briefing\"`"
    schedule, instruction = tokens[0], " ".join(tokens[1:])
    try:
        task = cron_store.add_task(agent_id, schedule, instruction)
        return f"✅ Cron task created: `{task.id[:8]}` | `{schedule}` | _{instruction}_"
    except Exception as exc:
        return f"Failed to create task: {exc}"


def _cron_delete(task_id_prefix: str, agent_id: str, cron_store: "CronStore") -> str:
    if not task_id_prefix:
        return "Usage: `/cron delete <id>`"
    tasks = cron_store.list_tasks(agent_id)
    matched = [t for t in tasks if t.id.startswith(task_id_prefix)]
    if not matched:
        return f"No task found with ID starting with `{task_id_prefix}`."
    if len(matched) > 1:
        return f"Ambiguous ID prefix `{task_id_prefix}` — matches {len(matched)} tasks. Use more characters."
    deleted = cron_store.delete_task(agent_id, matched[0].id)
    if deleted:
        return f"🗑 Deleted task `{matched[0].id[:8]}`."
    return "Failed to delete task."


def _cron_toggle(task_id_prefix: str, agent_id: str, cron_store: "CronStore", *, enabled: bool) -> str:
    if not task_id_prefix:
        verb = "enable" if enabled else "disable"
        return f"Usage: `/cron {verb} <id>`"
    tasks = cron_store.list_tasks(agent_id)
    matched = [t for t in tasks if t.id.startswith(task_id_prefix)]
    if not matched:
        return f"No task found with ID starting with `{task_id_prefix}`."
    if len(matched) > 1:
        return f"Ambiguous ID prefix `{task_id_prefix}`."
    updated = cron_store.update_task(agent_id, matched[0].id, enabled=enabled)
    if updated:
        verb = "enabled" if enabled else "disabled"
        return f"{'▶' if enabled else '⏸'} Task `{matched[0].id[:8]}` {verb}."
    return "Failed to update task."
