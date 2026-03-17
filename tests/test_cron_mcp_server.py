from __future__ import annotations

import json
import urllib.request

from g3lobster.mcp.cron_server import DEFAULT_BASE_URL, CronMCPHandler, parse_args


class _Response:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


def test_cron_handler_initialize_response() -> None:
    handler = CronMCPHandler(parent_agent_id="athena")
    resp = handler.handle_request({"method": "initialize", "id": 1})
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "g3lobster-cron"


def test_cron_handler_tools_list() -> None:
    handler = CronMCPHandler(parent_agent_id="athena")
    resp = handler.handle_request({"method": "tools/list", "id": 2})
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert "list_cron_jobs" in tool_names
    assert "create_cron_job" in tool_names
    assert "update_cron_job" in tool_names
    assert "delete_cron_job" in tool_names
    assert "run_cron_job" in tool_names
    assert "get_cron_history" in tool_names


def test_cron_handler_requires_identity(monkeypatch) -> None:
    monkeypatch.delenv("G3LOBSTER_AGENT_ID", raising=False)
    handler = CronMCPHandler(parent_agent_id="")

    resp = handler.handle_request({
        "method": "tools/call",
        "id": 3,
        "params": {"name": "list_cron_jobs", "arguments": {}},
    })
    result = resp["result"]
    assert result["isError"] is True
    assert "G3LOBSTER_AGENT_ID" in result["content"][0]["text"]


def test_cron_handler_rejects_cross_agent_access() -> None:
    handler = CronMCPHandler(parent_agent_id="athena")

    resp = handler.handle_request({
        "method": "tools/call",
        "id": 4,
        "params": {
            "name": "list_cron_jobs",
            "arguments": {"agent_id": "zeus"},
        },
    })
    result = resp["result"]
    assert result["isError"] is True
    assert "cross-agent access denied" in result["content"][0]["text"]


def test_cron_handler_create_cron_job_calls_expected_endpoint(monkeypatch) -> None:
    handler = CronMCPHandler(base_url="http://localhost:20001", parent_agent_id="athena")
    captured = {}

    def fake_urlopen(req, timeout):
        assert timeout == 30
        assert req.get_header("X-g3lobster-agent-source") == "mcp"
        assert req.get_header("X-g3lobster-actor-agent-id") == "athena"
        if req.get_method() == "GET":
            captured["list_url"] = req.full_url
            return _Response("[]")
        captured["create_url"] = req.full_url
        captured["method"] = req.get_method()
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response('{"id":"task-1","agent_id":"athena"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    resp = handler.handle_request({
        "method": "tools/call",
        "id": 5,
        "params": {
            "name": "create_cron_job",
            "arguments": {
                "schedule": "0 9 * * *",
                "instruction": "daily summary",
                "enabled": False,
                "dm_target": "nick@example.com",
            },
        },
    })

    assert "error" not in resp
    result = resp["result"]
    assert result.get("isError") is not True
    assert captured["list_url"] == "http://localhost:20001/agents/athena/crons"
    assert captured["create_url"] == "http://localhost:20001/agents/athena/crons"
    assert captured["method"] == "POST"
    assert captured["payload"] == {
        "schedule": "0 9 * * *",
        "instruction": "daily summary",
        "enabled": False,
        "dm_target": "nick@example.com",
    }


def test_cron_handler_create_rejects_subminute_schedule() -> None:
    handler = CronMCPHandler(parent_agent_id="athena")

    resp = handler.handle_request({
        "method": "tools/call",
        "id": 51,
        "params": {
            "name": "create_cron_job",
            "arguments": {
                "schedule": "*/30 * * * * *",
                "instruction": "daily summary",
            },
        },
    })
    result = resp["result"]
    assert result["isError"] is True
    assert "Sub-minute cron schedules are not allowed" in result["content"][0]["text"]


def test_cron_handler_create_rejects_long_instruction() -> None:
    handler = CronMCPHandler(parent_agent_id="athena")

    resp = handler.handle_request({
        "method": "tools/call",
        "id": 52,
        "params": {
            "name": "create_cron_job",
            "arguments": {
                "schedule": "0 9 * * *",
                "instruction": "x" * 2001,
            },
        },
    })
    result = resp["result"]
    assert result["isError"] is True
    assert "Instruction must be 2000 characters or fewer" in result["content"][0]["text"]


def test_cron_handler_create_rejects_over_limit(monkeypatch) -> None:
    monkeypatch.setenv("G3LOBSTER_CRON_MAX_JOBS_PER_AGENT", "1")
    handler = CronMCPHandler(base_url="http://localhost:20001", parent_agent_id="athena")

    def fake_urlopen(req, timeout):
        assert timeout == 30
        assert req.get_header("X-g3lobster-agent-source") == "mcp"
        assert req.get_header("X-g3lobster-actor-agent-id") == "athena"
        assert req.get_method() == "GET"
        return _Response('[{"id":"task-1"}]')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    resp = handler.handle_request({
        "method": "tools/call",
        "id": 53,
        "params": {
            "name": "create_cron_job",
            "arguments": {
                "schedule": "0 9 * * *",
                "instruction": "daily summary",
            },
        },
    })
    result = resp["result"]
    assert result["isError"] is True
    assert "maximum cron jobs per agent exceeded" in result["content"][0]["text"]


def test_cron_handler_run_and_history_paths(monkeypatch) -> None:
    handler = CronMCPHandler(base_url="http://localhost:20001", parent_agent_id="athena")
    captured = []

    def fake_urlopen(req, timeout):
        assert timeout == 30
        assert req.get_header("X-g3lobster-agent-source") == "mcp"
        assert req.get_header("X-g3lobster-actor-agent-id") == "athena"
        captured.append((req.get_method(), req.full_url, req.data))
        if req.full_url.endswith("/run"):
            return _Response('{"task_id":"task-1","status":"completed"}')
        return _Response('{"task_id":"task-1","runs":[]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    run_resp = handler.handle_request({
        "method": "tools/call",
        "id": 6,
        "params": {"name": "run_cron_job", "arguments": {"task_id": "task-1"}},
    })
    hist_resp = handler.handle_request({
        "method": "tools/call",
        "id": 7,
        "params": {"name": "get_cron_history", "arguments": {"task_id": "task-1"}},
    })

    assert run_resp["result"].get("isError") is not True
    assert hist_resp["result"].get("isError") is not True
    assert captured[0][0] == "POST"
    assert captured[0][1] == "http://localhost:20001/agents/athena/crons/task-1/run"
    assert captured[1][0] == "GET"
    assert captured[1][1] == "http://localhost:20001/agents/athena/crons/task-1/history"


def test_cron_handler_parse_args_optional_parent_id() -> None:
    args = parse_args(["--base-url", "http://localhost:9999"])
    assert args.parent_agent_id == ""
    assert args.base_url == "http://localhost:9999"


def test_cron_default_base_url() -> None:
    assert DEFAULT_BASE_URL == "http://localhost:20001"
