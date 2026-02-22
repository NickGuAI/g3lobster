"""FastAPI app factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from g3lobster.api.routes_agents import router as agents_router
from g3lobster.api.routes_delegation import router as delegation_router
from g3lobster.api.routes_health import router as health_router
from g3lobster.api.routes_setup import router as setup_router
from g3lobster.config import AppConfig


def create_app(
    registry,
    chat_bridge: Optional[object] = None,
    chat_bridge_factory=None,
    config: Optional[AppConfig] = None,
    config_path: Optional[str] = None,
    chat_auth_dir: Optional[str] = None,
    global_memory_manager: Optional[object] = None,
) -> FastAPI:
    runtime_config = config or AppConfig()
    runtime_config_path = str(Path(config_path or "config.yaml").expanduser().resolve())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await registry.start_all()
        if app.state.chat_bridge:
            await app.state.chat_bridge.start()
        try:
            yield
        finally:
            if app.state.chat_bridge:
                await app.state.chat_bridge.stop()
            await registry.stop_all()

    app = FastAPI(title="g3lobster", lifespan=lifespan)
    app.state.registry = registry
    app.state.chat_bridge = chat_bridge
    app.state.chat_bridge_factory = chat_bridge_factory
    app.state.config = runtime_config
    app.state.config_path = runtime_config_path
    app.state.bridge_lock = asyncio.Lock()
    app.state.chat_auth_dir = chat_auth_dir
    app.state.global_memory_manager = global_memory_manager
    app.state._stopped_memory_managers = {}

    app.include_router(health_router)
    app.include_router(agents_router)
    app.include_router(delegation_router)
    app.include_router(setup_router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

    return app
