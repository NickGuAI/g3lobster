"""Tests for incident response lifecycle: create → status → action → assign → resolve."""

from __future__ import annotations

import pytest

from g3lobster.incident.model import (
    ActionItem,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    TimelineEntry,
)
from g3lobster.incident.store import IncidentStore
from g3lobster.incident.formatter import (
    format_incident_card,
    format_resolution_summary,
    format_status_prompt,
    format_timeline,
)


AGENT_ID = "test-agent"


@pytest.fixture
def store(tmp_path):
    return IncidentStore(str(tmp_path / "data"))


# ------------------------------------------------------------------
# Store: create and get
# ------------------------------------------------------------------


def test_create_incident(store):
    inc = store.create(AGENT_ID, "prod API latency spike")
    assert inc.title == "prod API latency spike"
    assert inc.status == IncidentStatus.ACTIVE
    assert inc.severity == IncidentSeverity.SEV3
    assert inc.created_at


def test_get_active_incident(store):
    inc = store.create(AGENT_ID, "outage")
    active = store.get_active(AGENT_ID)
    assert active is not None
    assert active.id == inc.id


def test_no_active_incident(store):
    assert store.get_active(AGENT_ID) is None


def test_get_by_id(store):
    inc = store.create(AGENT_ID, "test")
    fetched = store.get(AGENT_ID, inc.id)
    assert fetched is not None
    assert fetched.title == "test"


# ------------------------------------------------------------------
# Store: timeline
# ------------------------------------------------------------------


def test_append_timeline(store):
    inc = store.create(AGENT_ID, "outage")
    updated = store.append_timeline(AGENT_ID, inc.id, "alice", "services recovering", "status")
    assert updated is not None
    assert len(updated.timeline) == 1
    assert updated.timeline[0].author == "alice"
    assert updated.timeline[0].content == "services recovering"
    assert updated.timeline[0].entry_type == "status"


def test_append_multiple_timeline_entries(store):
    inc = store.create(AGENT_ID, "outage")
    store.append_timeline(AGENT_ID, inc.id, "alice", "investigating", "status")
    store.append_timeline(AGENT_ID, inc.id, "bob", "found root cause", "note")
    updated = store.get(AGENT_ID, inc.id)
    assert len(updated.timeline) == 2


# ------------------------------------------------------------------
# Store: action items
# ------------------------------------------------------------------


def test_add_action(store):
    inc = store.create(AGENT_ID, "outage")
    updated = store.add_action(AGENT_ID, inc.id, "rollback deployment", "alice")
    assert updated is not None
    assert len(updated.actions) == 1
    assert updated.actions[0].description == "rollback deployment"
    assert updated.actions[0].assignee == "alice"
    assert updated.actions[0].status == "open"


def test_update_action(store):
    inc = store.create(AGENT_ID, "outage")
    updated = store.add_action(AGENT_ID, inc.id, "rollback")
    action_id = updated.actions[0].id
    resolved = store.update_action(AGENT_ID, inc.id, action_id, "done")
    assert resolved is not None
    assert resolved.actions[0].status == "done"


# ------------------------------------------------------------------
# Store: roles
# ------------------------------------------------------------------


def test_add_role(store):
    inc = store.create(AGENT_ID, "outage")
    updated = store.add_role(AGENT_ID, inc.id, "commander", "@alice")
    assert updated is not None
    assert updated.roles["commander"] == "@alice"


# ------------------------------------------------------------------
# Store: resolve
# ------------------------------------------------------------------


def test_resolve_incident(store):
    inc = store.create(AGENT_ID, "outage")
    store.append_timeline(AGENT_ID, inc.id, "alice", "investigating", "status")
    store.add_action(AGENT_ID, inc.id, "rollback")

    resolved = store.resolve(AGENT_ID, inc.id, "root cause was upstream provider")
    assert resolved is not None
    assert resolved.status == IncidentStatus.RESOLVED
    assert resolved.resolved_at is not None
    # Summary appended as timeline entry
    assert any("root cause" in e.content for e in resolved.timeline)

    # Active pointer should be cleared
    assert store.get_active(AGENT_ID) is None


def test_resolve_without_summary(store):
    inc = store.create(AGENT_ID, "outage")
    resolved = store.resolve(AGENT_ID, inc.id)
    assert resolved is not None
    assert resolved.status == IncidentStatus.RESOLVED


# ------------------------------------------------------------------
# Store: list
# ------------------------------------------------------------------


def test_list_incidents(store):
    store.create(AGENT_ID, "incident 1")
    store.create(AGENT_ID, "incident 2")
    incidents = store.list_incidents(AGENT_ID)
    assert len(incidents) == 2


def test_list_empty(store):
    assert store.list_incidents(AGENT_ID) == []


# ------------------------------------------------------------------
# Store: persistence across reads
# ------------------------------------------------------------------


def test_persistence(store):
    inc = store.create(AGENT_ID, "outage")
    store.append_timeline(AGENT_ID, inc.id, "alice", "investigating", "status")
    store.add_action(AGENT_ID, inc.id, "rollback")
    store.add_role(AGENT_ID, inc.id, "comms", "@bob")

    # Re-read from disk
    fetched = store.get(AGENT_ID, inc.id)
    assert fetched is not None
    assert len(fetched.timeline) == 1
    assert len(fetched.actions) == 1
    assert fetched.roles["comms"] == "@bob"
    assert fetched.severity == IncidentSeverity.SEV3
    assert fetched.status == IncidentStatus.ACTIVE


# ------------------------------------------------------------------
# Formatter
# ------------------------------------------------------------------


def test_format_incident_card():
    inc = Incident(
        id="abc",
        title="prod API latency",
        severity=IncidentSeverity.SEV2,
        created_at="2026-03-11T10:00:00+00:00",
    )
    card = format_incident_card(inc)
    assert "prod API latency" in card
    assert "SEV2" in card
    assert "Active" in card


def test_format_timeline():
    inc = Incident(
        id="abc",
        title="outage",
        timeline=[
            TimelineEntry(timestamp="2026-03-11T10:00:00+00:00", author="alice", content="investigating", entry_type="status"),
            TimelineEntry(timestamp="2026-03-11T10:15:00+00:00", author="bob", content="found cause", entry_type="note"),
        ],
    )
    text = format_timeline(inc)
    assert "10:00" in text
    assert "alice" in text
    assert "bob" in text


def test_format_resolution_summary():
    inc = Incident(
        id="abc",
        title="outage",
        severity=IncidentSeverity.SEV1,
        status=IncidentStatus.RESOLVED,
        created_at="2026-03-11T10:00:00+00:00",
        resolved_at="2026-03-11T11:30:00+00:00",
        timeline=[
            TimelineEntry(timestamp="2026-03-11T10:00:00+00:00", author="alice", content="started", entry_type="status"),
        ],
        actions=[
            ActionItem(id="a1", description="rollback", status="done", created_at="2026-03-11T10:05:00+00:00"),
            ActionItem(id="a2", description="investigate root cause", status="open", created_at="2026-03-11T10:10:00+00:00"),
        ],
    )
    text = format_resolution_summary(inc)
    assert "Resolution Summary" in text
    assert "1h 30m" in text
    assert "rollback" in text


def test_format_status_prompt():
    inc = Incident(id="abc", title="prod outage")
    text = format_status_prompt(inc, 14)
    assert "14 minutes" in text
    assert "prod outage" in text


# ------------------------------------------------------------------
# Command integration
# ------------------------------------------------------------------


def test_command_incident_create(tmp_path):
    from g3lobster.cron.store import CronStore
    from g3lobster.chat.commands import handle

    cron_store = CronStore(str(tmp_path / "cron"))
    incident_store = IncidentStore(str(tmp_path / "data"))

    reply = handle("/incident prod API latency spike", AGENT_ID, cron_store, incident_store)
    assert reply is not None
    assert "Incident declared" in reply
    assert "prod API latency spike" in reply

    # Incident should be active
    active = incident_store.get_active(AGENT_ID)
    assert active is not None
    assert active.title == "prod API latency spike"

    # Cron task should be created
    crons = cron_store.list_tasks(AGENT_ID)
    assert len(crons) == 1
    assert "__INCIDENT_PROMPT__" in crons[0].instruction


def test_command_incident_status(tmp_path):
    from g3lobster.cron.store import CronStore
    from g3lobster.chat.commands import handle

    cron_store = CronStore(str(tmp_path / "cron"))
    incident_store = IncidentStore(str(tmp_path / "data"))

    handle("/incident outage", AGENT_ID, cron_store, incident_store)
    reply = handle("/incident status services recovering", AGENT_ID, cron_store, incident_store)
    assert reply is not None
    assert "Timeline updated" in reply


def test_command_incident_action(tmp_path):
    from g3lobster.cron.store import CronStore
    from g3lobster.chat.commands import handle

    cron_store = CronStore(str(tmp_path / "cron"))
    incident_store = IncidentStore(str(tmp_path / "data"))

    handle("/incident outage", AGENT_ID, cron_store, incident_store)
    reply = handle("/incident action rollback deployment", AGENT_ID, cron_store, incident_store)
    assert reply is not None
    assert "Action item added" in reply


def test_command_incident_assign(tmp_path):
    from g3lobster.cron.store import CronStore
    from g3lobster.chat.commands import handle

    cron_store = CronStore(str(tmp_path / "cron"))
    incident_store = IncidentStore(str(tmp_path / "data"))

    handle("/incident outage", AGENT_ID, cron_store, incident_store)
    reply = handle("/incident assign commander @alice", AGENT_ID, cron_store, incident_store)
    assert reply is not None
    assert "commander" in reply
    assert "@alice" in reply


def test_command_resolve(tmp_path):
    from g3lobster.cron.store import CronStore
    from g3lobster.chat.commands import handle

    cron_store = CronStore(str(tmp_path / "cron"))
    incident_store = IncidentStore(str(tmp_path / "data"))

    handle("/incident outage", AGENT_ID, cron_store, incident_store)
    reply = handle("/resolve root cause was upstream", AGENT_ID, cron_store, incident_store)
    assert reply is not None
    assert "Incident resolved" in reply
    assert "Resolution Summary" in reply

    # Active should be cleared
    assert incident_store.get_active(AGENT_ID) is None

    # Cron should be cleaned up
    crons = cron_store.list_tasks(AGENT_ID)
    assert len(crons) == 0


def test_command_resolve_no_active(tmp_path):
    from g3lobster.cron.store import CronStore
    from g3lobster.chat.commands import handle

    cron_store = CronStore(str(tmp_path / "cron"))
    incident_store = IncidentStore(str(tmp_path / "data"))

    reply = handle("/resolve", AGENT_ID, cron_store, incident_store)
    assert reply is not None
    assert "No active incident" in reply


def test_command_help_includes_incident(tmp_path):
    from g3lobster.cron.store import CronStore
    from g3lobster.chat.commands import handle

    cron_store = CronStore(str(tmp_path / "cron"))
    reply = handle("/help", AGENT_ID, cron_store)
    assert reply is not None
    assert "/incident" in reply
    assert "/resolve" in reply
