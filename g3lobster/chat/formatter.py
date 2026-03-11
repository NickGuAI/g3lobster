"""Convert a subset of Markdown formatting to Google Chat markup.

Supported conversions (outside fenced code blocks):
  **bold**       -> *bold*
  ~~strike~~     -> ~strike~
  [text](url)    -> <url|text>
  # Header       -> *Header*   (levels 1-6)
"""

import re

# Pre-compiled patterns used inside _convert_segment.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def _convert_segment(text: str) -> str:
    """Apply all Markdown-to-Google-Chat conversions to a non-code segment."""
    text = _BOLD_RE.sub(r"*\1*", text)
    text = _STRIKE_RE.sub(r"~\1~", text)
    text = _LINK_RE.sub(r"<\2|\1>", text)
    text = _HEADER_RE.sub(r"*\1*", text)
    return text


def format_for_google_chat(text: str) -> str:
    """Convert Markdown formatting to Google Chat markup.

    Content inside fenced code blocks (delimited by triple back-ticks) is
    preserved verbatim.  All other text is converted according to the rules
    documented in the module docstring.

    Parameters
    ----------
    text:
        The Markdown-formatted input string.

    Returns
    -------
    str
        The string with Google Chat compatible markup.
    """
    if not text:
        return text

    # Split on fenced code block boundaries.  The regex captures the
    # delimiter so that ``re.split`` includes the fence markers as separate
    # list items we can use to track whether we are inside a code block.
    parts = re.split(r"(```)", text)

    result: list[str] = []
    inside_code = False
    for part in parts:
        if part == "```":
            inside_code = not inside_code
            result.append(part)
        elif inside_code:
            result.append(part)
        else:
            result.append(_convert_segment(part))

    return "".join(result)
