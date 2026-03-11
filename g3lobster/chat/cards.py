"""Google Chat Cards v2 builder helpers for memory inspector."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_memory_inspector_card(
    agent_name: str,
    agent_emoji: str,
    preferences: List[str],
    procedures: List[Dict[str, Any]],
    daily_notes: List[str],
    stats: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a Cards v2 payload for the memory inspector.

    Returns a dict suitable for the Google Chat ``cardsV2`` message field.
    """
    sections: List[Dict[str, Any]] = []

    # --- User Preferences ---
    if preferences:
        pref_widgets = []
        for idx, pref in enumerate(preferences[:10]):
            pref_widgets.append(
                _decorated_text(
                    top_label=f"Preference #{idx + 1}",
                    text=_truncate(pref, 200),
                    button=_forget_button("preference", str(idx)),
                )
            )
        sections.append(_section("User Preferences", pref_widgets))
    else:
        sections.append(
            _section("User Preferences", [_decorated_text(text="_No preferences stored._")])
        )

    # --- Learned Procedures ---
    if procedures:
        proc_widgets = []
        for proc in procedures[:10]:
            label = f"Weight: {proc.get('weight', 0):.1f} | Status: {proc.get('status', 'unknown')}"
            steps_preview = "; ".join(proc.get("steps", [])[:3])
            proc_widgets.append(
                _decorated_text(
                    top_label=label,
                    text=f"*{proc.get('title', 'Untitled')}*\n{_truncate(steps_preview, 150)}",
                    button=_forget_button("procedure", proc.get("title", "")),
                )
            )
        sections.append(_section("Learned Procedures", proc_widgets))
    else:
        sections.append(
            _section("Learned Procedures", [_decorated_text(text="_No procedures learned yet._")])
        )

    # --- Recent Context (daily notes) ---
    if daily_notes:
        note_widgets = []
        for note in daily_notes[:5]:
            note_widgets.append(_decorated_text(text=_truncate(note, 200)))
        sections.append(_section("Recent Context", note_widgets))
    else:
        sections.append(
            _section("Recent Context", [_decorated_text(text="_No daily notes yet._")])
        )

    # --- Agent Stats ---
    stats_text = (
        f"Sessions: {stats.get('total_sessions', 0)}\n"
        f"Messages: {stats.get('total_messages', 0)}\n"
        f"Memory size: {stats.get('memory_bytes', 0)} bytes\n"
        f"Daily notes: {stats.get('daily_notes', 0)}\n"
        f"Procedures: {stats.get('procedures_count', 0)}"
    )
    sections.append(_section("Agent Stats", [_decorated_text(text=stats_text)]))

    card: Dict[str, Any] = {
        "cardId": "memory-inspector",
        "card": {
            "header": {
                "title": f"{agent_emoji} {agent_name} — Memory Inspector",
                "subtitle": "What I remember about you",
            },
            "sections": sections,
        },
    }

    return {"cardsV2": [card]}


def build_memory_inspector_text(
    agent_name: str,
    agent_emoji: str,
    preferences: List[str],
    procedures: List[Dict[str, Any]],
    daily_notes: List[str],
    stats: Dict[str, Any],
) -> str:
    """Build a plain-text fallback of the memory inspector for environments
    that may not support Cards v2."""
    lines = [f"*{agent_emoji} {agent_name} — Memory Inspector*", ""]

    lines.append("*User Preferences:*")
    if preferences:
        for idx, pref in enumerate(preferences[:10], 1):
            lines.append(f"  {idx}. {_truncate(pref, 200)}")
    else:
        lines.append("  _No preferences stored._")

    lines.append("")
    lines.append("*Learned Procedures:*")
    if procedures:
        for proc in procedures[:10]:
            title = proc.get("title", "Untitled")
            weight = proc.get("weight", 0)
            status = proc.get("status", "unknown")
            lines.append(f"  - *{title}* (weight: {weight:.1f}, {status})")
    else:
        lines.append("  _No procedures learned yet._")

    lines.append("")
    lines.append("*Recent Context:*")
    if daily_notes:
        for note in daily_notes[:5]:
            lines.append(f"  - {_truncate(note, 200)}")
    else:
        lines.append("  _No daily notes yet._")

    lines.append("")
    lines.append("*Agent Stats:*")
    lines.append(f"  Sessions: {stats.get('total_sessions', 0)}")
    lines.append(f"  Messages: {stats.get('total_messages', 0)}")
    lines.append(f"  Memory size: {stats.get('memory_bytes', 0)} bytes")
    lines.append(f"  Daily notes: {stats.get('daily_notes', 0)}")
    lines.append(f"  Procedures: {stats.get('procedures_count', 0)}")

    lines.append("")
    lines.append("_Use `/forget <type> <id>` to remove specific memories._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _section(header: str, widgets: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "header": header,
        "collapsible": len(widgets) > 3,
        "widgets": widgets,
    }


def _decorated_text(
    text: str,
    top_label: Optional[str] = None,
    button: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    widget: Dict[str, Any] = {"text": text}
    if top_label:
        widget["topLabel"] = top_label
    dt: Dict[str, Any] = {"decoratedText": widget}
    if button:
        widget["button"] = button
    return dt


def _forget_button(item_type: str, item_id: str) -> Dict[str, Any]:
    """Build a button that triggers a /forget command.

    Google Chat CARD_CLICKED events require the app to handle interactive
    callbacks.  Since the polling bridge may not support this, the button
    text includes a /forget command hint the user can copy-paste.
    """
    return {
        "text": "Forget",
        "onClick": {
            "action": {
                "function": "forget_memory",
                "parameters": [
                    {"key": "type", "value": item_type},
                    {"key": "id", "value": item_id},
                ],
            }
        },
    }


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
