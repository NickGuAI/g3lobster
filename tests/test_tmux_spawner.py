from __future__ import annotations

import time

import pytest

from g3lobster.tmux.spawner import SubAgentSpawner, SubAgentStatus


class FakeTmuxSession:
    panes = {}
    existing = {}
    created = []
    killed = []

    def __init__(self, name: str, socket_name: str = "g3lobster"):
        self.name = name
        self.socket_name = socket_name

    async def exists(self) -> bool:
        return bool(self.existing.get(self.name, False))

    async def create(self, command: str, cwd: str = None) -> None:
        self.created.append((self.name, command, cwd, self.socket_name))
        self.existing[self.name] = True
        self.panes.setdefault(self.name, "")

    async def capture_pane(self, max_lines: int = 2000) -> str:
        return str(self.panes.get(self.name, ""))

    async def kill(self) -> None:
        self.killed.append((self.name, self.socket_name))
        self.existing[self.name] = False


@pytest.fixture(autouse=True)
def _reset_fake_tmux():
    FakeTmuxSession.panes = {}
    FakeTmuxSession.existing = {}
    FakeTmuxSession.created = []
    FakeTmuxSession.killed = []


@pytest.mark.asyncio
async def test_subagent_spawner_enforces_max_concurrency(monkeypatch) -> None:
    monkeypatch.setattr("g3lobster.tmux.spawner.TmuxSession", FakeTmuxSession)

    spawner = SubAgentSpawner(
        command="gemini",
        args=["-y"],
        max_concurrent_per_agent=1,
    )

    first = await spawner.spawn(agent_id="alpha", prompt="first task")
    assert first.status == SubAgentStatus.RUNNING

    with pytest.raises(RuntimeError, match="max concurrent sub-agents"):
        await spawner.spawn(agent_id="alpha", prompt="second task")


@pytest.mark.asyncio
async def test_subagent_spawner_marks_completed_from_exit_sentinel(monkeypatch) -> None:
    monkeypatch.setattr("g3lobster.tmux.spawner.TmuxSession", FakeTmuxSession)

    spawner = SubAgentSpawner(command="gemini", args=["-y"])
    run = await spawner.spawn(agent_id="alpha", prompt="summarize")

    FakeTmuxSession.panes[run.session_name] = "done output\n__G3LOBSTER_EXIT_CODE=0\n"

    runs = await spawner.list_runs(agent_id="alpha", active_only=False)
    assert len(runs) == 1
    assert runs[0].status == SubAgentStatus.COMPLETED
    assert "done output" in (runs[0].output or "")
    assert (run.session_name, "g3lobster") in FakeTmuxSession.killed


@pytest.mark.asyncio
async def test_subagent_spawner_timeout_auto_kill(monkeypatch) -> None:
    monkeypatch.setattr("g3lobster.tmux.spawner.TmuxSession", FakeTmuxSession)

    spawner = SubAgentSpawner(command="gemini", args=["-y"], default_timeout_s=1.0)
    run = await spawner.spawn(agent_id="alpha", prompt="long", timeout_s=1.0)
    spawner._runs[run.session_name].started_at = time.time() - 30.0

    runs = await spawner.list_runs(agent_id="alpha", active_only=False)
    assert len(runs) == 1
    assert runs[0].status == SubAgentStatus.TIMED_OUT
    assert (run.session_name, "g3lobster") in FakeTmuxSession.killed
