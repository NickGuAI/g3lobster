# Multi-Agent Control

g3lobster manages multiple named AI agents, each with its own persona, memory, and runtime process. The `AgentRegistry` is the central orchestrator — it handles lifecycle, health monitoring, and inter-agent delegation.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    AgentRegistry                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │ _agents: Dict[str, RegisteredAgent]               │  │
│  │                                                    │  │
│  │  "luna"  → RegisteredAgent(persona, agent, mem)   │  │
│  │  "sonic" → RegisteredAgent(persona, agent, mem)   │  │
│  │  "nova"  → RegisteredAgent(persona, agent, mem)   │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  Health Loop ─── inspects every 30s ───▶ auto-restart   │
│  SubagentRegistry ── tracks delegation runs             │
│  AlertManager ── routes health events to sinks          │
└─────────────────────────────────────────────────────────┘
```

## Agent Lifecycle

### Create

```
POST /agents
  → slugify_agent_id(name)        # "My Agent" → "my-agent"
  → ensure_unique_agent_id()      # "my-agent-2" if taken
  → save_persona(data_dir, persona)
      writes: data/agents/{id}/agent.json
      writes: data/agents/{id}/SOUL.md
      creates: .memory/, sessions/
```

### Start

```
POST /agents/{id}/start  or  registry.start_agent(id)
  → load_persona(data_dir, id)
  → MemoryManager(agent_data_dir)
  → ContextBuilder(memory, system_preamble=persona.soul)
  → GeminiAgent(process_factory, memory, context)
  → agent.start(mcp_servers=persona.mcp_servers)
      spawns Gemini CLI subprocess
      state: STARTING → IDLE
  → store as RegisteredAgent in registry._agents
```

### Assign (Task Execution)

```
registered_agent.assign(task)
  → acquire _assign_lock (serializes concurrent tasks)
  → agent.assign(task)
      state: IDLE → BUSY
      build prompt via ContextBuilder
      record user message to session
      send to Gemini CLI
      parse response
      record assistant message to session
      state: BUSY → IDLE (or DEAD if process died)
  → release lock
  → return Task with result/error
```

### Stop / Restart / Delete

| Action | What Happens |
|--------|--------------|
| `stop_agent(id)` | Kill process, remove from registry, state → STOPPED |
| `restart_agent(id)` | Stop + Start (new process, same persona) |
| `delete_agent(id)` | Stop + delete persona directory from disk |

### Update (Hot Reload)

```
PUT /agents/{id}
  → update persona fields (name, emoji, soul, model, mcp_servers, enabled)
  → save_persona()
  → if runtime exists: update persona + system_preamble in-place
  → side effects:
      enabled: false → stop agent
      enabled: true (was stopped) → start agent
      model or mcp_servers changed → restart agent (needs new process)
```

## State Machine

```
STARTING ──▶ IDLE ──▶ BUSY ──▶ IDLE
                │        │
                │        └──▶ DEAD (process died)
                │
                └──▶ STOPPED (manual stop)

Health loop detects:
  DEAD  → auto-restart + alert
  STUCK → (BUSY > stuck_timeout_s) → auto-restart + alert
```

## Agent Persona

Each agent's identity is defined by an `AgentPersona` dataclass:

```python
@dataclass
class AgentPersona:
    id: str                          # slug: "luna", "data-bot"
    name: str                        # display name: "Luna"
    emoji: str = "🤖"               # chat prefix
    soul: str = ""                   # system prompt (from SOUL.md)
    model: str = "gemini"            # LLM model
    mcp_servers: List[str] = ["*"]   # MCP tool access
    bot_user_id: Optional[str]       # Google Chat bot binding
    enabled: bool = True
    created_at: str                  # UTC ISO 8601
    updated_at: str                  # UTC ISO 8601
```

**Storage:**
- `data/agents/{id}/agent.json` — all fields except soul
- `data/agents/{id}/SOUL.md` — the soul/system prompt

**ID rules:** lowercase alphanumeric + hyphens, validated by `^[a-z0-9]+(?:-[a-z0-9]+)*$`. Reserved: `"global"`.

## Delegation

Agents can delegate tasks to other agents via the `SubagentRegistry`:

```
Parent Agent → delegate_task(parent_id, child_id, prompt, timeout)
  → SubagentRegistry.register_run()
      status: REGISTERED
      session_id: "delegation-{uuid[:8]}"
  → ensure child agent is running (auto-start if needed)
  → mark_running(run_id)
  → child.assign(Task(prompt, session_id))
  → on success: complete_run(run_id, result)
  → on failure: fail_run(run_id, error)
  → on timeout: health loop marks TIMED_OUT
```

**Delegation MCP server:** At startup, g3lobster registers a `g3lobster-delegation` MCP server in `.gemini/settings.json` that exposes two tools to agents:
- `delegate_to_agent(agent_id, task, timeout_s)` — call another agent
- `list_agents()` — discover available agents

Delegation runs are persisted to `data/.subagent_runs.json` and queryable via REST:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/delegation/run` | Create delegation run |
| GET | `/delegation/runs/{run_id}` | Get run status/result |
| GET | `/delegation/runs` | List runs (filterable by parent) |

## REST API

### Agent Management

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/agents` | List all agents with status |
| POST | `/agents` | Create new agent |
| GET | `/agents/{id}` | Get agent detail (includes soul) |
| PUT | `/agents/{id}` | Update agent config |
| DELETE | `/agents/{id}` | Delete agent + data |
| POST | `/agents/{id}/start` | Start agent runtime |
| POST | `/agents/{id}/stop` | Stop agent |
| POST | `/agents/{id}/restart` | Restart (stop + start) |
| POST | `/agents/{id}/link-bot` | Link Google Chat bot |
| POST | `/agents/{id}/test` | Send test message to chat |

### Memory & Sessions

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/agents/{id}/memory` | Read MEMORY.md |
| PUT | `/agents/{id}/memory` | Write MEMORY.md |
| GET | `/agents/{id}/procedures` | Read PROCEDURES.md |
| PUT | `/agents/{id}/procedures` | Write PROCEDURES.md |
| GET | `/agents/{id}/sessions` | List session files |
| GET | `/agents/{id}/sessions/{sid}` | Read session transcript |

### Global Memory

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/agents/_global/user-memory` | Read shared USER.md |
| PUT | `/agents/_global/user-memory` | Write shared USER.md |
| GET | `/agents/_global/user-memory/{uid}` | Read per-user memory |
| PUT | `/agents/_global/user-memory/{uid}` | Write per-user memory |
| GET | `/agents/_global/procedures` | Read global procedures |
| PUT | `/agents/_global/procedures` | Write global procedures |
| GET | `/agents/_global/knowledge` | List knowledge files |

## Configuration

```yaml
agents:
  data_dir: "./data"
  health_check_interval_s: 30    # Health loop frequency
  stuck_timeout_s: 300           # Seconds before agent is "stuck"
  context_messages: 12           # Recent messages in context
  compact_threshold: 40          # Compaction trigger
```

## Initialization

`build_runtime()` in `main.py` wires everything together:

1. Load config from `config.yaml` + environment overrides
2. Register delegation MCP server in `.gemini/settings.json`
3. Create `MCPManager`, `GlobalMemoryManager`
4. Create `process_factory` (spawns Gemini CLI per agent)
5. Create `agent_factory` (wires process + memory + context)
6. Create `AlertManager` with configured sinks
7. Create `AgentRegistry` with factory + alerts
8. Create `CronManager`, `ChatBridge`, `EmailBridge`
9. Wire alert sinks to bridges
10. On server start: `registry.start_all()` boots all enabled agents + health loop
