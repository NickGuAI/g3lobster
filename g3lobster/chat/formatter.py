"""Convert standard markdown to Google Chat markup."""

from __future__ import annotations

import re


def format_for_google_chat(text: str) -> str:
    """Convert standard markdown to Google Chat's non-standard markup.

    Google Chat uses:
    - ``*text*`` for bold (not ``**text**``)
    - ``~text~`` for strikethrough (not ``~~text~~``)
    - ``<url|text>`` for links (not ``[text](url)``)
    - No ``#`` headers — they render literally

    Fenced code blocks (``` ... ```) are preserved without conversion.
    """
    parts: list[str] = []
    # Split on fenced code blocks to avoid converting inside them.
    segments = re.split(r"(```[\s\S]*?```)", text)

    for i, segment in enumerate(segments):
        if i % 2 == 1:
            # Inside a fenced code block — keep as-is.
            parts.append(segment)
            continue

        # Convert **bold** to *bold*
        segment = re.sub(r"\*\*(.+?)\*\*", r"*\1*", segment)

        # Convert ~~strike~~ to ~strike~
        segment = re.sub(r"~~(.+?)~~", r"~\1~", segment)

        # Convert [text](url) to <url|text>
        segment = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", segment)

        # Strip headers (# Header -> *Header*)
        segment = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", segment, flags=re.MULTILINE)

        parts.append(segment)

    return "".join(parts)
