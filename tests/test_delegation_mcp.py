from __future__ import annotations

import pytest

from g3lobster.mcp.delegation_server import DelegationAPIClient


class StubDelegationClient(DelegationAPIClient):
    def __init__(self):
        super().__init__(
            base_url="http://127.0.0.1:20001",
            parent_agent_id="athena",
            parent_session_id="thread-1",
        )
        self.calls = []

    def _request_json(self, method: str, path: str, body=None, timeout_s: float = 30.0):
        self.calls.append((method, path, body, timeout_s))
        if method == "POST" and path == "/delegation/run":
            return {"run_id": "run-1", "status": "completed", "result": "ok", "error": None}
        if method == "GET" and path == "/agents":
            return {"agents": [{"id": "hephaestus", "name": "Hephaestus", "enabled": True}]}
        if method == "GET" and path == "/agents/hephaestus":
            return {"id": "hephaestus", "soul": "Build specialist.\nWrites code."}
        raise AssertionError(f"Unexpected request: {method} {path}")


def test_delegate_client_preserves_timeout_value() -> None:
    client = StubDelegationClient()

    payload = client.delegate_to_agent(agent_id="hephaestus", task="build", timeout_s=0.25)

    assert payload["status"] == "completed"
    post_calls = [call for call in client.calls if call[0] == "POST"]
    assert post_calls
    _method, _path, body, timeout_s = post_calls[0]
    assert body is not None
    assert body["timeout_s"] == 0.25
    assert timeout_s == pytest.approx(15.25)


def test_delegate_client_rejects_non_positive_timeout() -> None:
    client = StubDelegationClient()

    with pytest.raises(ValueError, match="timeout_s must be greater than 0"):
        client.delegate_to_agent(agent_id="hephaestus", task="build", timeout_s=0.0)


def test_delegate_client_rejects_blank_agent_or_task() -> None:
    client = StubDelegationClient()

    with pytest.raises(ValueError, match="agent_id is required"):
        client.delegate_to_agent(agent_id="   ", task="build")

    with pytest.raises(ValueError, match="task is required"):
        client.delegate_to_agent(agent_id="hephaestus", task="   ")


def test_list_agents_supports_dict_agents_payload() -> None:
    client = StubDelegationClient()

    agents = client.list_agents()

    assert agents == [
        {
            "id": "hephaestus",
            "name": "Hephaestus",
            "description": "Build specialist.",
        }
    ]


@pytest.mark.asyncio
async def test_delegate_client_async_wrapper() -> None:
    client = StubDelegationClient()

    payload = await client.delegate_to_agent_async(agent_id="hephaestus", task="build", timeout_s=0.25)

    assert payload["status"] == "completed"
