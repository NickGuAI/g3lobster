"""Incident data model — status, severity, timeline, and action items."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class IncidentStatus(Enum):
    ACTIVE = "active"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"


class IncidentSeverity(Enum):
    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"

    @classmethod
    def from_str(cls, s: str) -> "IncidentSeverity":
        return cls(s.lower()) if s.lower() in {m.value for m in cls} else cls.SEV3


@dataclass
class TimelineEntry:
    timestamp: str  # ISO 8601
    author: str
    content: str
    entry_type: str  # "status", "action", "note"


@dataclass
class ActionItem:
    id: str
    description: str
    assignee: str = ""
    status: str = "open"  # "open" or "done"
    created_at: str = ""  # ISO 8601


@dataclass
class Incident:
    id: str
    title: str
    severity: IncidentSeverity = IncidentSeverity.SEV3
    status: IncidentStatus = IncidentStatus.ACTIVE
    commander: str = ""
    roles: Dict[str, str] = field(default_factory=dict)
    timeline: List[TimelineEntry] = field(default_factory=list)
    actions: List[ActionItem] = field(default_factory=list)
    created_at: str = ""  # ISO 8601
    resolved_at: Optional[str] = None
    thread_id: str = ""
    space_id: str = ""
    cron_task_id: Optional[str] = None
