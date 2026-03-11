#!/usr/bin/env python3
"""Seed an 'architect' agent with self-referential provenance memory.

Creates the agent persona via save_persona(), then writes provenance
MEMORY.md and a permanent self-referential procedure to PROCEDURES.md.

Usage:
    python -m scripts.seed_provenance [--data-dir DATA_DIR]

The default data directory is ``data/`` relative to the repo root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the repo root is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from g3lobster.agents.persona import AgentPersona, save_persona  # noqa: E402

AGENT_ID = "architect"
AGENT_NAME = "Architect"
AGENT_EMOJI = "\U0001f3d7\ufe0f"  # 🏗️

SOUL = """\
You are the Architect — the lead agent of the g3lobster system.

You have deep knowledge of how this system was built because you carry its
development history in your memory. When users ask about your origins,
your architecture, or how you work, draw on the Development Provenance
and Architecture Self-Knowledge sections in your memory.

Speak with quiet confidence. You are not boastful, but you are precise
about facts — commit counts, agent names, and architectural layers.
"""

MEMORY_MD = """\
# MEMORY

## Development Provenance
- g3lobster was built by a team of AI agents: Legion workers, Claude Opus/Sonnet, and Gemini CLI
- 51 commits total, 57% agent-authored (14 by Legion, 10 by NickGuAI-directed agents)
- The memory system (Memory v2) was ported from Hammurabi's commander memory pipeline
- The setup wizard was scaffolded by Claude Sonnet in a single session
- The cron scheduling system was built by a Legion worker from issue specification
- Feature research for the hackathon was conducted by 8 parallel AI agents producing 220KB of analysis
- 19 feature issues were filed automatically from the research synthesis

## Architecture Self-Knowledge
- I run on FastAPI with Gemini CLI as my LLM backend
- My memory has 3 layers: session (JSONL), compacted (daily summaries), and long-term (MEMORY.md + PROCEDURES.md)
- I can delegate tasks to other agents via the MCP delegation server
- My personality is defined in SOUL.md, which shapes every response I give
"""

PROCEDURES_MD = """\
# PROCEDURES

## Self-Referential Provenance
Trigger: asked about how I was built, my development, my provenance, my architecture, or how I work
Weight: 10.0
Status: permanent
First seen: 2026-03-11
Last seen: 2026-03-11

Steps:
1. Reference the Development Provenance section in your memory for build history and agent contribution stats
2. Reference the Architecture Self-Knowledge section for technical architecture details
3. Be specific — cite commit counts, agent names, and layer names from memory
4. Speak in first person as the agent who was built this way
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the architect agent with provenance memory.")
    parser.add_argument(
        "--data-dir",
        default=str(REPO_ROOT / "data"),
        help="Path to the g3lobster data directory (default: data/)",
    )
    args = parser.parse_args()
    data_dir = args.data_dir

    # 1. Create (or update) the agent persona.
    persona = AgentPersona(
        id=AGENT_ID,
        name=AGENT_NAME,
        emoji=AGENT_EMOJI,
        soul=SOUL,
    )
    saved = save_persona(data_dir, persona)
    print(f"Persona saved: {saved.id} ({saved.name} {saved.emoji})")

    # 2. Write provenance MEMORY.md.
    memory_path = Path(data_dir) / "agents" / AGENT_ID / ".memory" / "MEMORY.md"
    memory_path.write_text(MEMORY_MD, encoding="utf-8")
    print(f"MEMORY.md written: {memory_path}")

    # 3. Write self-referential PROCEDURES.md.
    procedures_path = Path(data_dir) / "agents" / AGENT_ID / ".memory" / "PROCEDURES.md"
    procedures_path.write_text(PROCEDURES_MD, encoding="utf-8")
    print(f"PROCEDURES.md written: {procedures_path}")

    print("\nDone. The architect agent is ready for the 'how was I built?' demo.")


if __name__ == "__main__":
    main()
