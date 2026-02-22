"""Structured agent event system."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, DefaultDict, Dict, List, Optional

logger = logging.getLogger(__name__)

EventListener = Callable[["AgentEvent"], None]


@dataclass
class AgentEvent:
    event_id: str
    run_id: str
    agent_id: str
    seq: int
    stream: str
    event_type: str
    ts: float
    data: dict
    session_id: Optional[str] = None


class AgentEventEmitter:
    """Central event emitter with per-run sequence tracking."""

    def __init__(self, events_dir: Optional[Path] = None, max_recent: int = 500):
        self._listeners: List[EventListener] = []
        self._seq_counters: DefaultDict[str, int] = defaultdict(int)
        self._events_dir = Path(events_dir) if events_dir is not None else None
        self._recent: List[AgentEvent] = []
        self._max_recent = max(1, int(max_recent))
        self._lock = threading.RLock()

    @property
    def events_dir(self) -> Optional[Path]:
        return self._events_dir

    def emit(
        self,
        agent_id: str,
        run_id: str,
        stream: str,
        event_type: str,
        data: Optional[dict] = None,
        session_id: Optional[str] = None,
    ) -> AgentEvent:
        payload = dict(data or {})

        with self._lock:
            self._seq_counters[run_id] += 1
            seq = self._seq_counters[run_id]

        event = AgentEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            agent_id=str(agent_id),
            seq=seq,
            stream=str(stream),
            event_type=str(event_type),
            ts=time.time(),
            data=payload,
            session_id=session_id,
        )

        with self._lock:
            listeners = list(self._listeners)
            self._recent.append(event)
            if len(self._recent) > self._max_recent:
                self._recent = self._recent[-self._max_recent :]

        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("Event listener error")

        if self._events_dir:
            self._persist_event(event)

        return event

    def on_event(self, listener: EventListener) -> Callable[[], None]:
        """Register listener. Returns an unsubscribe function."""
        with self._lock:
            self._listeners.append(listener)

        def _unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return _unsubscribe

    def recent_events(
        self,
        agent_id: Optional[str] = None,
        stream: Optional[str] = None,
        limit: int = 50,
    ) -> List[AgentEvent]:
        with self._lock:
            events = list(self._recent)

        if agent_id:
            events = [item for item in events if item.agent_id == agent_id]
        if stream:
            events = [item for item in events if item.stream == stream]
        bounded_limit = max(1, int(limit))
        return events[-bounded_limit:]

    def _persist_event(self, event: AgentEvent) -> None:
        if not self._events_dir:
            return
        agent_dir = self._events_dir / event.agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        events_file = agent_dir / "events.jsonl"
        with events_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")
