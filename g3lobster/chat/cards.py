"""Google Chat Cards v2 builder helpers for the memory inspector."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_memory_inspector_card(
    agent_name: str,
    agent_emoji: str,
    preferences: List[str],
    procedures: List[Dict[str, Any]],
    daily_notes: List[str],
    stats: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build a Cards v2 payload for the memory inspector.

    Returns a list suitable for the ``cardsV2`` field of a Google Chat message.
    """
    sections: List[Dict[str, Any]] = []

    # Header section with agent stats
    sections.append(_build_stats_section(stats))

    # User preferences
    sections.append(_build_preferences_section(preferences))

    # Learned procedures
    sections.append(_build_procedures_section(procedures))

    # Recent daily notes
    sections.append(_build_daily_notes_section(daily_notes))

    return [
        {
            "cardId": "memory-inspector",
            "card": {
                "header": {
                    "title": f"{agent_emoji} {agent_name} — Memory Inspector",
                    "subtitle": "What I remember about you",
                    "imageUrl": "",
                    "imageType": "CIRCLE",
                },
                "sections": sections,
            },
        }
    ]


def build_forget_button(item_type: str, item_id: str) -> Dict[str, Any]:
    """Build a Cards v2 button widget for the "Forget" action.

    Returns a ``buttonList`` widget with an onClick action carrying
    ``item_type`` and ``item_id`` as action parameters.  If the Chat app
    does not support ``CARD_CLICKED`` callbacks (e.g. polling-based bridge),
    the button label doubles as a fallback hint showing the ``/forget``
    command the user can type manually.
    """
    command_hint = f"/forget {item_type} {item_id}"
    return {
        "buttonList": {
            "buttons": [
                {
                    "text": "Forget",
                    "onClick": {
                        "action": {
                            "function": "forget_memory_item",
                            "parameters": [
                                {"key": "item_type", "value": item_type},
                                {"key": "item_id", "value": str(item_id)},
                            ],
                        }
                    },
                }
            ]
        }
    }


def _build_stats_section(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Build the agent stats summary section."""
    sessions_total = stats.get("sessions_total", 0)
    messages_total = stats.get("messages_total", 0)
    memory_bytes = stats.get("memory_bytes", 0)
    daily_notes_count = stats.get("daily_notes_count", 0)

    memory_kb = round(memory_bytes / 1024, 1) if memory_bytes else 0

    return {
        "header": "Agent Stats",
        "widgets": [
            {
                "decoratedText": {
                    "topLabel": "Sessions",
                    "text": str(sessions_total),
                }
            },
            {
                "decoratedText": {
                    "topLabel": "Messages processed",
                    "text": str(messages_total),
                }
            },
            {
                "decoratedText": {
                    "topLabel": "Memory size",
                    "text": f"{memory_kb} KB",
                }
            },
            {
                "decoratedText": {
                    "topLabel": "Daily notes",
                    "text": str(daily_notes_count),
                }
            },
        ],
    }


def _build_preferences_section(preferences: List[str]) -> Dict[str, Any]:
    """Build the user preferences section."""
    if not preferences:
        return {
            "header": "User Preferences",
            "widgets": [
                {"decoratedText": {"text": "<i>No preferences stored yet.</i>"}}
            ],
        }

    widgets: List[Dict[str, Any]] = []
    for i, pref in enumerate(preferences[:10]):
        text = pref.strip()
        if len(text) > 200:
            text = text[:197] + "..."
        widget: Dict[str, Any] = {
            "decoratedText": {
                "text": text,
                "bottomLabel": f"/forget preference {i}",
            }
        }
        widgets.append(widget)
        widgets.append(build_forget_button("preference", str(i)))

    return {
        "header": f"User Preferences ({len(preferences)})",
        "widgets": widgets,
    }


def _build_procedures_section(procedures: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the learned procedures section."""
    if not procedures:
        return {
            "header": "Learned Procedures",
            "widgets": [
                {"decoratedText": {"text": "<i>No procedures learned yet.</i>"}}
            ],
        }

    widgets: List[Dict[str, Any]] = []
    for proc in procedures[:10]:
        title = proc.get("title", "Untitled")
        weight = proc.get("weight", 0)
        status = proc.get("status", "candidate")
        steps_count = proc.get("steps_count", 0)

        status_icon = "🟢" if status == "permanent" else "🟡" if status == "usable" else "⚪"
        widget: Dict[str, Any] = {
            "decoratedText": {
                "topLabel": f"{status_icon} {status} · weight {weight:.1f}",
                "text": f"<b>{title}</b>",
                "bottomLabel": f"{steps_count} steps · /forget procedure {title}",
            }
        }
        widgets.append(widget)
        widgets.append(build_forget_button("procedure", title))

    return {
        "header": f"Learned Procedures ({len(procedures)})",
        "widgets": widgets,
    }


def _build_daily_notes_section(daily_notes: List[str]) -> Dict[str, Any]:
    """Build the recent daily notes section (last 5)."""
    if not daily_notes:
        return {
            "header": "Recent Context",
            "widgets": [
                {"decoratedText": {"text": "<i>No daily notes yet.</i>"}}
            ],
        }

    widgets: List[Dict[str, Any]] = []
    for note in daily_notes[:5]:
        text = note.strip()
        if len(text) > 300:
            text = text[:297] + "..."
        widgets.append({"decoratedText": {"text": text}})

    return {
        "header": f"Recent Context (last {len(daily_notes[:5])} notes)",
        "widgets": widgets,
    }
