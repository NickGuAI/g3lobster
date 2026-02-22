from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from g3lobster.api.server import create_app


class DummyRegistry:
    async def start_all(self):
        return None

    async def stop_all(self):
        return None


def _build_test_app():
    return create_app(registry=DummyRegistry())


@pytest.fixture
def auth_headers(monkeypatch):
    monkeypatch.setattr(
        "g3lobster.api.routes_chat_events._verify_google_chat_bearer_token",
        lambda _token, _audience: {"email": "chat@system.gserviceaccount.com"},
    )
    return {"Authorization": "Bearer test-token"}


def test_message_event_returns_empty_json(auth_headers):
    app = _build_test_app()
    with TestClient(app) as client:
        response = client.post(
            "/chat/events",
            headers=auth_headers,
            json={"type": "MESSAGE", "message": {"text": "hello"}},
        )
        assert response.status_code == 200
        assert response.json() == {}


def test_added_to_space_returns_greeting(auth_headers):
    app = _build_test_app()
    with TestClient(app) as client:
        response = client.post(
            "/chat/events",
            headers=auth_headers,
            json={"type": "ADDED_TO_SPACE", "space": {"displayName": "TestSpace"}},
        )
        assert response.status_code == 200
        assert response.json() == {"text": "Hello! I've joined TestSpace."}


def test_removed_from_space_returns_empty_json(auth_headers):
    app = _build_test_app()
    with TestClient(app) as client:
        response = client.post("/chat/events", headers=auth_headers, json={"type": "REMOVED_FROM_SPACE"})
        assert response.status_code == 200
        assert response.json() == {}


def test_unknown_event_returns_empty_json(auth_headers):
    app = _build_test_app()
    with TestClient(app) as client:
        response = client.post("/chat/events", headers=auth_headers, json={"type": "UNKNOWN_TYPE"})
        assert response.status_code == 200
        assert response.json() == {}


def test_missing_bearer_token_returns_401():
    app = _build_test_app()
    with TestClient(app) as client:
        response = client.post("/chat/events", json={"type": "MESSAGE", "message": {"text": "hello"}})
        assert response.status_code == 401


def test_invalid_authorization_header_returns_401():
    app = _build_test_app()
    with TestClient(app) as client:
        response = client.post(
            "/chat/events",
            headers={"Authorization": "Basic test-token"},
            json={"type": "MESSAGE", "message": {"text": "hello"}},
        )
        assert response.status_code == 401


def test_invalid_bearer_token_returns_401(monkeypatch):
    def _raise_unauthorized(_token: str, _audience: str):
        raise HTTPException(status_code=401, detail="Invalid Google Chat bearer token")

    monkeypatch.setattr("g3lobster.api.routes_chat_events._verify_google_chat_bearer_token", _raise_unauthorized)

    app = _build_test_app()
    with TestClient(app) as client:
        response = client.post(
            "/chat/events",
            headers={"Authorization": "Bearer invalid-token"},
            json={"type": "MESSAGE", "message": {"text": "hello"}},
        )
        assert response.status_code == 401
