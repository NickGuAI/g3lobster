"""Tests for AlertManager."""

import asyncio
import time

import pytest

from g3lobster.alerts import AlertManager, AlertSeverity, make_event


@pytest.mark.asyncio
async def test_send_disabled():
    mgr = AlertManager(enabled=False)
    event = make_event("agent_dead", "agent-1", "process exited")
    await mgr.send(event)  # should not raise


@pytest.mark.asyncio
async def test_send_rate_limited():
    mgr = AlertManager(enabled=True, rate_limit_s=300)
    event = make_event("agent_dead", "agent-1", "process exited")
    # First send records the alert
    await mgr.send(event)
    # Second should be rate-limited (no error, just skipped)
    await mgr.send(event)
    assert mgr._last_alert.get("agent_dead:agent-1") is not None


@pytest.mark.asyncio
async def test_send_below_min_severity():
    mgr = AlertManager(enabled=True, min_severity="critical")
    event = make_event("agent_restarted", "agent-1", "restarted")
    # agent_restarted has WARNING severity, min is CRITICAL — should skip
    await mgr.send(event)
    assert "agent_restarted:agent-1" not in mgr._last_alert


def test_make_event():
    event = make_event("agent_dead", "agent-1", "process crashed")
    assert event.event_type == "agent_dead"
    assert event.agent_id == "agent-1"
    assert event.severity == AlertSeverity.CRITICAL
    assert event.timestamp  # non-empty


def test_severity_ordering():
    assert AlertSeverity.CRITICAL >= AlertSeverity.ERROR
    assert AlertSeverity.ERROR >= AlertSeverity.WARNING
    assert not (AlertSeverity.WARNING >= AlertSeverity.ERROR)
