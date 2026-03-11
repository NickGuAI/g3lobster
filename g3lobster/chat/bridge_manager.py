"""Bridge manager for per-agent Google Chat bridge lifecycle."""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Set

from g3lobster.agents.persona import list_personas, save_persona
from g3lobster.config import normalize_space_id

logger = logging.getLogger(__name__)


class BridgeManager:
    """Manages ChatBridge instances by space and agent membership."""

    def __init__(
        self,
        registry,
        bridge_factory: Callable[..., object],
        *,
        legacy_space_id: Optional[str] = None,
    ) -> None:
        self.registry = registry
        self.bridge_factory = bridge_factory
        self.legacy_space_id: Optional[str] = normalize_space_id(legacy_space_id)

        self._bridges_by_space: Dict[str, object] = {}
        self._space_agents: Dict[str, Set[str]] = {}
        self._agent_to_space: Dict[str, str] = {}

    def set_legacy_space_id(self, space_id: Optional[str]) -> None:
        self.legacy_space_id = normalize_space_id(space_id)

    def _apply_legacy_space_defaults(self) -> int:
        """Migrate legacy chat.space_id to per-agent fields when needed."""
        if not self.legacy_space_id:
            return 0

        personas = list_personas(self.registry.data_dir)
        if not personas:
            return 0
        if any(persona.space_id for persona in personas):
            return 0

        candidates = [persona for persona in personas if persona.bridge_enabled]
        if not candidates:
            # Backward-compat for pre-migration setups where bridge was global.
            candidates = [persona for persona in personas if persona.enabled]

        migrated = 0
        for persona in candidates:
            if persona.space_id == self.legacy_space_id and persona.bridge_enabled:
                continue
            persona.space_id = self.legacy_space_id
            persona.bridge_enabled = True
            save_persona(self.registry.data_dir, persona)
            migrated += 1

        if migrated:
            logger.info("Migrated legacy chat space to %s agent personas", migrated)
        return migrated

    async def start_all(self) -> int:
        self._apply_legacy_space_defaults()
        started = 0
        for persona in list_personas(self.registry.data_dir):
            if not persona.bridge_enabled:
                continue
            if not normalize_space_id(persona.space_id):
                continue
            try:
                await self.start_bridge(persona.id)
                started += 1
            except Exception as exc:
                logger.warning("Failed to start bridge for %s: %s", persona.id, exc)
        return started

    async def stop_all(self) -> None:
        for bridge in list(self._bridges_by_space.values()):
            await bridge.stop()
        self._bridges_by_space.clear()
        self._space_agents.clear()
        self._agent_to_space.clear()

    async def start_bridge(self, agent_id: str) -> bool:
        self._apply_legacy_space_defaults()
        persona = self.registry.load_persona(agent_id)
        if not persona:
            return False
        if not persona.bridge_enabled:
            return False

        space_id = normalize_space_id(persona.space_id)
        if not space_id:
            return False

        current_space = self._agent_to_space.get(agent_id)
        if current_space and current_space != space_id:
            await self.stop_bridge(agent_id)

        target_agents = set(self._space_agents.get(space_id, set()))
        target_agents.add(agent_id)

        bridge = self._bridges_by_space.get(space_id)
        if bridge is None:
            bridge = self.bridge_factory(space_id=space_id, agent_filter=set(target_agents))
            try:
                await bridge.start()
            except Exception:
                try:
                    await bridge.stop()
                except Exception:
                    pass
                raise
            self._bridges_by_space[space_id] = bridge
        else:
            if hasattr(bridge, "set_agent_filter"):
                bridge.set_agent_filter(set(target_agents))
            if not getattr(bridge, "is_running", False):
                await bridge.start()
        self._space_agents[space_id] = target_agents
        self._agent_to_space[agent_id] = space_id
        return True

    async def stop_bridge(self, agent_id: str) -> bool:
        space_id = self._agent_to_space.pop(agent_id, None)
        if not space_id:
            return False

        members = set(self._space_agents.get(space_id, set()))
        members.discard(agent_id)

        bridge = self._bridges_by_space.get(space_id)
        if not members:
            self._space_agents.pop(space_id, None)
            self._bridges_by_space.pop(space_id, None)
            if bridge:
                await bridge.stop()
            return True

        self._space_agents[space_id] = members
        if bridge and hasattr(bridge, "set_agent_filter"):
            bridge.set_agent_filter(set(members))
        return True

    def get_bridge(self, agent_id: str):
        space_id = self._agent_to_space.get(agent_id)
        if not space_id:
            return None
        return self._bridges_by_space.get(space_id)

    @property
    def is_running(self) -> bool:
        return any(getattr(bridge, "is_running", False) for bridge in self._bridges_by_space.values())

    def set_debug_mode(self, enabled: bool) -> None:
        for bridge in self._bridges_by_space.values():
            if hasattr(bridge, "debug_mode"):
                bridge.debug_mode = enabled

    def status(self) -> List[Dict[str, object]]:
        self._apply_legacy_space_defaults()
        payload: List[Dict[str, object]] = []
        for persona in list_personas(self.registry.data_dir):
            configured_space = normalize_space_id(persona.space_id)
            running_bridge = self.get_bridge(persona.id)
            payload.append(
                {
                    "agent_id": persona.id,
                    "space_id": configured_space,
                    "space_name": getattr(running_bridge, "space_name", None),
                    "bridge_enabled": bool(persona.bridge_enabled),
                    "is_running": bool(running_bridge and getattr(running_bridge, "is_running", False)),
                }
            )
        return payload
