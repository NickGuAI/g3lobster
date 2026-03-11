"""Thread summarization with institutional context cross-referencing."""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from g3lobster.memory.search import MemorySearchEngine

logger = logging.getLogger(__name__)

# Natural language patterns for "catch me up" intent
CATCHUP_INTENT_RE = re.compile(
    r"catch\s+me\s+up|summarize\s+(?:this\s+)?thread|what\s+did\s+I\s+miss|"
    r"tldr|tl;dr|what(?:'s| is| has)\s+(?:been\s+)?happening",
    re.IGNORECASE,
)

# Max messages to include in a single summarization prompt to avoid exceeding context limits
_MAX_PROMPT_MESSAGES = 80
_CHUNK_SIZE = 40


def detect_catchup_intent(text: str) -> bool:
    """Return True if text expresses a 'catch me up' intent."""
    return bool(CATCHUP_INTENT_RE.search(text))


def fetch_thread_messages(
    service: Any,
    space_id: str,
    thread_id: str,
    page_size: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch all messages in a thread from the Google Chat API.

    Paginates through results to get the full thread history.
    """
    all_messages: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    while True:
        kwargs: Dict[str, Any] = {
            "parent": space_id,
            "pageSize": page_size,
            "filter": f'thread.name = "{thread_id}"',
        }
        if page_token:
            kwargs["pageToken"] = page_token

        response = service.spaces().messages().list(**kwargs).execute()
        messages = response.get("messages", [])
        all_messages.extend(messages)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # Sort by createTime ascending
    all_messages.sort(key=lambda m: m.get("createTime", ""))
    return all_messages


def extract_participants(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract unique participants from thread messages.

    Returns a list of dicts with 'name' and 'display_name' keys.
    """
    seen: Dict[str, Dict[str, str]] = {}
    for msg in messages:
        sender = msg.get("sender", {})
        sender_name = sender.get("name", "")
        display_name = sender.get("displayName", "")
        if sender_name and sender_name not in seen:
            seen[sender_name] = {
                "name": sender_name,
                "display_name": display_name,
                "type": sender.get("type", "UNKNOWN"),
            }
    return list(seen.values())


def search_related_memory(
    search_engine: "MemorySearchEngine",
    participants: List[Dict[str, str]],
    topics: List[str],
    agent_id: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, str]]:
    """Search agent memory for discussions related to participants and topics."""
    hits: List[Dict[str, str]] = []
    seen_snippets: set = set()

    # Build search queries from topics and participant names
    queries: List[str] = list(topics)
    for p in participants:
        display = p.get("display_name", "")
        if display and display not in queries:
            queries.append(display)

    agent_ids = [agent_id] if agent_id else None

    for query in queries[:5]:  # Limit to 5 queries to avoid excessive searching
        try:
            results = search_engine.search(
                query,
                agent_ids=agent_ids,
                limit=limit,
            )
            for hit in results:
                snippet_key = hit.snippet[:100]
                if snippet_key not in seen_snippets:
                    seen_snippets.add(snippet_key)
                    hits.append({
                        "source": hit.source,
                        "snippet": hit.snippet,
                        "memory_type": hit.memory_type,
                    })
        except Exception:
            logger.debug("Memory search failed for query %r", query, exc_info=True)

    return hits[:limit]


def search_related_emails(
    gmail_service: Any,
    participants: List[Dict[str, str]],
    limit: int = 5,
) -> List[Dict[str, str]]:
    """Search Gmail for recent threads involving the same participants.

    Only called when an active Gmail service is available.
    """
    hits: List[Dict[str, str]] = []

    # Build a Gmail search query from participant display names
    human_participants = [
        p["display_name"]
        for p in participants
        if p.get("type") == "HUMAN" and p.get("display_name")
    ]
    if not human_participants:
        return hits

    # Search for emails from/to participants (limit to first 3)
    query_parts = [f"from:{name}" for name in human_participants[:3]]
    query = " OR ".join(query_parts)

    try:
        response = gmail_service.users().messages().list(
            userId="me", q=query, maxResults=limit,
        ).execute()
        for msg_stub in response.get("messages", []):
            try:
                msg = gmail_service.users().messages().get(
                    userId="me", id=msg_stub["id"], format="metadata",
                    metadataHeaders=["Subject", "From", "Date"],
                ).execute()
                headers = msg.get("payload", {}).get("headers", [])
                subject = ""
                sender = ""
                for h in headers:
                    if h.get("name", "").lower() == "subject":
                        subject = h.get("value", "")
                    elif h.get("name", "").lower() == "from":
                        sender = h.get("value", "")
                if subject:
                    hits.append({
                        "subject": subject,
                        "from": sender,
                        "snippet": msg.get("snippet", ""),
                    })
            except Exception:
                logger.debug("Failed to fetch email %s", msg_stub["id"], exc_info=True)
    except Exception:
        logger.debug("Gmail search failed", exc_info=True)

    return hits


def _extract_topics(messages: List[Dict[str, Any]]) -> List[str]:
    """Extract key topics from thread messages for memory cross-referencing."""
    # Collect text from all messages, take first few words of each as topic hints
    text_parts: List[str] = []
    for msg in messages[:20]:  # Sample first 20 messages
        text = (msg.get("text") or "").strip()
        if text:
            text_parts.append(text)

    combined = " ".join(text_parts)
    # Extract capitalized phrases and repeated nouns as rough topic extraction
    # Simple heuristic: words that appear multiple times
    words = re.findall(r"\b[A-Za-z]{3,}\b", combined.lower())
    word_counts: Dict[str, int] = {}
    for w in words:
        word_counts[w] = word_counts.get(w, 0) + 1

    # Return top frequent words (excluding common stop words)
    stop_words = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "had", "her", "was", "one", "our", "out", "has", "have", "been",
        "this", "that", "with", "they", "from", "will", "what", "when",
        "where", "which", "their", "there", "would", "could", "should",
        "about", "just", "like", "also", "into", "than", "then", "them",
        "some", "very", "does", "did", "how", "its", "let", "may",
    }
    topics = sorted(
        ((w, c) for w, c in word_counts.items() if w not in stop_words and c >= 2),
        key=lambda x: x[1],
        reverse=True,
    )
    return [w for w, _ in topics[:10]]


def _format_thread_for_prompt(messages: List[Dict[str, Any]]) -> str:
    """Format thread messages into a compact transcript for the summarization prompt."""
    lines: List[str] = []
    for msg in messages:
        sender = msg.get("sender", {})
        display = sender.get("displayName", sender.get("name", "unknown"))
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        create_time = (msg.get("createTime") or "")[:19].replace("T", " ")
        # Truncate very long messages
        if len(text) > 500:
            text = text[:497] + "..."
        lines.append(f"[{create_time}] {display}: {text}")
    return "\n".join(lines)


def _build_summary_prompt(
    thread_text: str,
    memory_hits: List[Dict[str, str]],
    email_hits: List[Dict[str, str]],
) -> str:
    """Build the Gemini summarization prompt with cross-referenced context."""
    parts: List[str] = [
        "Summarize this Google Chat thread. Produce a structured summary with these sections:",
        "- **Key Points**: The main topics and information discussed",
        "- **Decisions**: Any decisions made or conclusions reached",
        "- **Action Items**: Tasks assigned or next steps mentioned",
        "- **Related Context**: Connections to past discussions or email threads (if any)",
        "",
        "Be concise. Use bullet points. Focus on what matters.",
        "",
        "## Thread Transcript",
        thread_text,
    ]

    if memory_hits:
        parts.append("")
        parts.append("## Related Agent Memory")
        for hit in memory_hits[:5]:
            parts.append(f"- [{hit.get('memory_type', 'memory')}] {hit['snippet'][:200]}")

    if email_hits:
        parts.append("")
        parts.append("## Related Email Threads")
        for hit in email_hits[:3]:
            parts.append(f"- Subject: {hit.get('subject', 'N/A')} (from: {hit.get('from', 'N/A')})")
            snippet = hit.get("snippet", "")
            if snippet:
                parts.append(f"  Preview: {snippet[:150]}")

    return "\n".join(parts)


def _call_gemini(
    prompt: str,
    gemini_command: str = "gemini",
    gemini_args: Optional[List[str]] = None,
    timeout_s: float = 60.0,
    cwd: Optional[str] = None,
) -> str:
    """Call Gemini CLI with the given prompt and return the response."""
    args = gemini_args if gemini_args is not None else ["-y"]
    command = [gemini_command, *args, "-p", prompt]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        cwd=cwd,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Gemini CLI exited with code {result.returncode}: {stderr}")
    return (result.stdout or "").strip()


def _chunk_messages(messages: List[Dict[str, Any]], chunk_size: int = _CHUNK_SIZE) -> List[List[Dict[str, Any]]]:
    """Split messages into chunks for processing large threads."""
    chunks: List[List[Dict[str, Any]]] = []
    for i in range(0, len(messages), chunk_size):
        chunks.append(messages[i : i + chunk_size])
    return chunks


class ThreadSummarizer:
    """Orchestrates thread summarization with institutional context."""

    def __init__(
        self,
        service: Any,
        search_engine: Optional["MemorySearchEngine"] = None,
        gmail_service: Any = None,
        gemini_command: str = "gemini",
        gemini_args: Optional[List[str]] = None,
        gemini_timeout_s: float = 60.0,
        gemini_cwd: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        self.service = service
        self.search_engine = search_engine
        self.gmail_service = gmail_service
        self.gemini_command = gemini_command
        self.gemini_args = gemini_args
        self.gemini_timeout_s = gemini_timeout_s
        self.gemini_cwd = gemini_cwd
        self.agent_id = agent_id

    def summarize(
        self,
        space_id: str,
        thread_id: str,
    ) -> str:
        """Fetch thread, cross-reference context, and return structured summary."""
        # 1. Fetch thread messages
        messages = fetch_thread_messages(self.service, space_id, thread_id)
        if not messages:
            return "No messages found in this thread."

        # 2. Extract participants and topics
        participants = extract_participants(messages)
        topics = _extract_topics(messages)

        # 3. Cross-reference memory
        memory_hits: List[Dict[str, str]] = []
        if self.search_engine and topics:
            memory_hits = search_related_memory(
                self.search_engine,
                participants,
                topics,
                agent_id=self.agent_id,
            )

        # 4. Cross-reference email (optional)
        email_hits: List[Dict[str, str]] = []
        if self.gmail_service and participants:
            email_hits = search_related_emails(self.gmail_service, participants)

        # 5. Build prompt and summarize
        if len(messages) <= _MAX_PROMPT_MESSAGES:
            thread_text = _format_thread_for_prompt(messages)
            prompt = _build_summary_prompt(thread_text, memory_hits, email_hits)
            return _call_gemini(
                prompt,
                gemini_command=self.gemini_command,
                gemini_args=self.gemini_args,
                timeout_s=self.gemini_timeout_s,
                cwd=self.gemini_cwd,
            )

        # 6. Chunked summarization for long threads
        chunks = _chunk_messages(messages)
        chunk_summaries: List[str] = []
        for i, chunk in enumerate(chunks):
            chunk_text = _format_thread_for_prompt(chunk)
            chunk_prompt = (
                f"Summarize this part ({i + 1}/{len(chunks)}) of a Google Chat thread. "
                "Return key points, decisions, and action items as bullet points.\n\n"
                f"{chunk_text}"
            )
            try:
                summary = _call_gemini(
                    chunk_prompt,
                    gemini_command=self.gemini_command,
                    gemini_args=self.gemini_args,
                    timeout_s=self.gemini_timeout_s,
                    cwd=self.gemini_cwd,
                )
                chunk_summaries.append(summary)
            except Exception:
                logger.warning("Chunk %d/%d summarization failed", i + 1, len(chunks), exc_info=True)
                chunk_summaries.append(f"(chunk {i + 1} summarization failed)")

        # Final merge pass
        merge_prompt = _build_summary_prompt(
            "\n\n---\n\n".join(
                f"## Part {i + 1}\n{s}" for i, s in enumerate(chunk_summaries)
            ),
            memory_hits,
            email_hits,
        )
        return _call_gemini(
            merge_prompt,
            gemini_command=self.gemini_command,
            gemini_args=self.gemini_args,
            timeout_s=self.gemini_timeout_s,
            cwd=self.gemini_cwd,
        )
