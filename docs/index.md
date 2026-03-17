# G3LOBSTER

**G3LOBSTER** is a Google Chat multi-agent service. You define named AI personas — each with its own memory, soul, scheduled jobs, and task board — and they live in your Google Chat spaces, responding to messages, running cron jobs, posting heartbeat updates, and delegating work to each other.

---

## What It Does

```
Google Chat Space
       │
       │  user message
       ▼
  ChatBridge (polling)
       │
       ├─▶  detect command (/cron, /memory, ...)
       │        └─▶ handle locally
       │
       └─▶  route to named agent (by @mention or concierge)
                  │
                  ├─▶  ContextBuilder assembles prompt:
                  │       soul + memory + procedures +
                  │       recent conversation + user prefs
                  │
                  ├─▶  Gemini CLI process executes
                  │
                  └─▶  response posted back to space
                         (with agent name + avatar)
```

Agents also wake up on a **heartbeat** (default every hour), review their task board and cron schedule, and post a status update to their space — without any user prompt.

---

## Quickstart

```bash
# 1. Install dependencies
make install          # creates .venv, installs deps

# 2. Copy and edit config
cp config.yaml.example config.yaml
# Set: agents.data_dir, chat.space_id, gemini.command

# 3. Run
make run              # starts FastAPI on http://0.0.0.0:20001/

# 4. Open the onboarding wizard
open http://localhost:20001/
# Upload credentials.json → OAuth → configure space → create first agent → launch
```

---

## Key Concepts

### Agent

A named persona backed by a long-lived Gemini CLI process. Each agent has:

| Component | Location | Purpose |
|-----------|----------|---------|
| `SOUL.md` | `data/agents/{id}/SOUL.md` | Character, tone, rules |
| `MEMORY.md` | `data/agents/{id}/.memory/MEMORY.md` | Accumulated facts |
| `PROCEDURES.md` | `data/agents/{id}/.memory/PROCEDURES.md` | Learned workflows |
| `sessions/` | `data/agents/{id}/sessions/` | Conversation transcripts |
| `crons.json` | `data/agents/{id}/crons.json` | Scheduled jobs |
| `tasks/` | `data/agents/{id}/tasks/` | Task board items |
| `agent.json` | `data/agents/{id}/agent.json` | Config (space, emoji, avatar, heartbeat) |

### Space

A Google Chat space. Each agent can be wired to a specific space via `space_id`. Multiple agents can share one space; each identifies itself with its emoji and name (or a card with profile picture if `avatar_url` is set).

### Task Board

A simple Kanban-like board per agent: `todo → in_progress → completed` (or `blocked`, `canceled`). Agents manage it themselves via MCP tools during work and heartbeat check-ins. Humans can view and edit via the web UI or REST API.

### Heartbeat

A periodic autonomous check-in (default: every hour). The agent wakes up, reviews its task board and cron schedule, builds a status update, and posts it to its space. No human prompt required.

### Cron

5-field cron expressions (e.g., `0 9 * * 1-5`) tied to a natural-language instruction. At fire time, the agent receives the instruction as a task and executes it.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      FastAPI Server                      │
│  /setup  /agents  /tasks  /crons  /board  /events  ...  │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
   AgentRegistry             ChatBridgeManager
   (pool of agents)          (per-agent bridges)
        │                         │
   ┌────┴────┐               ┌────┴────┐
   │GeminiAgent│             │ChatBridge│
   │  process  │◄────────────│  polling │
   └────┬─────┘              └──────────┘
        │
   ┌────┴──────────────────────────┐
   │         ContextBuilder         │
   │  soul + memory + procedures   │
   │  + session history + agents   │
   └───────────────────────────────┘
        │
   ┌────┴──────────────────────────┐
   │       MemoryManager            │
   │  MEMORY.md  PROCEDURES.md     │
   │  sessions/  compactions/      │
   └───────────────────────────────┘
```

---

## Configuration Reference

`config.yaml` (copy from `config.yaml.example`):

```yaml
agents:
  data_dir: ./data                  # Where agent files live
  compact_threshold: 40             # Messages before session compaction
  context_messages: 12              # Recent messages included in prompt
  health_check_interval_s: 30       # Agent health check frequency
  stuck_timeout_s: 0                # 0 = disabled

gemini:
  command: gemini                   # Gemini CLI binary path
  args: [-y]                        # Extra args (accept prompts)
  workspace_dir: .                  # Working dir for Gemini

mcp:
  config_dir: ./config/mcp          # MCP YAML configs
  default_servers: ['*']            # All servers by default

chat:
  enabled: true
  space_id: spaces/AAAA...          # Default Google Chat space
  poll_interval_s: 2                # How often to poll for new messages
  concierge_enabled: true           # Auto-route unaddressed messages
  concierge_agent_id: concierge

server:
  host: 0.0.0.0
  port: 20001

auth:
  enabled: false                    # API key auth (set for production)
  api_key: ''
```

All keys can be overridden via environment variables using the pattern:
`G3LOBSTER_<SECTION>_<KEY>=value`

Example: `G3LOBSTER_SERVER_PORT=8080`, `G3LOBSTER_AUTH_API_KEY=secret`

---

## REST API Overview

| Category | Base Path | Key Endpoints |
|----------|-----------|---------------|
| Setup wizard | `/setup` | `GET /status`, `POST /start`, `POST /stop` |
| Agents | `/agents` | CRUD + start/stop/restart/test/heartbeat |
| Task board | `/tasks` | CRUD + complete |
| Cron | `/agents/{id}/crons` | CRUD + run + history |
| Memory | `/agents/{id}/memory` | GET/PUT MEMORY.md |
| Procedures | `/agents/{id}/procedures` | GET/PUT PROCEDURES.md |
| Events (SSE) | `/events` | Real-time agent event stream |
| Metrics | `/agents/metrics/summary` | Aggregate stats |
| Health | `/health` | Liveness probe |

Full OpenAPI schema available at `http://localhost:20001/docs`.

---

## MCP Tools Available to Agents

Agents have three built-in MCP servers wired at startup:

| Server | Tools | Purpose |
|--------|-------|---------|
| `g3lobster-tasks` | `list_tasks`, `create_task`, `update_task`, `complete_task`, `delete_task` | Manage own task board |
| `g3lobster-cron` | `list_cron_jobs`, `create_cron_job`, `update_cron_job`, `run_cron_job`, `delete_cron_job` | Manage own cron schedule |
| `g3lobster-delegation` | `list_agents`, `delegate_task` | Delegate work to sibling agents |

These are always injected via the agent's `PROCEDURES.md` (permanent weight procedures seeded on first init).

---

## Further Reading

| Topic | Document |
|-------|---------|
| Memory system deep-dive | [memory-architecture.md](memory-architecture.md) |
| Heartbeat + Cron | [heartbeat-and-cron.md](heartbeat-and-cron.md) |
| Task board | [task-board.md](task-board.md) |
| Agent personas | [gemini-cli-and-persona.md](gemini-cli-and-persona.md) |
| Google Chat setup | [google-chat-integration.md](google-chat-integration.md) |
| Multi-agent control | [multi-agent-control.md](multi-agent-control.md) |
| Cloud Run deploy | [cloud-run-deployment.md](cloud-run-deployment.md) |
