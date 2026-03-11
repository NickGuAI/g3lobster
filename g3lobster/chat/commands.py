"""Slash-command interception for Google Chat messages.

When a user sends ``@agent /cron list`` (or any ``/<cmd>`` prefix), this module
intercepts the message *before* it reaches the AI, handles it locally, and
returns a formatted text reply.  If the command is unrecognised, ``None`` is
returned so the caller can fall through to normal AI routing.

Supported commands
------------------
``/help``                              — list available commands
``/status``                            — fleet dashboard (Cards v2)
``/memory``                            — memory inspector card (Cards v2)
``/forget <type> <id>``                — forget a specific memory item
``/cron list``                         — list scheduled tasks for this agent
``/cron add "<schedule>" "<task>"``    — create a cron task
``/cron delete <id>``                  — delete a cron task
``/cron enable <id>``                  — enable a disabled task
``/cron disable <id>``                 — disable a task without deleting it
"""

from __future__ import annotations

import re
import shlex
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

if TYPE_CHECKING:
    from g3lobster.agents.registry import AgentRegistry
    from g3lobster.cron.store import CronStore

# Matches a leading slash command token anywhere after optional whitespace /
# (handles both "/cron list" and "@robo /cron list" after the mention is stripped).
_SLASH_RE = re.compile(r"(?:^|\s)/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+(.*))?", re.DOTALL)

HELP_TEXT = """\
*Available commands*
• `/help` — show this message
• `/status` — fleet dashboard showing all agents at a glance
• `/memory` — show what the agent remembers about you
• `/forget <type> <id>` — forget a memory item (type: preference, procedure)
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

    Recognised commands: ``help``, ``status``, ``memory``, ``forget``, ``cron``, ``sleep``.
    """
    m = _SLASH_RE.search(text)
    if not m:
        return None
    cmd = m.group(1).lower()
    rest = (m.group(2) or "").strip()
    return cmd, rest


async def handle(
    text: str,
    agent_id: str,
    cron_store: "CronStore",
    registry: Optional["AgentRegistry"] = None,
) -> Optional[Union[str, Dict[str, Any]]]:
    """Intercept and handle a slash command.

    Returns a reply string (or Cards v2 dict for ``/status``) when the command
    is handled, or ``None`` when the message should be forwarded to the AI agent.
    """
    result = detect_command(text)
    if result is None:
        return None

    cmd, rest = result

    if cmd == "help":
        return HELP_TEXT

    if cmd == "status":
        return await _handle_status(registry)

    if cmd == "memory":
        return await _handle_memory(agent_id, registry)

    if cmd == "forget":
        return _handle_forget(rest, agent_id, registry)

    if cmd == "cron":
        return _handle_cron(rest, agent_id, cron_store)

    if cmd == "sleep":
        return _handle_sleep(rest, agent_id)

    # Unknown command — fall through to AI
    return None


async def _handle_memory(
    agent_id: str,
    registry: Optional["AgentRegistry"],
) -> Union[str, Dict[str, Any]]:
    """Handle /memory — returns a Cards v2 memory inspector card."""
    if registry is None:
        return "⚠️ Memory inspector unavailable — registry not connected."

    runtime = registry.get_agent(agent_id)
    if not runtime:
        return f"⚠️ Agent `{agent_id}` is not running."

    from g3lobster.chat.memory_inspector import build_memory_card

    global_memory = getattr(registry, "global_memory", None)
    card = await build_memory_card(
        agent_name=runtime.persona.name,
        agent_emoji=runtime.persona.emoji,
        memory_manager=runtime.memory_manager,
        global_memory=global_memory,
        user_id="slash-command",
    )
    return card


def _handle_forget(
    args: str,
    agent_id: str,
    registry: Optional["AgentRegistry"],
) -> str:
    """Handle /forget <type> <id> — remove a specific memory item."""
    if registry is None:
        return "⚠️ Forget unavailable — registry not connected."

    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        return "Usage: `/forget <type> <id>`\nTypes: `preference <index>`, `procedure <title>`"

    item_type, item_id = parts[0].lower(), parts[1].strip()

    runtime = registry.get_agent(agent_id)
    if not runtime:
        return f"⚠️ Agent `{agent_id}` is not running."

    manager = runtime.memory_manager

    if item_type == "preference":
        try:
            index = int(item_id)
        except ValueError:
            return f"Invalid preference index: `{item_id}`. Must be a number."
        deleted = manager.delete_tagged_memory("preference", index)
        if not deleted:
            deleted = manager.delete_tagged_memory("user preference", index)
        if deleted:
            return f"🗑 Forgot preference #{index}."
        return f"No preference found at index {index}."

    if item_type == "procedure":
        # Remove from permanent procedure store
        procs = manager.list_procedures()
        matched = [p for p in procs if p.title.lower() == item_id.lower()]
        if not matched:
            return f"No procedure found with title `{item_id}`."
        remaining = [p for p in procs if p.title.lower() != item_id.lower()]
        manager.procedure_store.save_procedures(remaining)
        return f"🗑 Forgot procedure `{matched[0].title}`."

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


# ---------------------------------------------------------------------------
# /status — Fleet Dashboard Card
# ---------------------------------------------------------------------------

_STATE_PILLS: Dict[str, str] = {
    "idle": "🟢 idle",
    "busy": "🟡 busy",
    "starting": "🟡 starting",
    "stuck": "🔴 stuck",
    "dead": "🔴 dead",
    "stopped": "⚪ stopped",
    "sleeping": "⚪ sleeping",
}


def _format_uptime(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _build_agent_section(agent: Dict[str, Any]) -> Dict[str, Any]:
    """Build a Cards v2 section for a single agent."""
    state = str(agent.get("state", "stopped"))
    pill = _STATE_PILLS.get(state, f"⚫ {state}")
    emoji = agent.get("emoji", "🤖")
    name = agent.get("name", agent.get("id", "unknown"))
    uptime = _format_uptime(int(agent.get("uptime_s", 0)))
    task = agent.get("current_task") or "—"
    pending = int(agent.get("pending_assignments", 0))

    widgets: List[Dict[str, Any]] = [
        {
            "decoratedText": {
                "topLabel": f"{emoji} {name}",
                "text": pill,
                "bottomLabel": f"Uptime: {uptime}  |  Task: {task}  |  Queued: {pending}",
            }
        },
    ]
    return {"widgets": widgets}


def _build_status_card(
    status_data: Dict[str, Any],
    bridge_running: bool = False,
) -> List[Dict[str, Any]]:
    """Build a Cards v2 payload from registry.status() data."""
    agents: List[Dict[str, Any]] = status_data.get("agents", [])

    # Fleet summary counts
    active = sum(1 for a in agents if a.get("state") not in ("stopped", "dead"))
    stopped = sum(1 for a in agents if a.get("state") in ("stopped", "dead"))
    bridge_label = "🟢 running" if bridge_running else "🔴 stopped"

    sections: List[Dict[str, Any]] = [
        {
            "header": f"Fleet: {active} active · {stopped} stopped · {len(agents)} total",
            "widgets": [
                {
                    "decoratedText": {
                        "text": (
                            f"Bridge: {bridge_label} &nbsp;|&nbsp; "
                            f"<b>{active}</b> active &nbsp;|&nbsp; "
                            f"<b>{stopped}</b> stopped &nbsp;|&nbsp; "
                            f"<b>{len(agents)}</b> total"
                        ),
                    }
                },
            ],
        },
    ]

    for agent in agents:
        sections.append(_build_agent_section(agent))

    return [
        {
            "cardId": "fleet-status",
            "card": {
                "header": {
                    "title": "Fleet Status",
                    "subtitle": f"{len(agents)} agents registered",
                    "imageUrl": "",
                    "imageType": "CIRCLE",
                },
                "sections": sections,
            },
        }
    ]


async def _handle_status(
    registry: Optional["AgentRegistry"],
) -> Union[str, Dict[str, Any]]:
    """Handle /status — returns a Cards v2 dict or fallback text."""
    if registry is None:
        return "⚠️ Fleet status unavailable — registry not connected."

    status_data = await registry.status()
    bridge = getattr(registry, "chat_bridge", None)
    bridge_running = bool(getattr(bridge, "is_running", False)) if bridge else False
    cards_v2 = _build_status_card(status_data, bridge_running=bridge_running)
    return {"cardsV2": cards_v2}


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
