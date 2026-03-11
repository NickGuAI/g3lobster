"""Cards v2 builder helpers for Google Chat."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_forget_button(item_type: str, item_id: str) -> Dict[str, Any]:
    """Build a Cards v2 button widget that triggers a /forget command.

    Since the polling-based bridge may not support CARD_CLICKED events,
    the button text instructs the user to use the ``/forget`` slash command.
    """
    return {
        "button": {
            "text": "\U0001f5d1 Forget",
            "onClick": {
                "action": {
                    "function": "memory_forget",
                    "parameters": [
                        {"key": "type", "value": item_type},
                        {"key": "id", "value": item_id},
                    ],
                }
            },
        }
    }


def _build_header(agent_name: str, agent_emoji: str) -> Dict[str, Any]:
    return {
        "title": f"{agent_emoji} Memory Inspector",
        "subtitle": f"What {agent_name} remembers about you",
    }


def _build_preferences_section(preferences: List[str]) -> Dict[str, Any]:
    if not preferences:
        widgets = [{"decoratedText": {"text": "_No preferences stored yet._"}}]
    else:
        widgets = []
        for i, pref in enumerate(preferences[:10]):
            preview = pref[:200] + ("..." if len(pref) > 200 else "")
            widget: Dict[str, Any] = {
                "decoratedText": {
                    "topLabel": f"Preference #{i + 1}",
                    "text": preview,
                }
            }
            widgets.append(widget)

    return {
        "header": "\U0001f3af User Preferences",
        "widgets": widgets,
    }


def _build_procedures_section(procedures: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not procedures:
        widgets = [{"decoratedText": {"text": "_No procedures learned yet._"}}]
    else:
        widgets = []
        for proc in procedures[:10]:
            title = proc.get("title", "Untitled")
            weight = proc.get("weight", 0)
            status = proc.get("status", "candidate")
            steps_count = len(proc.get("steps", []))
            pill = {
                "permanent": "\U0001f7e2",
                "usable": "\U0001f7e1",
                "candidate": "\u26aa",
            }.get(status, "\u26ab")

            widgets.append({
                "decoratedText": {
                    "topLabel": f"{pill} {title}",
                    "text": f"Weight: {weight:.1f} \u00b7 Status: {status} \u00b7 {steps_count} steps",
                }
            })

    return {
        "header": "\U0001f4d6 Learned Procedures",
        "widgets": widgets,
    }


def _build_daily_notes_section(daily_notes: List[str]) -> Dict[str, Any]:
    if not daily_notes:
        widgets = [{"decoratedText": {"text": "_No recent daily notes._"}}]
    else:
        widgets = []
        for note in daily_notes[:5]:
            preview = note[:200] + ("..." if len(note) > 200 else "")
            widgets.append({"decoratedText": {"text": preview}})

    return {
        "header": "\U0001f4c5 Recent Context",
        "widgets": widgets,
    }


def _build_stats_section(stats: Dict[str, Any]) -> Dict[str, Any]:
    total_sessions = stats.get("total_sessions", 0)
    total_messages = stats.get("total_messages", 0)
    memory_bytes = stats.get("memory_bytes", 0)
    procedures_count = stats.get("procedures_count", 0)
    daily_notes_count = stats.get("daily_notes_count", 0)

    memory_kb = memory_bytes / 1024 if memory_bytes else 0

    return {
        "header": "\U0001f4ca Agent Stats",
        "widgets": [
            {
                "decoratedText": {
                    "text": (
                        f"Sessions: <b>{total_sessions}</b> &nbsp;\u00b7&nbsp; "
                        f"Messages: <b>{total_messages}</b> &nbsp;\u00b7&nbsp; "
                        f"Memory: <b>{memory_kb:.1f} KB</b>"
                    ),
                }
            },
            {
                "decoratedText": {
                    "text": (
                        f"Procedures: <b>{procedures_count}</b> &nbsp;\u00b7&nbsp; "
                        f"Daily notes: <b>{daily_notes_count}</b>"
                    ),
                }
            },
        ],
    }


def _build_forget_hint_section() -> Dict[str, Any]:
    return {
        "widgets": [
            {
                "decoratedText": {
                    "topLabel": "Manage Memory",
                    "text": (
                        "Use <b>/forget preference &lt;number&gt;</b> to remove a preference\n"
                        "Use <b>/forget procedure &lt;title&gt;</b> to remove a procedure"
                    ),
                }
            }
        ],
    }


def build_memory_inspector_card(
    *,
    agent_name: str = "Agent",
    agent_emoji: str = "\U0001f916",
    preferences: Optional[List[str]] = None,
    procedures: Optional[List[Dict[str, Any]]] = None,
    daily_notes: Optional[List[str]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build a complete Cards v2 payload for the memory inspector."""
    sections = [
        _build_preferences_section(preferences or []),
        _build_procedures_section(procedures or []),
        _build_daily_notes_section(daily_notes or []),
        _build_stats_section(stats or {}),
        _build_forget_hint_section(),
    ]

    return [
        {
            "cardId": "memory-inspector",
            "card": {
                "header": _build_header(agent_name, agent_emoji),
                "sections": sections,
            },
        }
    ]
