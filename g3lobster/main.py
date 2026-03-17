"""Entrypoint for g3lobster standalone service."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

import uvicorn

from g3lobster.agents.registry import AgentRegistry
from g3lobster.alerts import AlertManager
from g3lobster.api.event_bus import EventBus
from g3lobster.api.server import create_app
from g3lobster.chat.bridge import ChatBridge
from g3lobster.chat.bridge_manager import BridgeManager
from g3lobster.chat.calendar_bridge import CalendarBridge
from g3lobster.chat.email_bridge import EmailBridge
from g3lobster.meeting_prep.orchestrator import MeetingPrepOrchestrator
from g3lobster.cli.process import GeminiProcess
from g3lobster.config import AppConfig, load_config
from g3lobster.control_plane import ControlPlane, Dispatcher, Orchestrator, TaskRegistry
from g3lobster.calendar.checker import FocusTimeChecker
from g3lobster.calendar.buffer import MessageBuffer
from g3lobster.cron.manager import CronManager
from g3lobster.cron.store import CronStore
from g3lobster.standup.orchestrator import StandupOrchestrator
from g3lobster.standup.store import StandupStore
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager
from g3lobster.mcp.loader import MCPConfigLoader
from g3lobster.mcp.manager import MCPManager
from g3lobster.pool.agent import GeminiAgent
from g3lobster.pool.tmux_spawn import TmuxSpawner

logger = logging.getLogger(__name__)


def _load_gemini_settings(workspace_dir: str) -> tuple[Path, dict]:
    """Load Gemini CLI workspace settings (or return an empty baseline)."""
    gemini_dir = Path(workspace_dir) / ".gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    settings_path = gemini_dir / "settings.json"

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    return settings_path, settings


def _write_gemini_settings(settings_path: Path, settings: dict) -> None:
    settings_path.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )


def _ensure_delegation_mcp_config(workspace_dir: str, server_port: int) -> None:
    """Auto-register the delegation MCP server in Gemini CLI workspace settings."""
    settings_path, settings = _load_gemini_settings(workspace_dir)
    mcp_servers = settings.setdefault("mcpServers", {})
    base_url = f"http://127.0.0.1:{server_port}"

    mcp_servers["g3lobster-delegation"] = {
        "command": sys.executable,
        "args": [
            "-m",
            "g3lobster.mcp.delegation_server",
            "--base-url",
            base_url,
        ],
    }

    _write_gemini_settings(settings_path, settings)
    logger.info(
        "Registered delegation MCP server in %s (base_url=%s)",
        settings_path,
        base_url,
    )


def _ensure_cron_mcp_config(workspace_dir: str, server_port: int) -> None:
    """Auto-register the cron MCP server in Gemini CLI workspace settings."""
    settings_path, settings = _load_gemini_settings(workspace_dir)
    mcp_servers = settings.setdefault("mcpServers", {})
    base_url = f"http://127.0.0.1:{server_port}"

    mcp_servers["g3lobster-cron"] = {
        "command": sys.executable,
        "args": [
            "-m",
            "g3lobster.mcp.cron_server",
            "--base-url",
            base_url,
        ],
    }

    _write_gemini_settings(settings_path, settings)
    logger.info(
        "Registered cron MCP server in %s (base_url=%s)",
        settings_path,
        base_url,
    )


def build_runtime(config: AppConfig):
    _ensure_delegation_mcp_config(
        workspace_dir=config.gemini.workspace_dir,
        server_port=config.server.port,
    )
    _ensure_cron_mcp_config(
        workspace_dir=config.gemini.workspace_dir,
        server_port=config.server.port,
    )

    mcp_loader = MCPConfigLoader(config_dir=config.mcp.config_dir)
    mcp_manager = MCPManager(loader=mcp_loader)
    global_memory_manager = GlobalMemoryManager(config.agents.data_dir)

    def process_factory(model_name: str, agent_id: str = "") -> GeminiProcess:
        args = list(config.gemini.args)
        normalized_model = (model_name or "").strip()
        if normalized_model and normalized_model.lower() != "gemini" and "--model" not in args:
            args.extend(["--model", normalized_model])

        return GeminiProcess(
            command=config.gemini.command,
            args=args,
            cwd=config.gemini.workspace_dir,
            idle_read_window_s=config.gemini.idle_read_window_s,
            agent_id=agent_id or None,
        )

    def agent_factory(persona, memory_manager: MemoryManager, context_builder: ContextBuilder) -> GeminiAgent:
        return GeminiAgent(
            agent_id=persona.id,
            process_factory=lambda: process_factory(persona.model, agent_id=persona.id),
            mcp_manager=mcp_manager,
            memory_manager=memory_manager,
            context_builder=context_builder,
            default_mcp_servers=persona.mcp_servers or config.mcp.default_servers,
        )

    alert_manager = AlertManager(
        enabled=config.alerts.enabled,
        chat_space_id=config.alerts.chat_space_id,
        webhook_url=config.alerts.webhook_url,
        email_address=config.alerts.email_address,
        min_severity=config.alerts.min_severity,
        rate_limit_s=config.alerts.rate_limit_s,
        server_host=config.server.host,
        server_port=config.server.port,
    )

    registry = AgentRegistry(
        data_dir=config.agents.data_dir,
        compact_threshold=config.agents.compact_threshold,
        compact_keep_ratio=config.agents.compact_keep_ratio,
        compact_chunk_size=config.agents.compact_chunk_size,
        procedure_min_frequency=config.agents.procedure_min_frequency,
        memory_max_sections=config.agents.memory_max_sections,
        gemini_command=config.gemini.command,
        gemini_args=config.gemini.args,
        gemini_timeout_s=config.gemini.response_timeout_s,
        gemini_cwd=config.gemini.workspace_dir,
        context_messages=config.agents.context_messages,
        health_check_interval_s=config.agents.health_check_interval_s,
        stuck_timeout_s=config.agents.stuck_timeout_s,
        global_memory_manager=global_memory_manager,
        agent_factory=agent_factory,
        alert_manager=alert_manager,
        queue_depth_limit=config.control_plane.queue_depth,
    )

    control_plane = None
    if config.control_plane.enabled:
        tmux_spawner = None
        if config.control_plane.tmux_enabled:
            tmux_spawner = TmuxSpawner(
                command=config.gemini.command,
                args=config.gemini.args,
                cwd=config.gemini.workspace_dir,
                session_prefix=config.control_plane.tmux_session_prefix,
                max_sessions_per_agent=config.control_plane.tmux_max_sessions_per_agent,
                idle_ttl_s=config.control_plane.tmux_idle_ttl_s,
            )

        task_registry = TaskRegistry(max_tasks=config.control_plane.max_tasks)
        dispatcher = Dispatcher(
            agent_registry=registry,
            task_registry=task_registry,
            max_queue_depth=config.control_plane.queue_depth,
            tmux_spawner=tmux_spawner,
        )
        orchestrator = Orchestrator(task_registry=task_registry, dispatcher=dispatcher)
        dispatcher.set_on_task_complete(orchestrator.on_task_complete)
        control_plane = ControlPlane(
            task_registry=task_registry,
            dispatcher=dispatcher,
            orchestrator=orchestrator,
            tmux_spawner=tmux_spawner,
        )
    registry.control_plane = control_plane

    chat_auth_dir = str(Path(config.agents.data_dir) / "chat_auth")
    cron_store = CronStore(config.agents.data_dir)

    # Calendar focus-time guard
    focus_checker = None
    message_buffer = None
    if config.calendar.enabled:
        from g3lobster.chat.auth import get_calendar_service
        try:
            calendar_service = get_calendar_service(chat_auth_dir)
            message_buffer = MessageBuffer(config.agents.data_dir)
            focus_checker = FocusTimeChecker(
                calendar_service=calendar_service,
                ttl_s=300.0,
            )
            logger.info("Calendar focus-time guard enabled")
        except Exception:
            logger.warning("Calendar service unavailable — focus guard disabled", exc_info=True)

    cron_manager = CronManager(
        cron_store=cron_store,
        registry=registry,
        consolidation_enabled=config.agents.consolidation_enabled,
        consolidation_schedule=config.agents.consolidation_schedule,
        consolidation_days_window=config.agents.consolidation_days_window,
        consolidation_stale_days=config.agents.consolidation_stale_days,
        gemini_command=config.gemini.command,
        gemini_args=config.gemini.args,
        gemini_timeout_s=config.gemini.response_timeout_s or 45.0,
        gemini_cwd=config.gemini.workspace_dir,
        focus_checker=focus_checker,
        calendar_cron_schedule=config.calendar.check_interval_cron if config.calendar.enabled else None,
    ) if config.cron.enabled else None
    event_bus = EventBus()

    # Standup conductor — must be created before chat_bridge_factory so it can be captured.
    standup_store = StandupStore(config.agents.data_dir)
    standup_orchestrator = StandupOrchestrator(
        store=standup_store,
        registry=registry,
    )

    # Wire standup orchestrator into cron manager so __standup_*  instructions
    # are intercepted instead of being sent to the agent as raw prompts.
    if cron_manager is not None:
        cron_manager._standup_orchestrator = standup_orchestrator

    def chat_bridge_factory(
        space_id: str,
        service=None,
        last_message_time=None,
        seen_content=None,
        agent_filter=None,
    ) -> ChatBridge:
        concierge_id = config.chat.concierge_agent_id if config.chat.concierge_enabled else None
        return ChatBridge(
            registry=registry,
            space_id=space_id,
            poll_interval_s=config.chat.poll_interval_s,
            service=service,
            space_name=config.chat.space_name,
            last_message_time=last_message_time,
            seen_content=seen_content,
            auth_data_dir=chat_auth_dir,
            cron_store=cron_store,
            standup_orchestrator=standup_orchestrator,
            debug_mode=config.debug_mode,
            agent_filter=agent_filter,
            concierge_agent_id=concierge_id,
            debounce_window_ms=config.chat.debounce_window_ms,
            event_bus=event_bus,
            focus_checker=focus_checker,
            message_buffer=message_buffer,
            stream_update_interval_s=config.chat.stream_update_interval_s,
        )

    bridge_manager = BridgeManager(
        registry=registry,
        bridge_factory=chat_bridge_factory,
        legacy_space_id=config.chat.space_id,
    )

    email_bridge: EmailBridge | None = None
    if config.email.enabled and config.email.base_address:
        email_bridge = EmailBridge(
            registry=registry,
            base_address=config.email.base_address,
            poll_interval_s=config.email.poll_interval_s,
            auth_data_dir=config.email.auth_data_dir,
        )

    calendar_bridge: CalendarBridge | None = None
    if config.calendar.enabled:
        calendar_bridge = CalendarBridge(
            lookahead_minutes=config.calendar.lookahead_minutes,
            poll_interval_s=config.calendar.poll_interval_s,
            max_attendees=config.calendar.max_attendees,
            auth_data_dir=config.calendar.auth_data_dir,
        )

        # Wire calendar -> orchestrator -> Chat DM delivery.
        from g3lobster.memory.search import MemorySearchEngine

        memory_search = MemorySearchEngine(data_dir=config.agents.data_dir)
        # Reuse the Gmail service from the email bridge if available.
        gmail_service = None  # set below after email_bridge is checked

        meeting_prep = MeetingPrepOrchestrator(
            memory_search=memory_search,
        )

        async def _on_meeting(meeting) -> None:
            """Prepare a briefing and deliver it via Chat DM."""
            # Lazily attach Gmail service from email bridge if available.
            if email_bridge and email_bridge.service and not meeting_prep.email_service:
                meeting_prep.email_service = email_bridge.service

            briefing = await meeting_prep.prepare(meeting)
            # Deliver via the first running chat bridge.
            for bridge_obj in bridge_manager._bridges_by_space.values():
                if getattr(bridge_obj, "is_running", False):
                    await bridge_obj.send_dm(briefing)
                    break
            else:
                logger.info("CalendarBridge: no running chat bridge to deliver briefing for %r", meeting.title)

        calendar_bridge.set_on_meeting(_on_meeting)

    # Wire alert manager sinks that depend on runtime objects created above.
    if email_bridge:
        alert_manager.email_bridge = email_bridge
    registry.chat_bridge = bridge_manager

    # Task board
    board_store = None
    sheets_sync = None
    if config.tasks.enabled:
        from g3lobster.board.store import BoardStore
        board_store = BoardStore(data_dir=config.agents.data_dir)

        if config.tasks.google_sheet_id:
            from g3lobster.board.sheets import SheetsSync
            sheets_sync = SheetsSync(
                sheet_id=config.tasks.google_sheet_id,
                store=board_store,
                credentials_path=config.tasks.google_credentials_path,
            )

    return (
        registry,
        bridge_manager,
        chat_bridge_factory,
        chat_auth_dir,
        global_memory_manager,
        cron_store,
        cron_manager,
        email_bridge,
        calendar_bridge,
        control_plane,
        standup_store,
        standup_orchestrator,
        event_bus,
        board_store,
        sheets_sync,
    )


def build_app(config_path: Optional[str] = None):
    resolved_config_path = Path(config_path or "config.yaml").expanduser().resolve()
    config = load_config(str(resolved_config_path))
    (
        registry,
        bridge_manager,
        chat_bridge_factory,
        chat_auth_dir,
        global_memory_manager,
        cron_store,
        cron_manager,
        email_bridge,
        calendar_bridge,
        control_plane,
        standup_store,
        standup_orchestrator,
        event_bus,
        board_store,
        sheets_sync,
    ) = build_runtime(config)
    app = create_app(
        registry=registry,
        bridge_manager=bridge_manager,
        chat_bridge_factory=chat_bridge_factory,
        config=config,
        config_path=str(resolved_config_path),
        chat_auth_dir=chat_auth_dir,
        global_memory_manager=global_memory_manager,
        cron_store=cron_store,
        cron_manager=cron_manager,
        email_bridge=email_bridge,
        calendar_bridge=calendar_bridge,
        control_plane=control_plane,
        standup_store=standup_store,
        standup_orchestrator=standup_orchestrator,
        event_bus=event_bus,
        board_store=board_store,
        sheets_sync=sheets_sync,
    )
    return app, config


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run g3lobster service")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--host", default=None, help="Override host")
    parser.add_argument("--port", type=int, default=None, help="Override port")
    parser.add_argument("--log-level", default="info", help="uvicorn log level")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    app, config = build_app(args.config)

    host = args.host or config.server.host
    port = args.port or int(os.environ.get("PORT", 0)) or config.server.port

    logger.info("Starting g3lobster on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level=args.log_level)


if __name__ == "__main__":
    main()
