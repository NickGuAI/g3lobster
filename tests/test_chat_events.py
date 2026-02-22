from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from g3lobster.api.server import create_app


class DummyRegistry:
    async def start_all(self) -> None:
        return None

    async def stop_all(self) -> None:
        return None


@pytest.fixture
def app():
    return create_app(registry=DummyRegistry())


def test_message_event_returns_empty_json(app) -> None:
    with TestClient(app) as client:
        response = client.post("/chat/events", json={"type": "MESSAGE", "message": {"text": "hello"}})

    assert response.status_code == 200
    assert response.json() == {}


def test_added_to_space_returns_greeting(app) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/chat/events",
            json={"type": "ADDED_TO_SPACE", "space": {"displayName": "TestSpace"}},
        )

    assert response.status_code == 200
    assert response.json() == {"text": "Hello! I've joined TestSpace."}


def test_removed_from_space_returns_empty_json(app) -> None:
    with TestClient(app) as client:
        response = client.post("/chat/events", json={"type": "REMOVED_FROM_SPACE"})

    assert response.status_code == 200
    assert response.json() == {}
