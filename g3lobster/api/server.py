"""FastAPI app factory."""

from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

from g3lobster.api.routes_agents import router as agents_router
from g3lobster.api.routes_chat_events import router as chat_events_router
from g3lobster.api.routes_control import router as control_router
from g3lobster.api.routes_calendar import router as calendar_router
from g3lobster.api.routes_calendar_setup import router as calendar_setup_router
from g3lobster.api.routes_cron import router as cron_router
from g3lobster.api.routes_standup import router as standup_router
from g3lobster.api.routes_delegation import router as delegation_router
from g3lobster.api.routes_export import router as export_router
from g3lobster.api.routes_health import router as health_router
from g3lobster.api.routes_thinking import router as thinking_router
from g3lobster.api.routes_metrics import router as metrics_router
from g3lobster.api.routes_setup import router as setup_router
from g3lobster.api.routes_board import router as board_router
from g3lobster.api.routes_tasks import router as tasks_router
from g3lobster.api.event_bus import EventBus
from g3lobster.config import AppConfig


def create_app(
    registry,
    bridge_manager: Optional[object] = None,
    chat_bridge: Optional[object] = None,
    chat_bridge_factory=None,
    config: Optional[AppConfig] = None,
    config_path: Optional[str] = None,
    chat_auth_dir: Optional[str] = None,
    global_memory_manager: Optional[object] = None,
    cron_store: Optional[object] = None,
    cron_manager: Optional[object] = None,
    email_bridge: Optional[object] = None,
    calendar_bridge: Optional[object] = None,
    control_plane: Optional[object] = None,
    standup_store: Optional[object] = None,
    standup_orchestrator: Optional[object] = None,
    event_bus: Optional[EventBus] = None,
    board_store=None,
    sheets_sync=None,
) -> FastAPI:
    runtime_config = config or AppConfig()
    runtime_config_path = str(Path(config_path or "config.yaml").expanduser().resolve())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not runtime_config.auth.enabled or not runtime_config.auth.api_key:
            logger.warning("API authentication is disabled. Set G3LOBSTER_AUTH_API_KEY to enable.")
        await registry.start_all()
        if app.state.cron_manager:
            app.state.cron_manager.start()
        if app.state.bridge_manager and app.state.config.chat.enabled:
            await app.state.bridge_manager.start_all()
        elif app.state.chat_bridge:
            await app.state.chat_bridge.start()
        if app.state.email_bridge:
            await app.state.email_bridge.start()
        if app.state.calendar_bridge:
            await app.state.calendar_bridge.start()
        try:
            yield
        finally:
            if app.state.calendar_bridge:
                await app.state.calendar_bridge.stop()
            if app.state.email_bridge:
                await app.state.email_bridge.stop()
            if app.state.bridge_manager:
                await app.state.bridge_manager.stop_all()
            elif app.state.chat_bridge:
                await app.state.chat_bridge.stop()
            if app.state.cron_manager:
                app.state.cron_manager.stop()
            await registry.stop_all()

    app = FastAPI(title="g3lobster", lifespan=lifespan)
    app.state.registry = registry
    app.state.bridge_manager = bridge_manager
    app.state.chat_bridge = chat_bridge
    app.state.chat_bridge_factory = chat_bridge_factory
    app.state.config = runtime_config
    app.state.config_path = runtime_config_path
    app.state.bridge_lock = asyncio.Lock()
    app.state.chat_auth_dir = chat_auth_dir
    app.state.global_memory_manager = global_memory_manager
    app.state.cron_store = cron_store
    app.state.cron_manager = cron_manager
    app.state.email_bridge = email_bridge
    app.state.calendar_bridge = calendar_bridge
    app.state.control_plane = control_plane
    app.state.standup_store = standup_store
    app.state.standup_orchestrator = standup_orchestrator
    app.state._stopped_memory_managers = {}
    app.state.event_bus = event_bus or EventBus()
    app.state.board_store = board_store
    app.state.sheets_sync = sheets_sync

    _AUTH_EXEMPT_PREFIXES = ("/health", "/setup", "/chat/events", "/docs", "/openapi.json", "/ui")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if any(path == prefix or path.startswith(prefix + "/") or path == prefix for prefix in _AUTH_EXEMPT_PREFIXES):
            return await call_next(request)
        # Also exempt exact matches (e.g. /docs without trailing slash)
        if not runtime_config.auth.enabled or not runtime_config.auth.api_key:
            return await call_next(request)
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if hmac.compare_digest(token, runtime_config.auth.api_key):
                return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    app.include_router(health_router)
    app.include_router(agents_router)
    app.include_router(cron_router)
    app.include_router(setup_router)
    app.include_router(chat_events_router)
    app.include_router(delegation_router)
    app.include_router(control_router)
    app.include_router(metrics_router)
    app.include_router(export_router)
    app.include_router(standup_router)
    app.include_router(calendar_router)
    app.include_router(calendar_setup_router)
    app.include_router(thinking_router)
    app.include_router(board_router)
    app.include_router(tasks_router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

    return app
