"""Guided setup routes for Google Chat-first runtime."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from g3lobster.agents.persona import list_personas
from g3lobster.api.models import (
    AgentBridgeStatus,
    CompleteAuthRequest,
    CredentialsUploadRequest,
    SetupStatus,
    SpaceConfigRequest,
)
from g3lobster.chat.auth import (
    complete_authorization,
    create_authorization_url,
    credentials_exist,
    get_authenticated_service,
    save_credentials_json,
    token_exists,
)
from g3lobster.config import save_chat_config

router = APIRouter(prefix="/setup", tags=["setup"])


def _bridge_running(bridge) -> bool:
    if not bridge:
        return False
    return bridge.is_running


def _status_payload(request: Request) -> SetupStatus:
    config = request.app.state.config
    chat_auth_dir = request.app.state.chat_auth_dir
    bridge_manager = getattr(request.app.state, "bridge_manager", None)

    credentials_ok = credentials_exist(chat_auth_dir)
    auth_ok = token_exists(chat_auth_dir)
    bridge_enabled = bool(config.chat.enabled)
    agents_ready = bool(list_personas(config.agents.data_dir))

    agent_bridges: list[AgentBridgeStatus] = []
    if bridge_manager:
        agent_bridges = [AgentBridgeStatus(**item) for item in bridge_manager.status()]
        bridge_running = any(item.is_running for item in agent_bridges)
        space_configured = any(bool(item.space_id) for item in agent_bridges) or bool(config.chat.space_id)
    else:
        bridge_running = _bridge_running(request.app.state.chat_bridge)
        space_configured = bool(config.chat.space_id)

    completed = all(
        [
            credentials_ok,
            auth_ok,
            space_configured,
            bridge_enabled,
            bridge_running,
            agents_ready,
        ]
    )

    return SetupStatus(
        credentials_ok=credentials_ok,
        auth_ok=auth_ok,
        space_configured=space_configured,
        bridge_enabled=bridge_enabled,
        bridge_running=bridge_running,
        agents_ready=agents_ready,
        completed=completed,
        agent_bridges=agent_bridges,
        space_id=config.chat.space_id,
        space_name=config.chat.space_name,
        email_enabled=config.email.enabled,
        email_base_address=config.email.base_address,
        email_poll_interval_s=config.email.poll_interval_s,
        debug_mode=config.debug_mode,
    )


@router.get("/status", response_model=SetupStatus)
async def setup_status(request: Request) -> SetupStatus:
    return _status_payload(request)


@router.post("/credentials")
async def setup_credentials(payload: CredentialsUploadRequest, request: Request) -> dict:
    save_credentials_json(payload.credentials, request.app.state.chat_auth_dir)
    return {"saved": True}


@router.get("/test-auth")
async def test_auth(request: Request, force: bool = False) -> dict:
    chat_auth_dir = request.app.state.chat_auth_dir

    if not force and token_exists(chat_auth_dir):
        try:
            _ = get_authenticated_service(chat_auth_dir)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"authenticated": True}

    if not credentials_exist(chat_auth_dir):
        raise HTTPException(status_code=400, detail="Upload credentials.json first")

    try:
        auth_url = create_authorization_url(chat_auth_dir)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"authenticated": False, "auth_url": auth_url}


@router.post("/complete-auth")
async def complete_auth(payload: CompleteAuthRequest, request: Request) -> dict:
    try:
        complete_authorization(request.app.state.chat_auth_dir, payload.code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"authenticated": True}


def _normalize_space_id(raw: str) -> str:
    """Accept 'space/XXX' (from browser URL) or 'spaces/XXX' (API format)."""
    raw = raw.strip()
    if raw.startswith("space/") and not raw.startswith("spaces/"):
        raw = "spaces/" + raw[len("space/"):]
    if not raw.startswith("spaces/"):
        raw = "spaces/" + raw
    return raw


@router.post("/space")
async def configure_space(payload: SpaceConfigRequest, request: Request) -> dict:
    config = request.app.state.config
    config.chat.space_id = _normalize_space_id(payload.space_id)
    config.chat.space_name = payload.space_name
    bridge_manager = getattr(request.app.state, "bridge_manager", None)
    if bridge_manager:
        bridge_manager.set_legacy_space_id(config.chat.space_id)
    save_chat_config(config.chat, request.app.state.config_path)
    return {"configured": True, "space_id": config.chat.space_id}


@router.get("/space-bots")
async def list_space_bots(request: Request, space_id: Optional[str] = None) -> dict:
    """List bot members of the configured space so users can grab bot_user_id."""
    config = request.app.state.config
    chat_auth_dir = request.app.state.chat_auth_dir

    target_space = space_id or config.chat.space_id
    if not target_space:
        raise HTTPException(status_code=400, detail="Configure a chat space first (step 2)")
    if not token_exists(chat_auth_dir):
        raise HTTPException(status_code=400, detail="Complete OAuth first (step 1)")

    space_id = _normalize_space_id(target_space)

    try:
        service = get_authenticated_service(chat_auth_dir)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Auth error: {exc}") from exc

    try:
        result = await asyncio.to_thread(
            service.spaces().members().list(
                parent=space_id,
                filter='member.type = "BOT"',
                pageSize=100,
            ).execute
        )
    except Exception as exc:
        msg = str(exc) or repr(exc)
        raise HTTPException(status_code=400, detail=f"Failed to list space members: {msg}") from exc

    bots = []
    for member in result.get("memberships", []):
        m = member.get("member", {})
        if m.get("type") == "BOT":
            bots.append({
                "user_id": m.get("name", ""),
                "display_name": m.get("displayName", ""),
            })

    return {"bots": bots}


@router.post("/start")
async def start_bridge(request: Request, agent_id: Optional[str] = None) -> dict:
    config = request.app.state.config
    bridge_manager = getattr(request.app.state, "bridge_manager", None)

    if bridge_manager:
        async with request.app.state.bridge_lock:
            if agent_id:
                started = await bridge_manager.start_bridge(agent_id)
                if not started:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Bridge is not configured for agent '{agent_id}'",
                    )
            else:
                started_count = await bridge_manager.start_all()
                if started_count == 0:
                    raise HTTPException(
                        status_code=400,
                        detail="No bridge-enabled agents with configured space_id were found",
                    )

            config.chat.enabled = True
            save_chat_config(config.chat, request.app.state.config_path)

        return {"started": True}

    if not config.chat.space_id:
        raise HTTPException(status_code=400, detail="Configure a chat space first")

    async with request.app.state.bridge_lock:
        old_bridge = request.app.state.chat_bridge
        if _bridge_running(old_bridge):
            config.chat.enabled = True
            save_chat_config(config.chat, request.app.state.config_path)
            return {"started": True}

        factory = request.app.state.chat_bridge_factory
        if not factory:
            raise HTTPException(status_code=500, detail="Chat bridge factory is unavailable")

        if old_bridge:
            service = getattr(old_bridge, "service", None)
            last_message_time = getattr(old_bridge, "_last_message_time", None)
            seen_content = set(getattr(old_bridge, "_seen_content", set()))
        else:
            service = None
            last_message_time = None
            seen_content = None

        try:
            bridge = factory(
                space_id=config.chat.space_id,
                service=service,
                last_message_time=last_message_time,
                seen_content=seen_content,
            )
        except TypeError:
            bridge = factory(
                service=service,
                last_message_time=last_message_time,
                seen_content=seen_content,
            )
        try:
            await bridge.start()
        except Exception as exc:
            try:
                await bridge.stop()
            except Exception:
                pass
            raise HTTPException(status_code=400, detail=f"Failed to start chat bridge: {exc}") from exc

        if old_bridge and old_bridge is not bridge:
            try:
                await old_bridge.stop()
            except Exception:
                pass

        request.app.state.chat_bridge = bridge
        config.chat.enabled = True
        save_chat_config(config.chat, request.app.state.config_path)

    return {"started": True}


@router.post("/stop")
async def stop_bridge(request: Request, agent_id: Optional[str] = None) -> dict:
    config = request.app.state.config
    bridge_manager = getattr(request.app.state, "bridge_manager", None)

    if bridge_manager:
        async with request.app.state.bridge_lock:
            if agent_id:
                await bridge_manager.stop_bridge(agent_id)
                config.chat.enabled = bridge_manager.is_running
            else:
                await bridge_manager.stop_all()
                config.chat.enabled = False
            save_chat_config(config.chat, request.app.state.config_path)
        return {"stopped": True}

    async with request.app.state.bridge_lock:
        bridge = request.app.state.chat_bridge
        if bridge:
            await bridge.stop()
            request.app.state.chat_bridge = None

        config.chat.enabled = False
        save_chat_config(config.chat, request.app.state.config_path)

    return {"stopped": True}


@router.post("/debug-mode")
async def toggle_debug_mode(request: Request) -> dict:
    """Toggle global debug mode on/off."""
    config = request.app.state.config
    config.debug_mode = not config.debug_mode

    bridge_manager = getattr(request.app.state, "bridge_manager", None)
    if bridge_manager:
        bridge_manager.set_debug_mode(config.debug_mode)
    else:
        bridge = request.app.state.chat_bridge
        if bridge:
            bridge.debug_mode = config.debug_mode

    return {"debug_mode": config.debug_mode}
