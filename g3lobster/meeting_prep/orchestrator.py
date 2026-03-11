"""Meeting prep orchestrator.

Given an upcoming meeting, queries Gmail for recent threads with attendees,
searches agent memory for relevant context, and synthesizes a structured
briefing message.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from g3lobster.chat.calendar_bridge import MeetingInfo
    from g3lobster.chat.email_bridge import EmailBridge
    from g3lobster.memory.search import MemorySearchEngine

logger = logging.getLogger(__name__)

# Maximum number of Gmail threads to include per attendee
_MAX_THREADS_PER_ATTENDEE = 3
# Maximum number of memory hits to include
_MAX_MEMORY_HITS = 10


class MeetingPrepOrchestrator:
    """Builds meeting briefing packets from Gmail + memory context."""

    def __init__(
        self,
        memory_search: Optional["MemorySearchEngine"] = None,
        email_service=None,
        gmail_user_id: str = "me",
    ) -> None:
        self.memory_search = memory_search
        self.email_service = email_service
        self.gmail_user_id = gmail_user_id

    async def prepare(self, meeting: "MeetingInfo", agent_id: str = "") -> str:
        """Build a structured briefing for the given meeting.

        Returns a formatted markdown string suitable for delivery via Chat DM.
        """
        sections: List[str] = []

        # Header
        start_str = meeting.start_time.strftime("%H:%M %Z") if meeting.start_time else ""
        sections.append(f"## Meeting Briefing: {meeting.title}")
        sections.append(f"**Time:** {start_str}")
        if meeting.attendees:
            sections.append(f"**Attendees:** {', '.join(meeting.attendees)}")
        if meeting.meet_link:
            sections.append(f"**Meet link:** {meeting.meet_link}")
        if meeting.doc_links:
            sections.append("**Linked docs:**")
            for link in meeting.doc_links:
                sections.append(f"  - {link}")

        # Meeting description
        if meeting.description:
            sections.append("\n### Agenda / Description")
            sections.append(meeting.description[:2000])

        # Gmail context
        email_context = await self._query_gmail(meeting)
        if email_context:
            sections.append("\n### Recent Email Threads with Attendees")
            sections.append(email_context)

        # Memory context
        memory_context = self._query_memory(meeting, agent_id)
        if memory_context:
            sections.append("\n### Relevant Context from Memory")
            sections.append(memory_context)

        return "\n".join(sections)

    async def _query_gmail(self, meeting: "MeetingInfo") -> str:
        """Query Gmail for recent threads with meeting attendees."""
        if not self.email_service or not meeting.attendees:
            return ""

        import asyncio

        summaries: List[str] = []
        for attendee in meeting.attendees[:10]:  # Cap attendees to query
            try:
                query = f"from:{attendee} OR to:{attendee} newer_than:7d"
                response = await asyncio.to_thread(
                    self.email_service.users()
                    .messages()
                    .list(
                        userId=self.gmail_user_id,
                        q=query,
                        maxResults=_MAX_THREADS_PER_ATTENDEE,
                    )
                    .execute
                )
                messages = response.get("messages", [])
                for msg_stub in messages:
                    msg = await asyncio.to_thread(
                        self.email_service.users()
                        .messages()
                        .get(
                            userId=self.gmail_user_id,
                            id=msg_stub["id"],
                            format="metadata",
                            metadataHeaders=["Subject", "From", "Date"],
                        )
                        .execute
                    )
                    headers = msg.get("payload", {}).get("headers", [])
                    subject = next(
                        (h["value"] for h in headers if h["name"] == "Subject"), "(no subject)"
                    )
                    from_addr = next(
                        (h["value"] for h in headers if h["name"] == "From"), ""
                    )
                    summaries.append(f"- **{subject}** (from: {from_addr})")
            except Exception:
                logger.debug("Failed to query Gmail for %s", attendee, exc_info=True)

        return "\n".join(summaries) if summaries else ""

    def _query_memory(self, meeting: "MeetingInfo", agent_id: str) -> str:
        """Search agent memory for context related to attendees and topic."""
        if not self.memory_search:
            return ""

        # Build search queries from meeting title + attendee names
        queries = [meeting.title]
        for attendee in meeting.attendees[:5]:
            # Extract name part from email (before @)
            name_part = attendee.split("@")[0].replace(".", " ")
            queries.append(name_part)

        all_hits: List[str] = []
        seen_snippets: set = set()
        for query in queries:
            if not query.strip():
                continue
            agent_ids = [agent_id] if agent_id else None
            hits = self.memory_search.search(
                query,
                agent_ids=agent_ids,
                limit=5,
            )
            for hit in hits:
                snippet_key = hit.snippet[:100]
                if snippet_key in seen_snippets:
                    continue
                seen_snippets.add(snippet_key)
                all_hits.append(f"- [{hit.memory_type}] {hit.snippet[:300]}")
                if len(all_hits) >= _MAX_MEMORY_HITS:
                    break
            if len(all_hits) >= _MAX_MEMORY_HITS:
                break

        return "\n".join(all_hits) if all_hits else ""
