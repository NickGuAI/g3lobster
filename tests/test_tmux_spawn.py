from __future__ import annotations

import asyncio

import pytest

from g3lobster.pool.tmux_spawn import TmuxSpawner


class _DummyProcess:
    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_tmux_spawner_spawn_and_capture(monkeypatch) -> None:
    calls = []

    async def fake_exec(*cmd, **_kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ("tmux", "-V"):
            return _DummyProcess(stdout=b"tmux 3.3\n")
        if cmd[1] == "capture-pane":
            return _DummyProcess(stdout=b"captured output\n")
        return _DummyProcess(stdout=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    spawner = TmuxSpawner(command="gemini", args=["-y"], session_prefix="g3l", idle_ttl_s=3600)
    session = await spawner.spawn(
        agent_id="athena",
        task_id="1234567890",
        prompt="long task",
        session_id="thread-1",
    )

    assert session == "g3l-athena-12345678"
    assert len(spawner.list_sessions()) == 1

    output = await spawner.capture(session)
    assert output == "captured output"

    assert any(call[:3] == ["tmux", "new-session", "-d"] for call in calls)


@pytest.mark.asyncio
async def test_tmux_spawner_evicts_idle(monkeypatch) -> None:
    async def fake_exec(*_cmd, **_kwargs):
        return _DummyProcess(stdout=b"ok\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    spawner = TmuxSpawner(command="gemini", idle_ttl_s=0)
    session = await spawner.spawn(
        agent_id="athena",
        task_id="abcdef1234",
        prompt="task",
        session_id="thread-1",
    )

    evicted = await spawner.evict_idle_sessions()
    assert evicted == [session]
    assert spawner.list_sessions() == []
