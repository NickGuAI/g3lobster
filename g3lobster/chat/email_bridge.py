"""Gmail polling bridge for g3lobster.

Users trigger agents by sending email to ``<prefix>+<agent_id>@<domain>``.
For example, if ``base_address = "helper@example.com"``, then an email sent to
``helper+robo@example.com`` is routed to the ``robo`` agent.

The bridge polls Gmail via the API, processes new messages, forwards the
subject + body as a prompt, and replies to the thread with the agent's response.

Setup
-----
1. Enable the Gmail API and create OAuth credentials (same Google project as Chat).
2. Set ``email.enabled: true`` and ``email.base_address: "helper@example.com"`` in ``config.yaml``.
3. On first run, an OAuth browser window opens to grant Gmail access.
   Credentials are cached in ``{auth_data_dir}/gmail_token.json``.
"""

from __future__ import annotations

import asyncio
import base64
import email as email_lib
import logging
import re
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)

# Matches the local-part "prefix+agent_id" from a recipient address.
_PLUS_RE = re.compile(r"^[^+]+\+([a-zA-Z0-9_-]+)@", re.IGNORECASE)


def _extract_agent_id(to_address: str) -> Optional[str]:
    """Return the agent ID encoded in a ``prefix+agent_id@domain`` address."""
    m = _PLUS_RE.match(to_address.strip())
    return m.group(1).lower() if m else None


def _get_gmail_service(auth_data_dir: Optional[str]):
    """Authenticate and return a Gmail API service object."""
    from google.auth.transport.requests import Request  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    from googleapiclient.discovery import build  # type: ignore

    scopes = ["https://www.googleapis.com/auth/gmail.modify"]
    token_path = Path(auth_data_dir or ".") / "gmail_token.json"
    creds_path = Path(auth_data_dir or ".") / "credentials.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


def _decode_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text
    return ""


def _get_header(headers: list, name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


class EmailBridge:
    """Polls Gmail for messages addressed to ``+<agent_id>@`` and routes them."""

    def __init__(
        self,
        registry,
        base_address: str,
        poll_interval_s: float = 30.0,
        auth_data_dir: Optional[str] = None,
        service=None,
    ) -> None:
        self.registry = registry
        self.base_address = base_address
        self.poll_interval_s = poll_interval_s
        self.auth_data_dir = auth_data_dir
        self.service = service

        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._seen_message_ids: Set[str] = set()

    async def start(self) -> None:
        if self.service is None:
            try:
                self.service = await asyncio.to_thread(_get_gmail_service, self.auth_data_dir)
            except Exception:
                logger.exception("EmailBridge: failed to authenticate with Gmail — email bridge disabled")
                return

        if self._poll_task and not self._poll_task.done():
            return

        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="g3lobster-email-poll")
        logger.info("EmailBridge started (base_address=%s)", self.base_address)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    @property
    def is_running(self) -> bool:
        return bool(self._poll_task and not self._poll_task.done())

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except Exception:
                logger.exception("EmailBridge poll error")
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(self.poll_interval_s)

    async def _poll_once(self) -> None:
        if not self.service:
            return

        # Search for unread messages sent to any +<agent> address at our domain
        domain = self.base_address.split("@", 1)[-1] if "@" in self.base_address else ""
        query = f"to:@{domain} is:unread" if domain else "is:unread"

        response = await asyncio.to_thread(
            self.service.users().messages().list(userId="me", q=query, maxResults=20).execute
        )
        messages = response.get("messages", [])
        for msg_stub in messages:
            msg_id = msg_stub["id"]
            if msg_id in self._seen_message_ids:
                continue
            self._seen_message_ids.add(msg_id)
            try:
                await self._process_message(msg_id)
            except Exception:
                logger.exception("EmailBridge: error processing message %s", msg_id)

    async def _process_message(self, msg_id: str) -> None:
        msg = await asyncio.to_thread(
            self.service.users().messages().get(userId="me", id=msg_id, format="full").execute
        )
        headers = msg.get("payload", {}).get("headers", [])
        to_header = _get_header(headers, "To")
        subject = _get_header(headers, "Subject") or "(no subject)"
        thread_id = msg.get("threadId")

        # Determine target agent from +address suffix
        agent_id = None
        for addr in to_header.replace(",", ";").split(";"):
            agent_id = _extract_agent_id(addr.strip())
            if agent_id:
                break

        if not agent_id:
            logger.debug("EmailBridge: no +agent_id in To: %r — skipping", to_header)
            return

        body = _decode_body(msg.get("payload", {})).strip()
        prompt = f"[Email: {subject}]\n\n{body}" if body else f"[Email: {subject}]"

        runtime = self.registry.get_agent(agent_id)
        if not runtime:
            started = await self.registry.start_agent(agent_id)
            if not started:
                logger.warning("EmailBridge: agent %r not found", agent_id)
                return
            runtime = self.registry.get_agent(agent_id)
            if not runtime:
                return

        from g3lobster.tasks.types import Task, TaskStatus

        session_id = f"email__{agent_id}"
        task = Task(prompt=prompt, session_id=session_id)
        result_task = await runtime.assign(task)

        if result_task.status == TaskStatus.COMPLETED and result_task.result:
            reply_body = result_task.result
        elif result_task.status == TaskStatus.FAILED:
            reply_body = f"Error: {result_task.error}"
        else:
            reply_body = "Task was canceled."

        await self._send_reply(msg_id, thread_id, subject, reply_body, headers)

        # Mark original as read
        try:
            await asyncio.to_thread(
                self.service.users().messages().modify(
                    userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
                ).execute
            )
        except Exception:
            logger.exception("EmailBridge: failed to mark message %s as read", msg_id)

    async def _send_reply(
        self,
        original_msg_id: str,
        thread_id: Optional[str],
        subject: str,
        body: str,
        original_headers: list,
    ) -> None:
        message_id_header = _get_header(original_headers, "Message-ID")
        from_header = _get_header(original_headers, "From")

        reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
        raw = (
            f"To: {from_header}\n"
            f"Subject: {reply_subject}\n"
            f"Content-Type: text/plain; charset=utf-8\n"
        )
        if message_id_header:
            raw += f"In-Reply-To: {message_id_header}\n"
            raw += f"References: {message_id_header}\n"
        raw += f"\n{body}"

        encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
        send_body: dict = {"raw": encoded}
        if thread_id:
            send_body["threadId"] = thread_id

        await asyncio.to_thread(
            self.service.users().messages().send(userId="me", body=send_body).execute
        )
        logger.info("EmailBridge: replied to message %s", original_msg_id)
