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
``/incident <title>``                  — declare a new incident
``/incident status <update>``          — add a status update to the active incident
``/incident assign <role> <@user>``    — assign an incident role
``/incident action <description>``     — add an action item
``/resolve [summary]``                 — resolve the active incident
"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from g3lobster.cron.store import CronStore
    from g3lobster.incident.store import IncidentStore

# Matches a leading slash command token anywhere after optional whitespace /
# (handles both "/cron list" and "@robo /cron list" after the mention is stripped).
_SLASH_RE = re.compile(r"(?:^|\s)/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+(.*))?", re.DOTALL)

HELP_TEXT = """\
*Available commands*
• `/help` — show this message
• `/cron list` — list scheduled tasks for this agent
• `/cron add "<schedule>" "<instruction>"` — create a new cron task
  _schedule_: standard 5-field cron expression, e.g. `0 9 * * *`
• `/cron delete <id>` — delete a task by its ID
• `/cron enable <id>` — enable a disabled task
• `/cron disable <id>` — disable a task (keeps it)
• `/sleep <seconds>` — put the agent to sleep for a duration
• `/incident <title>` — declare a new incident
• `/incident status <update>` — add a timeline status update
• `/incident assign <role> <@user>` — assign an incident role
• `/incident action <description>` — track an action item
• `/resolve [summary]` — resolve the active incident and post summary
"""


def detect_command(text: str) -> Optional[tuple[str, str]]:
    """Return ``(command, rest)`` if text contains a ``/`` command, else ``None``.

    Recognised commands: ``help``, ``cron``, ``sleep``.
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
    incident_store: Optional["IncidentStore"] = None,
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

    if cmd == "cron":
        return _handle_cron(rest, agent_id, cron_store)

    if cmd == "sleep":
        return _handle_sleep(rest, agent_id)

    if cmd == "incident" and incident_store is not None:
        return _handle_incident(rest, agent_id, incident_store, cron_store)

    if cmd == "resolve" and incident_store is not None:
        return _handle_resolve(rest, agent_id, incident_store, cron_store)

    # Unknown command — fall through to AI
    return None


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


# ------------------------------------------------------------------
# Incident commands
# ------------------------------------------------------------------

_INCIDENT_CRON_SCHEDULE = "*/15 * * * *"
_INCIDENT_PROMPT_PREFIX = "__INCIDENT_PROMPT__"


def _handle_incident(
    args: str, agent_id: str, incident_store: "IncidentStore", cron_store: "CronStore",
) -> str:
    from g3lobster.incident.formatter import format_incident_card

    parts = args.split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "status":
        return _incident_status(rest, agent_id, incident_store)
    if sub == "assign":
        return _incident_assign(rest, agent_id, incident_store)
    if sub == "action":
        return _incident_action(rest, agent_id, incident_store)

    # Default: create a new incident. The entire args string is the title.
    title = args.strip()
    if not title:
        return (
            "Usage:\n"
            "• `/incident <title>` — declare a new incident\n"
            "• `/incident status <update>` — add a status update\n"
            "• `/incident assign <role> <@user>` — assign a role\n"
            "• `/incident action <description>` — add an action item"
        )

    incident = incident_store.create(agent_id, title)

    # Schedule periodic status prompts via cron
    try:
        cron_task = cron_store.add_task(
            agent_id,
            _INCIDENT_CRON_SCHEDULE,
            f"{_INCIDENT_PROMPT_PREFIX}:{incident.id}",
        )
        incident.cron_task_id = cron_task.id
        incident_store._save(agent_id, incident)
    except Exception:
        pass  # Non-fatal — incident still works without prompts

    return f"🚨 *Incident declared*\n\n{format_incident_card(incident)}"


def _incident_status(args: str, agent_id: str, incident_store: "IncidentStore") -> str:
    content = args.strip()
    if not content:
        return "Usage: `/incident status <update>`"
    incident = incident_store.get_active(agent_id)
    if not incident:
        return "No active incident. Start one with `/incident <title>`."
    updated = incident_store.append_timeline(agent_id, incident.id, "user", content, "status")
    if not updated:
        return "Failed to update timeline."
    open_actions = sum(1 for a in updated.actions if a.status == "open")
    return f"✅ Timeline updated. {len(updated.timeline)} entries, {open_actions} open action items."


def _incident_assign(args: str, agent_id: str, incident_store: "IncidentStore") -> str:
    parts = args.split(None, 1)
    if len(parts) < 2:
        return "Usage: `/incident assign <role> <@user>`\nExample: `/incident assign commander @alice`"
    role, user = parts[0], parts[1]
    incident = incident_store.get_active(agent_id)
    if not incident:
        return "No active incident. Start one with `/incident <title>`."
    updated = incident_store.add_role(agent_id, incident.id, role, user)
    if not updated:
        return "Failed to assign role."
    if role.lower() == "commander":
        incident_store.get(agent_id, incident.id)
        # Update commander field separately
        inc = incident_store.get(agent_id, incident.id)
        if inc:
            inc.commander = user
            incident_store._save(agent_id, inc)
    return f"✅ Role `{role}` assigned to {user}."


def _incident_action(args: str, agent_id: str, incident_store: "IncidentStore") -> str:
    description = args.strip()
    if not description:
        return "Usage: `/incident action <description>`"
    incident = incident_store.get_active(agent_id)
    if not incident:
        return "No active incident. Start one with `/incident <title>`."
    updated = incident_store.add_action(agent_id, incident.id, description)
    if not updated:
        return "Failed to add action item."
    open_count = sum(1 for a in updated.actions if a.status == "open")
    return f"✅ Action item added. {open_count} open / {len(updated.actions)} total."


def _handle_resolve(
    args: str, agent_id: str, incident_store: "IncidentStore", cron_store: "CronStore",
) -> str:
    from g3lobster.incident.formatter import format_resolution_summary

    incident = incident_store.get_active(agent_id)
    if not incident:
        return "No active incident to resolve."

    summary = args.strip()

    # Remove the cron status prompt task
    if incident.cron_task_id:
        try:
            cron_store.delete_task(agent_id, incident.cron_task_id)
        except Exception:
            pass

    resolved = incident_store.resolve(agent_id, incident.id, summary)
    if not resolved:
        return "Failed to resolve incident."

    return f"✅ *Incident resolved*\n\n{format_resolution_summary(resolved)}"
