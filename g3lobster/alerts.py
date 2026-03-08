"""Proactive health alert manager."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @classmethod
    def from_str(cls, s: str) -> "AlertSeverity":
        return cls(s.lower()) if s.lower() in {m.value for m in cls} else cls.WARNING

    def __ge__(self, other):
        order = {self.WARNING: 0, self.ERROR: 1, self.CRITICAL: 2}
        return order[self] >= order[other]

    def __gt__(self, other):
        order = {self.WARNING: 0, self.ERROR: 1, self.CRITICAL: 2}
        return order[self] > order[other]

    def __le__(self, other):
        return not self.__gt__(other)

    def __lt__(self, other):
        return not self.__ge__(other)


@dataclass
class AlertEvent:
    event_type: str  # agent_dead, agent_stuck, agent_restarted, delegation_timeout, bridge_stopped
    agent_id: str
    detail: str
    severity: AlertSeverity
    timestamp: str  # ISO 8601


_SEVERITY_MAP: Dict[str, AlertSeverity] = {
    "agent_dead": AlertSeverity.CRITICAL,
    "agent_stuck": AlertSeverity.ERROR,
    "agent_restarted": AlertSeverity.WARNING,
    "delegation_timeout": AlertSeverity.ERROR,
    "bridge_stopped": AlertSeverity.CRITICAL,
}


class AlertManager:
    """Routes health alerts to configured sinks with rate limiting."""

    def __init__(
        self,
        enabled: bool = False,
        chat_space_id: str = "",
        webhook_url: str = "",
        email_address: str = "",
        min_severity: str = "warning",
        rate_limit_s: int = 300,
        chat_service: Any = None,
        email_bridge: Any = None,
        server_host: str = "0.0.0.0",
        server_port: int = 20001,
    ):
        self.enabled = enabled
        self.chat_space_id = chat_space_id
        self.webhook_url = webhook_url
        self.email_address = email_address
        self.min_severity = AlertSeverity.from_str(min_severity)
        self.rate_limit_s = rate_limit_s
        self.chat_service = chat_service
        self.email_bridge = email_bridge
        self._mgmt_url = f"http://{server_host}:{server_port}"
        self._last_alert: Dict[str, float] = {}  # key: "{event_type}:{agent_id}" -> timestamp

    def _rate_key(self, event: AlertEvent) -> str:
        return f"{event.event_type}:{event.agent_id}"

    def _is_rate_limited(self, event: AlertEvent) -> bool:
        key = self._rate_key(event)
        last = self._last_alert.get(key)
        if last is None:
            return False
        return (time.monotonic() - last) < self.rate_limit_s

    def _record_alert(self, event: AlertEvent) -> None:
        self._last_alert[self._rate_key(event)] = time.monotonic()

    async def send(self, event: AlertEvent) -> None:
        if not self.enabled:
            return
        if event.severity < self.min_severity:
            return
        if self._is_rate_limited(event):
            logger.debug("Alert rate-limited: %s for %s", event.event_type, event.agent_id)
            return

        self._record_alert(event)
        message = self._format_message(event)

        # Fire all sinks concurrently; failures don't propagate.
        tasks = []
        if self.chat_space_id and self.chat_service:
            tasks.append(self._send_chat(message))
        if self.webhook_url:
            tasks.append(self._send_webhook(event, message))
        if self.email_address and self.email_bridge:
            tasks.append(self._send_email(event, message))

        for coro in tasks:
            try:
                await coro
            except Exception:
                logger.exception("Alert delivery failed for %s", event.event_type)

    def _format_message(self, event: AlertEvent) -> str:
        icon = {"warning": "\u26a0\ufe0f", "error": "\U0001f534", "critical": "\U0001f6a8"}.get(event.severity.value, "\u2139\ufe0f")
        return (
            f"{icon} *g3lobster alert* \u2014 {event.event_type}\n"
            f"Agent: `{event.agent_id}`\n"
            f"Detail: {event.detail}\n"
            f"Time: {event.timestamp}\n"
            f"Dashboard: {self._mgmt_url}/api/agents"
        )

    async def _send_chat(self, message: str) -> None:
        body = {"text": message}
        await asyncio.to_thread(
            self.chat_service.spaces()
            .messages()
            .create(parent=self.chat_space_id, body=body)
            .execute
        )
        logger.info("Alert sent to Chat space %s", self.chat_space_id)

    async def _send_webhook(self, event: AlertEvent, message: str) -> None:
        payload = {
            "text": message,
            "event_type": event.event_type,
            "agent_id": event.agent_id,
            "detail": event.detail,
            "severity": event.severity.value,
            "timestamp": event.timestamp,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self.webhook_url, json=payload)
            resp.raise_for_status()
        logger.info("Alert sent to webhook %s", self.webhook_url)

    async def _send_email(self, event: AlertEvent, message: str) -> None:
        import base64

        subject = f"g3lobster alert: {event.event_type} — {event.agent_id}"
        raw = (
            f"To: {self.email_address}\n"
            f"Subject: {subject}\n"
            f"Content-Type: text/plain; charset=utf-8\n"
            f"\n{message}"
        )
        encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
        send_body: dict = {"raw": encoded}
        await asyncio.to_thread(
            self.email_bridge.service.users()
            .messages()
            .send(userId="me", body=send_body)
            .execute
        )
        logger.info("Alert sent via email to %s", self.email_address)


def make_event(event_type: str, agent_id: str, detail: str) -> AlertEvent:
    """Create an AlertEvent with auto-assigned severity and timestamp."""
    return AlertEvent(
        event_type=event_type,
        agent_id=agent_id,
        detail=detail,
        severity=_SEVERITY_MAP.get(event_type, AlertSeverity.WARNING),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
