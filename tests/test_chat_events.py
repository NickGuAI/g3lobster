from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import g3lobster.api.routes_chat_events as chat_events
from g3lobster.api.server import create_app


class _NoopRegistry:
    async def start_all(self) -> None:
        return None

    async def stop_all(self) -> None:
        return None


@pytest.fixture
def verifier_spy(monkeypatch) -> dict:
    seen: dict[str, str] = {}

    def _fake_verify_google_chat_bearer(token: str, audience: str) -> None:
        seen["token"] = token
        seen["audience"] = audience
        if token != "valid-google-token":
            raise HTTPException(status_code=401, detail="Invalid Google Chat bearer token")

    monkeypatch.setattr(chat_events, "_verify_google_chat_bearer", _fake_verify_google_chat_bearer)
    return seen


@pytest.fixture
def client(verifier_spy: dict) -> TestClient:
    app = create_app(registry=_NoopRegistry())
    with TestClient(app) as test_client:
        yield test_client


def test_message_event_returns_empty_json(client: TestClient, verifier_spy: dict) -> None:
    response = client.post(
        "/chat/events",
        headers={"Authorization": "Bearer valid-google-token"},
        json={"type": "MESSAGE", "message": {"text": "hello"}},
    )
    assert response.status_code == 200
    assert response.json() == {}
    assert verifier_spy["audience"] == "http://testserver/chat/events"


def test_added_to_space_returns_greeting(client: TestClient) -> None:
    response = client.post(
        "/chat/events",
        headers={"Authorization": "Bearer valid-google-token"},
        json={"type": "ADDED_TO_SPACE", "space": {"displayName": "TestSpace"}},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "Hello! I've joined TestSpace."}


def test_removed_from_space_returns_empty_json(client: TestClient) -> None:
    response = client.post(
        "/chat/events",
        headers={"Authorization": "Bearer valid-google-token"},
        json={"type": "REMOVED_FROM_SPACE"},
    )
    assert response.status_code == 200
    assert response.json() == {}


def test_unknown_event_returns_empty_json(client: TestClient) -> None:
    response = client.post(
        "/chat/events",
        headers={"Authorization": "Bearer valid-google-token"},
        json={"type": "CARD_CLICKED"},
    )
    assert response.status_code == 200
    assert response.json() == {}


def test_event_requires_authorization_header(client: TestClient) -> None:
    response = client.post("/chat/events", json={"type": "MESSAGE"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Authorization bearer token"


def test_event_rejects_invalid_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/chat/events",
        headers={"Authorization": "Bearer invalid"},
        json={"type": "MESSAGE"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Google Chat bearer token"
