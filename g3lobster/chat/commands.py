"""Slash-command interception for Google Chat messages.

When a user sends ``@agent /cron list`` (or any ``/<cmd>`` prefix), this module
intercepts the message *before* it reaches the AI, handles it locally, and
returns a formatted text reply.  If the command is unrecognised, ``None`` is
returned so the caller can fall through to normal AI routing.

Supported commands
------------------
``/help``                              — list available commands
``/status``                            — fleet dashboard (Cards v2)
``/quick``                             — show quick action buttons
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
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

if TYPE_CHECKING:
    from g3lobster.agents.registry import AgentRegistry
    from g3lobster.cron.store import CronStore
    from g3lobster.memory.global_memory import GlobalMemoryManager
    from g3lobster.memory.manager import MemoryManager
    from g3lobster.incident.store import IncidentStore

# Matches a leading slash command token anywhere after optional whitespace /
# (handles both "/cron list" and "@robo /cron list" after the mention is stripped).
_SLASH_RE = re.compile(r"(?:^|\s)/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+(.*))?", re.DOTALL)

HELP_TEXT = """\
*Available commands*
• `/help` — show this message
• `/status` — fleet dashboard showing all agents at a glance
• `/quick` — show quick action buttons
• `/catchup` — summarize this thread with cross-referenced context
• `/cron list` — list scheduled tasks for this agent
• `/cron add "<schedule>" "<instruction>"` — create a new cron task
  _schedule_: standard 5-field cron expression, e.g. `0 9 * * *`
• `/cron delete <id>` — delete a task by its ID
• `/cron enable <id>` — enable a disabled task
• `/cron disable <id>` — disable a task (keeps it)
• `/sleep <seconds>` — put the agent to sleep for a duration
• `/teach <fact>` — teach the agents something new
• `/teach list` — list all taught knowledge
• `/teach forget <keyword>` — remove a knowledge entry
• `/memory` — show what the agent remembers about you
• `/forget preference <number>` — remove a stored preference
• `/forget procedure <title>` — remove a learned procedure
• `/incident <title>` — declare a new incident
• `/incident status <update>` — add a timeline status update
• `/incident assign <role> <@user>` — assign an incident role
• `/incident action <description>` — track an action item
• `/resolve [summary]` — resolve the active incident and post summary
"""


def _handle_help(registry: Optional["AgentRegistry"]) -> str:
    """Build /help output including available agents with their slug names."""
    lines = [HELP_TEXT]
    if registry is not None:
        try:
            personas = registry.list_enabled_personas()
        except Exception:
            personas = []
        if personas:
            lines.append("\n*Available agents*")
            for p in personas:
                slugs = [f"/{p.id}"]
                for alias in getattr(p, "aliases", []):
                    slugs.append(f"/{alias}")
                slug_str = ", ".join(slugs)
                lines.append(f"\u2022 {slug_str} \u2014 {p.emoji} {p.name}")
    return "\n".join(lines)


def detect_command(text: str) -> Optional[tuple[str, str]]:
    """Return ``(command, rest)`` if text contains a ``/`` command, else ``None``.

    Recognised commands: ``help``, ``status``, ``quick``, ``teach``, ``cron``, ``sleep``, ``memory``, ``forget``, ``catchup``.
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
    global_memory: Optional["GlobalMemoryManager"] = None,
    incident_store: Optional["IncidentStore"] = None,
    thread_id: str = "",
    space_id: str = "",
    user_id: str = "",
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
        return _handle_help(registry)

    if cmd == "quick":
        return _handle_quick()

    if cmd == "status":
        return await _handle_status(registry)

    if cmd == "cron":
        return _handle_cron(rest, agent_id, cron_store)

    if cmd == "sleep":
        return _handle_sleep(rest, agent_id)

    if cmd == "teach":
        return _handle_teach(rest, global_memory)

    if cmd == "memory":
        return await _handle_memory(agent_id, registry, global_memory)

    if cmd == "forget":
        return _handle_forget(rest, agent_id, registry)

    if cmd == "catchup":
        return "__CATCHUP__"

    if cmd == "incident" and incident_store is not None:
        return _handle_incident(rest, agent_id, incident_store, cron_store)

    if cmd == "resolve" and incident_store is not None:
        return _handle_resolve(rest, agent_id, incident_store, cron_store)

    # Unknown command — fall through to AI
    return None


def _handle_quick() -> Dict[str, Any]:
    """Handle /quick — returns a Cards v2 action card."""
    buttons = [
        ("\U0001f4cb Morning Briefing", "morning_briefing", "Give me my morning briefing"),
        ("\U0001f4dd Summarize Thread", "summarize_thread", "Summarize this thread"),
        ("\U0001f52e What's Next?", "whats_next", "What should I focus on next?"),
        ("\U0001f4da Teach Something", "teach_something", "Teach me something interesting"),
        ("\u2699\ufe0f Agent Status", "agent_status", "/status"),
    ]

    button_widgets = []
    for label, action_id, prompt_text in buttons:
        button_widgets.append({
            "button": {
                "text": label,
                "onClick": {
                    "action": {
                        "function": "quick_action",
                        "parameters": [
                            {"key": "action", "value": action_id},
                            {"key": "prompt", "value": prompt_text},
                        ],
                    }
                },
            }
        })

    cards_v2 = [
        {
            "cardId": "quick-actions",
            "card": {
                "header": {
                    "title": "Quick Actions",
                    "subtitle": "Tap a button to get started",
                },
                "sections": [
                    {
                        "widgets": [
                            {"buttonList": {"buttons": [b["button"] for b in button_widgets]}}
                        ]
                    }
                ],
            },
        }
    ]
    return {"cardsV2": cards_v2}


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


def _handle_teach(args: str, global_memory: Optional["GlobalMemoryManager"]) -> str:
    """Handle /teach command for global knowledge management."""
    if global_memory is None:
        return "\u26a0\ufe0f Knowledge system unavailable."

    args = args.strip()
    if not args:
        return (
            "*Usage:*\n"
            "\u2022 `/teach <fact>` \u2014 add to global knowledge\n"
            "\u2022 `/teach list` \u2014 show current knowledge entries\n"
            "\u2022 `/teach forget <keyword>` \u2014 remove a knowledge entry"
        )

    parts = args.split(None, 1)
    sub = parts[0].lower()

    if sub == "list":
        entries = global_memory.read_all_knowledge()
        if not entries:
            return "\U0001f4da No knowledge entries yet. Add one with `/teach <fact>`."
        lines = ["*\U0001f4da Global Knowledge:*"]
        for key, content in entries.items():
            # Show first 100 chars of content
            preview = content[:100] + ("..." if len(content) > 100 else "")
            lines.append(f"\u2022 `{key}`: {preview}")
        return "\n".join(lines)

    if sub == "forget":
        keyword = parts[1].strip() if len(parts) > 1 else ""
        if not keyword:
            return "Usage: `/teach forget <keyword>`"
        removed = global_memory.remove_knowledge(keyword)
        if removed == 0:
            return f"No knowledge entries matching `{keyword}` found."
        return f"\U0001f5d1 Removed {removed} knowledge entry{'s' if removed > 1 else ''} matching `{keyword}`."

    # Default: add knowledge
    # Use the full args as content, derive key from first few words
    words = args.split()
    key = "_".join(words[:5])
    global_memory.add_knowledge(key, args)
    return f"\u2705 Learned: _{args}_"


# ---------------------------------------------------------------------------
# /memory — Memory Inspector Card
# ---------------------------------------------------------------------------


async def _handle_memory(
    agent_id: str,
    registry: Optional["AgentRegistry"],
    global_memory: Optional["GlobalMemoryManager"],
) -> Union[str, Dict[str, Any]]:
    """Handle /memory — returns a Cards v2 memory inspector card."""
    if registry is None:
        return "\u26a0\ufe0f Memory inspector unavailable — registry not connected."

    from g3lobster.chat.memory_inspector import build_memory_card

    card_payload = await build_memory_card(
        agent_id=agent_id,
        user_id="unknown",  # slash commands don't carry user context
        registry=registry,
        global_memory=global_memory,
    )
    return card_payload


# ---------------------------------------------------------------------------
# /forget — Delete memory items
# ---------------------------------------------------------------------------


def _handle_forget(
    args: str,
    agent_id: str,
    registry: Optional["AgentRegistry"],
) -> str:
    """Handle /forget — remove specific memory items."""
    if registry is None:
        return "\u26a0\ufe0f Forget unavailable — registry not connected."

    args = args.strip()
    if not args:
        return (
            "*Usage:*\n"
            "\u2022 `/forget preference <number>` \u2014 remove a stored preference by index\n"
            "\u2022 `/forget procedure <title>` \u2014 remove a learned procedure by title keyword"
        )

    parts = args.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    runtime = registry.get_agent(agent_id)
    if not runtime:
        return f"\u26a0\ufe0f Agent `{agent_id}` is not running."

    mm = runtime.memory_manager

    if sub == "preference":
        return _forget_preference(mm, rest)

    if sub == "procedure":
        return _forget_procedure(mm, rest)

    return f"Unknown forget target: `{sub}`. Use `preference` or `procedure`."


def _forget_preference(mm: "MemoryManager", index_str: str) -> str:
    """Remove a tagged preference entry by 1-based index."""
    if not index_str:
        return "Usage: `/forget preference <number>`"
    try:
        index = int(index_str)
    except ValueError:
        return f"Invalid preference index: `{index_str}`. Must be a number."

    removed = mm.delete_tagged_memory("user preference", index - 1)
    if removed:
        return f"\U0001f5d1 Removed preference #{index}."
    return f"No preference found at index #{index}."


def _forget_procedure(mm: "MemoryManager", keyword: str) -> str:
    """Remove a procedure by title keyword."""
    if not keyword:
        return "Usage: `/forget procedure <title>`"

    keyword_lower = keyword.lower()
    procedures = mm.list_procedures()
    matched = [p for p in procedures if keyword_lower in p.title.lower()]

    if not matched:
        return f"No procedure matching `{keyword}` found."

    # Remove matching procedures by rewriting PROCEDURES.md without them
    remaining = [p for p in procedures if keyword_lower not in p.title.lower()]
    mm.procedure_store.save_procedures(remaining)

    count = len(matched)
    titles = ", ".join(p.title for p in matched[:3])
    return f"\U0001f5d1 Removed {count} procedure{'s' if count > 1 else ''}: {titles}"


# ---------------------------------------------------------------------------
# /status — Fleet Dashboard Card
# ---------------------------------------------------------------------------

_STATE_PILLS: Dict[str, str] = {
    "idle": "\U0001f7e2 idle",
    "busy": "\U0001f7e1 busy",
    "starting": "\U0001f7e1 starting",
    "stuck": "\U0001f534 stuck",
    "dead": "\U0001f534 dead",
    "stopped": "\u26aa stopped",
    "sleeping": "\u26aa sleeping",
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
    pill = _STATE_PILLS.get(state, f"\u26ab {state}")
    emoji = agent.get("emoji", "\U0001f916")
    name = agent.get("name", agent.get("id", "unknown"))
    uptime = _format_uptime(int(agent.get("uptime_s", 0)))
    task = agent.get("current_task") or "\u2014"
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
    bridge_label = "\U0001f7e2 running" if bridge_running else "\U0001f534 stopped"

    sections: List[Dict[str, Any]] = [
        {
            "header": f"Fleet: {active} active \u00b7 {stopped} stopped \u00b7 {len(agents)} total",
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
        return "\u26a0\ufe0f Fleet status unavailable \u2014 registry not connected."

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
        status = "\u2705" if t.enabled else "\u23f8"
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
        return f"\u2705 Cron task created: `{task.id[:8]}` | `{schedule}` | _{instruction}_"
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
        return f"Ambiguous ID prefix `{task_id_prefix}` \u2014 matches {len(matched)} tasks. Use more characters."
    deleted = cron_store.delete_task(agent_id, matched[0].id)
    if deleted:
        return f"\U0001f5d1 Deleted task `{matched[0].id[:8]}`."
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
        icon = "\u25b6" if enabled else "\u23f8"
        return f"{icon} Task `{matched[0].id[:8]}` {verb}."
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
