"""Tests for Google Chat interaction event handler."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_api import _build_test_app


def _make_client(tmp_path: Path) -> TestClient:
    app, _bridge_instances, _config_path = _build_test_app(tmp_path)
    return TestClient(app)


def test_message_event_returns_empty_json(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.post(
            "/chat/events",
            json={"type": "MESSAGE", "message": {"text": "hello"}},
        )
        assert resp.status_code == 200
        assert resp.json() == {}


def test_added_to_space_returns_greeting(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.post(
            "/chat/events",
            json={"type": "ADDED_TO_SPACE", "space": {"displayName": "TestSpace"}},
        )
        assert resp.status_code == 200
        assert "TestSpace" in resp.json()["text"]


def test_removed_from_space_returns_empty_json(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.post(
            "/chat/events",
            json={"type": "REMOVED_FROM_SPACE"},
        )
        assert resp.status_code == 200
        assert resp.json() == {}


def test_unknown_event_returns_empty_json(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.post(
            "/chat/events",
            json={"type": "CARD_CLICKED"},
        )
        assert resp.status_code == 200
        assert resp.json() == {}
