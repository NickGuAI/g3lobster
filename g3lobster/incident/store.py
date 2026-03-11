"""File-based incident storage.

Incidents are persisted as JSON at ``{data_dir}/{agent_id}/incidents/{incident_id}.json``.
The active incident pointer is stored at ``{data_dir}/{agent_id}/active_incident.json``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from g3lobster.incident.model import (
    ActionItem,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    TimelineEntry,
)

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _safe_agent_id(agent_id: str) -> str:
    """Validate agent_id is filesystem-safe (no path traversal)."""
    safe = agent_id.strip()
    if not safe or not _ID_RE.match(safe):
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    return safe


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _incident_from_dict(data: dict) -> Incident:
    """Reconstruct an Incident from a plain dict (as read from JSON)."""
    d = {k: v for k, v in data.items() if k in Incident.__dataclass_fields__}
    # Reconstruct nested objects
    d["status"] = IncidentStatus(d["status"]) if "status" in d else IncidentStatus.ACTIVE
    d["severity"] = IncidentSeverity(d["severity"]) if "severity" in d else IncidentSeverity.SEV3
    d["timeline"] = [
        TimelineEntry(**{k: v for k, v in entry.items() if k in TimelineEntry.__dataclass_fields__})
        for entry in d.get("timeline", [])
        if isinstance(entry, dict)
    ]
    d["actions"] = [
        ActionItem(**{k: v for k, v in item.items() if k in ActionItem.__dataclass_fields__})
        for item in d.get("actions", [])
        if isinstance(item, dict)
    ]
    return Incident(**d)


def _serialize(incident: Incident) -> dict:
    """Convert Incident to a JSON-serializable dict."""
    d = asdict(incident)
    d["status"] = incident.status.value
    d["severity"] = incident.severity.value
    return d


class IncidentStore:
    """CRUD store for per-agent incidents backed by local JSON files."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)

    def _incidents_dir(self, agent_id: str) -> Path:
        safe = _safe_agent_id(agent_id)
        return self._data_dir / safe / "incidents"

    def _active_file(self, agent_id: str) -> Path:
        safe = _safe_agent_id(agent_id)
        return self._data_dir / safe / "active_incident.json"

    # ------------------------------------------------------------------
    # Internal I/O helpers
    # ------------------------------------------------------------------

    def _save(self, agent_id: str, incident: Incident) -> None:
        """Atomically write incident JSON to its file."""
        incidents_dir = self._incidents_dir(agent_id)
        incidents_dir.mkdir(parents=True, exist_ok=True)
        path = incidents_dir / f"{incident.id}.json"
        payload = json.dumps(_serialize(incident), indent=2, ensure_ascii=False)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=incidents_dir,
            prefix=f"{incident.id}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                tmp.write(payload + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    def _write_active_pointer(self, agent_id: str, incident_id: Optional[str]) -> None:
        """Atomically write (or clear) the active incident pointer."""
        path = self._active_file(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(incident_id or "", ensure_ascii=False)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                tmp.write(payload + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    def _read_incident_file(self, path: Path) -> Optional[Incident]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            return _incident_from_dict(raw)
        except (TypeError, KeyError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(
        self,
        agent_id: str,
        title: str,
        thread_id: str = "",
        space_id: str = "",
    ) -> Incident:
        """Create a new incident, set it as active, and return it."""
        incident = Incident(
            id=str(uuid.uuid4()),
            title=title,
            thread_id=thread_id,
            space_id=space_id,
            created_at=_now(),
        )
        self._save(agent_id, incident)
        self._write_active_pointer(agent_id, incident.id)
        return incident

    def get(self, agent_id: str, incident_id: str) -> Optional[Incident]:
        """Read a single incident by ID."""
        path = self._incidents_dir(agent_id) / f"{incident_id}.json"
        if not path.exists():
            return None
        return self._read_incident_file(path)

    def get_active(self, agent_id: str) -> Optional[Incident]:
        """Read the currently active incident via the pointer file."""
        pointer = self._active_file(agent_id)
        if not pointer.exists():
            return None
        try:
            incident_id = json.loads(pointer.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not incident_id:
            return None
        return self.get(agent_id, incident_id)

    def append_timeline(
        self,
        agent_id: str,
        incident_id: str,
        author: str,
        content: str,
        entry_type: str = "status",
    ) -> Optional[Incident]:
        """Append a timeline entry, save, and return the updated incident."""
        incident = self.get(agent_id, incident_id)
        if incident is None:
            return None
        entry = TimelineEntry(
            timestamp=_now(),
            author=author,
            content=content,
            entry_type=entry_type,
        )
        incident.timeline.append(entry)
        self._save(agent_id, incident)
        return incident

    def add_action(
        self,
        agent_id: str,
        incident_id: str,
        description: str,
        assignee: str = "",
    ) -> Optional[Incident]:
        """Add an action item with a new uuid id."""
        incident = self.get(agent_id, incident_id)
        if incident is None:
            return None
        action = ActionItem(
            id=str(uuid.uuid4()),
            description=description,
            assignee=assignee,
            created_at=_now(),
        )
        incident.actions.append(action)
        self._save(agent_id, incident)
        return incident

    def update_action(
        self,
        agent_id: str,
        incident_id: str,
        action_id: str,
        status: str = "done",
    ) -> Optional[Incident]:
        """Mark an action item's status."""
        incident = self.get(agent_id, incident_id)
        if incident is None:
            return None
        for action in incident.actions:
            if action.id == action_id:
                action.status = status
                self._save(agent_id, incident)
                return incident
        return None

    def add_role(
        self,
        agent_id: str,
        incident_id: str,
        role: str,
        user: str,
    ) -> Optional[Incident]:
        """Set a role in the incident's roles dict."""
        incident = self.get(agent_id, incident_id)
        if incident is None:
            return None
        incident.roles[role] = user
        self._save(agent_id, incident)
        return incident

    def resolve(
        self,
        agent_id: str,
        incident_id: str,
        summary: str = "",
    ) -> Optional[Incident]:
        """Resolve the incident: set status, record timestamp, clear active pointer."""
        incident = self.get(agent_id, incident_id)
        if incident is None:
            return None
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = _now()
        if summary:
            entry = TimelineEntry(
                timestamp=incident.resolved_at,
                author="system",
                content=summary,
                entry_type="status",
            )
            incident.timeline.append(entry)
        self._save(agent_id, incident)
        # Clear active pointer if it points to this incident
        active = self._active_file(agent_id)
        if active.exists():
            try:
                current_id = json.loads(active.read_text(encoding="utf-8"))
                if current_id == incident_id:
                    self._write_active_pointer(agent_id, None)
            except (json.JSONDecodeError, OSError):
                pass
        return incident

    def list_incidents(self, agent_id: str) -> List[Incident]:
        """List all incidents for the given agent."""
        incidents_dir = self._incidents_dir(agent_id)
        if not incidents_dir.exists():
            return []
        result: List[Incident] = []
        for path in sorted(incidents_dir.glob("*.json")):
            incident = self._read_incident_file(path)
            if incident is not None:
                result.append(incident)
        return result
