from __future__ import annotations

from typing import Optional

import pytest

from g3lobster.agents.persona import AgentPersona, load_persona, save_persona
from g3lobster.chat.bridge_manager import BridgeManager


class FakeChatBridge:
    def __init__(self, space_id, agent_filter=None, **_kwargs):
        self.space_id = space_id
        self.agent_filter = set(agent_filter or set())
        self.started = 0
        self.stopped = 0
        self._running = False

    async def start(self):
        self.started += 1
        self._running = True

    async def stop(self):
        self.stopped += 1
        self._running = False

    @property
    def is_running(self):
        return self._running

    def set_agent_filter(self, agent_ids):
        self.agent_filter = set(agent_ids or set())


class FakeRegistry:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def load_persona(self, agent_id: str):
        return load_persona(self.data_dir, agent_id)


def _save(
    data_dir: str,
    *,
    agent_id: str,
    name: str,
    enabled: bool = True,
    bridge_enabled: bool = True,
    space_id: Optional[str] = None,
) -> None:
    save_persona(
        data_dir,
        AgentPersona(
            id=agent_id,
            name=name,
            enabled=enabled,
            bridge_enabled=bridge_enabled,
            space_id=space_id,
        ),
    )


@pytest.mark.asyncio
async def test_shared_space_uses_one_bridge_instance(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    _save(data_dir, agent_id="iris", name="Iris", space_id="spaces/A")
    _save(data_dir, agent_id="nova", name="Nova", space_id="spaces/A")

    registry = FakeRegistry(data_dir)
    bridges: list[FakeChatBridge] = []

    def bridge_factory(space_id, **kwargs):
        bridge = FakeChatBridge(space_id=space_id, **kwargs)
        bridges.append(bridge)
        return bridge

    manager = BridgeManager(registry=registry, bridge_factory=bridge_factory)

    assert await manager.start_bridge("iris") is True
    assert await manager.start_bridge("nova") is True

    assert len(bridges) == 1
    assert bridges[0].space_id == "spaces/A"
    assert bridges[0].agent_filter == {"iris", "nova"}
    assert bridges[0].started == 1


@pytest.mark.asyncio
async def test_stop_one_agent_does_not_stop_shared_space_bridge(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    _save(data_dir, agent_id="iris", name="Iris", space_id="spaces/A")
    _save(data_dir, agent_id="nova", name="Nova", space_id="spaces/A")

    registry = FakeRegistry(data_dir)
    bridges: list[FakeChatBridge] = []

    def bridge_factory(space_id, **kwargs):
        bridge = FakeChatBridge(space_id=space_id, **kwargs)
        bridges.append(bridge)
        return bridge

    manager = BridgeManager(registry=registry, bridge_factory=bridge_factory)
    await manager.start_bridge("iris")
    await manager.start_bridge("nova")

    assert await manager.stop_bridge("iris") is True
    assert manager.get_bridge("iris") is None
    assert manager.get_bridge("nova") is bridges[0]
    assert bridges[0].is_running is True
    assert bridges[0].stopped == 0
    assert bridges[0].agent_filter == {"nova"}


@pytest.mark.asyncio
async def test_distinct_spaces_create_distinct_bridges(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    _save(data_dir, agent_id="iris", name="Iris", space_id="spaces/A")
    _save(data_dir, agent_id="nova", name="Nova", space_id="spaces/B")

    registry = FakeRegistry(data_dir)
    bridges: list[FakeChatBridge] = []

    def bridge_factory(space_id, **kwargs):
        bridge = FakeChatBridge(space_id=space_id, **kwargs)
        bridges.append(bridge)
        return bridge

    manager = BridgeManager(registry=registry, bridge_factory=bridge_factory)
    await manager.start_bridge("iris")
    await manager.start_bridge("nova")

    assert len(bridges) == 2
    assert {bridge.space_id for bridge in bridges} == {"spaces/A", "spaces/B"}


@pytest.mark.asyncio
async def test_agent_without_space_is_not_started(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    _save(data_dir, agent_id="iris", name="Iris", space_id=None, bridge_enabled=True)

    registry = FakeRegistry(data_dir)
    manager = BridgeManager(registry=registry, bridge_factory=lambda space_id, **kwargs: FakeChatBridge(space_id, **kwargs))

    assert await manager.start_bridge("iris") is False
    assert manager.status()[0]["is_running"] is False


@pytest.mark.asyncio
async def test_legacy_space_migrates_to_enabled_agents(tmp_path) -> None:
    data_dir = str(tmp_path / "data")
    _save(
        data_dir,
        agent_id="iris",
        name="Iris",
        enabled=True,
        bridge_enabled=False,
        space_id=None,
    )

    registry = FakeRegistry(data_dir)
    manager = BridgeManager(
        registry=registry,
        bridge_factory=lambda space_id, **kwargs: FakeChatBridge(space_id, **kwargs),
        legacy_space_id="spaces/legacy",
    )

    # Status call triggers idempotent migration path.
    status = manager.status()
    assert status[0]["space_id"] == "spaces/legacy"
    assert status[0]["bridge_enabled"] is True

    migrated = load_persona(data_dir, "iris")
    assert migrated is not None
    assert migrated.space_id == "spaces/legacy"
    assert migrated.bridge_enabled is True
