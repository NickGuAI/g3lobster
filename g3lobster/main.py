"""Entrypoint for g3lobster standalone service."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Optional, Sequence

import uvicorn

from g3lobster.agents.registry import AgentRegistry
from g3lobster.api.server import create_app
from g3lobster.chat.bridge import ChatBridge
from g3lobster.cli.process import GeminiProcess
from g3lobster.config import AppConfig, load_config
from g3lobster.infra.events import AgentEventEmitter
from g3lobster.memory.context import ContextBuilder
from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.manager import MemoryManager
from g3lobster.mcp.loader import MCPConfigLoader
from g3lobster.mcp.manager import MCPManager
from g3lobster.pool.agent import GeminiAgent

logger = logging.getLogger(__name__)


def build_runtime(config: AppConfig):
    mcp_loader = MCPConfigLoader(config_dir=config.mcp.config_dir)
    mcp_manager = MCPManager(loader=mcp_loader)
    global_memory_manager = GlobalMemoryManager(config.agents.data_dir)
    emitter = AgentEventEmitter(events_dir=Path(config.agents.data_dir) / "agents")

    def process_factory(model_name: str) -> GeminiProcess:
        args = list(config.gemini.args)
        normalized_model = (model_name or "").strip()
        if normalized_model and normalized_model.lower() != "gemini" and "--model" not in args:
            args.extend(["--model", normalized_model])

        return GeminiProcess(
            command=config.gemini.command,
            args=args,
            cwd=config.gemini.workspace_dir,
            idle_read_window_s=config.gemini.idle_read_window_s,
        )

    def agent_factory(persona, memory_manager: MemoryManager, context_builder: ContextBuilder) -> GeminiAgent:
        return GeminiAgent(
            agent_id=persona.id,
            process_factory=lambda: process_factory(persona.model),
            mcp_manager=mcp_manager,
            memory_manager=memory_manager,
            context_builder=context_builder,
            default_mcp_servers=persona.mcp_servers or config.mcp.default_servers,
            emitter=emitter,
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
        emitter=emitter,
    )

    chat_auth_dir = str(Path(config.agents.data_dir) / "chat_auth")

    def chat_bridge_factory(
        service=None,
        last_message_time=None,
        seen_content=None,
    ) -> ChatBridge:
        return ChatBridge(
            registry=registry,
            space_id=config.chat.space_id,
            poll_interval_s=config.chat.poll_interval_s,
            service=service,
            space_name=config.chat.space_name,
            last_message_time=last_message_time,
            seen_content=seen_content,
            auth_data_dir=chat_auth_dir,
        )

    chat_bridge = chat_bridge_factory() if config.chat.enabled else None

    return registry, chat_bridge, chat_bridge_factory, chat_auth_dir, global_memory_manager, emitter


def build_app(config_path: Optional[str] = None):
    resolved_config_path = Path(config_path or "config.yaml").expanduser().resolve()
    config = load_config(str(resolved_config_path))
    registry, chat_bridge, chat_bridge_factory, chat_auth_dir, global_memory_manager, emitter = build_runtime(config)
    app = create_app(
        registry=registry,
        chat_bridge=chat_bridge,
        chat_bridge_factory=chat_bridge_factory,
        config=config,
        config_path=str(resolved_config_path),
        chat_auth_dir=chat_auth_dir,
        global_memory_manager=global_memory_manager,
        emitter=emitter,
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
    port = args.port or config.server.port

    logger.info("Starting g3lobster on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level=args.log_level)


if __name__ == "__main__":
    main()
