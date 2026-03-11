"""Gemini CLI output parsing utilities."""

from __future__ import annotations

import hashlib
import re


def clean_text(text: str, strip_markdown: bool = False) -> str:
    """Removes ANSI escape sequences, UI clutter, and optionally markdown."""
    if not text:
        return ""

    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][0-9;]*\x07", "", text)
    text = re.sub(r"\x1b\([AB012]", "", text)

    box_chars = r"[╭╮╯╰─│┌┐└┘├┤┬┴┼═║╒╓╔╕╖╗╘╙╚╛╜╝╞╟╠╡╢╣╤╥╦╧╨╩╪╫╬█■]"
    text = re.sub(box_chars, "", text)
    text = re.sub(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]", "", text)

    text = re.sub(
        r"You are currently in screen reader-friendly view.*?next run\.",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if strip_markdown:
        text = re.sub(r"[*_`]", "", text)

    ui_patterns = [
        r"Prioritizing.*Response",
        r"Clarifying.*Response",
        r".*context file.*YOLO mode",
        r"^\*\s+Type your message",
        r"~\s+no sandbox",
        r"gemini-.* /model",
    ]

    lines = []
    for line in text.splitlines():
        clean_line = line.strip()
        if not clean_line or clean_line == ">":
            continue
        if any(re.search(pattern, clean_line) for pattern in ui_patterns):
            continue
        lines.append(clean_line)
    return "\n".join(lines)


def split_reasoning(text: str) -> tuple[str, str]:
    """Split model output into (reasoning, response) around the ✦ separator.

    Returns a tuple of (reasoning_text, response_text).  If no separator
    is present, reasoning is empty and the full text is the response.
    """
    if not text:
        return ("", "")
    if "✦" in text:
        reasoning, response = text.split("✦", 1)
        return (reasoning.strip(), response.strip())
    return ("", text.strip())


def strip_reasoning(text: str) -> str:
    """Heuristic to strip reasoning/thinking from model responses."""
    _, response = split_reasoning(text)
    return response


def get_content_id(content: str) -> str:
    """Generates a stable, content-based ID for deduplication."""
    if not content:
        return "empty"
    return hashlib.md5(content.strip().encode("utf-8")).hexdigest()[:12]
