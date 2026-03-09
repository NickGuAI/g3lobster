# Gemini CLI Integration and Persona Configuration

g3lobster uses the Gemini CLI as its LLM backend. Each agent spawns a fresh Gemini CLI subprocess per prompt, with persona and memory injected into the prompt text.

## How Gemini CLI Is Invoked

### Command Construction

Each prompt is executed as a headless subprocess:

```
gemini [base_args] -p "<full_prompt>" [--allowed-mcp-server-names <server1> <server2> ...]
```

**Components:**
- `gemini` — CLI binary (configurable via `config.gemini.command`)
- `-y` — default base arg (auto-confirm, configurable via `config.gemini.args`)
- `-p` — headless prompt mode (no interactive UI)
- `--allowed-mcp-server-names` — MCP server filtering (omitted when wildcard `*`)

### Process Lifecycle

`GeminiProcess` in `cli/process.py` manages subprocess spawning:

```python
# Simplified flow
proc = asyncio.create_subprocess_exec(
    *cmd,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    env={**os.environ, "G3LOBSTER_AGENT_ID": agent_id, "G3LOBSTER_SESSION_ID": session_id},
    cwd=workspace_dir,
)
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
return stdout.decode("utf-8", errors="replace").strip()
```

**Key details:**
- Each `ask()` call spawns a **new process** (no persistent sessions)
- Environment variables `G3LOBSTER_AGENT_ID` and `G3LOBSTER_SESSION_ID` are injected for delegation MCP context
- Default timeout: 120 seconds (configurable per task)
- Working directory: `config.gemini.workspace_dir` (default `.`)

### Per-Agent Model Selection

When an agent's `persona.model` differs from the default `"gemini"`, the `--model` flag is added dynamically:

```python
# In process_factory (main.py)
if model_name.lower() != "gemini" and "--model" not in args:
    args.extend(["--model", model_name])
```

This allows different agents to use different models (e.g., `gemini-2.5-pro`, `gemini-2.5-flash`).

## Response Parsing

Gemini CLI outputs plain text in `-p` mode. The output goes through a two-stage cleaning pipeline in `cli/parser.py`:

### Stage 1: `clean_text(raw_output)`

Removes CLI artifacts:
- ANSI escape sequences (`\x1b[...`)
- Box-drawing characters (`╭─┌├` etc.)
- Braille progress indicators
- UI clutter ("Prioritizing...", "Type your message", model selection lines, context warnings)

### Stage 2: `strip_reasoning(text)`

If extended thinking is enabled, the output may contain a `✦` separator between reasoning and response. This stage splits on `✦` and returns only the response portion.

```python
parsed = strip_reasoning(clean_text(raw_output))
```

## Persona Configuration

### The SOUL.md File

Each agent's personality and behavior is defined in `SOUL.md`:

```
data/agents/{agent_id}/
├── agent.json     # Metadata (name, emoji, model, mcp_servers, etc.)
└── SOUL.md        # System prompt — the agent's "soul"
```

The soul is injected as the `# Agent Persona` section of every prompt via `ContextBuilder`.

### AgentPersona Fields

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `id` | str | (required) | Slug identifier (`my-agent`) |
| `name` | str | (required) | Display name (`My Agent`) |
| `emoji` | str | `"🤖"` | Chat message prefix |
| `soul` | str | `""` | System prompt (loaded from SOUL.md) |
| `model` | str | `"gemini"` | LLM model for this agent |
| `mcp_servers` | List[str] | `["*"]` | MCP tool access filter |
| `bot_user_id` | Optional[str] | `None` | Google Chat bot binding |
| `enabled` | bool | `True` | Whether agent can be started |

### Creating an Agent

**Via REST API:**
```bash
curl -X POST http://localhost:20001/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Luna",
    "emoji": "🦀",
    "soul": "You are Luna, a helpful research assistant.",
    "model": "gemini-2.5-pro",
    "mcp_servers": ["*"]
  }'
```

**Via setup wizard:** Step 3 of the UI at `/ui`.

### ID Generation

Agent IDs are slugified from names:
- `"My Agent"` → `"my-agent"`
- `"Luna 2.0"` → `"luna-2-0"`
- If taken: appends `-2`, `-3`, etc.
- Pattern: `^[a-z0-9]+(?:-[a-z0-9]+)*$`
- Reserved: `"global"`

## MCP Server Configuration

### Per-Agent MCP Filtering

Each agent declares which MCP servers it can access via `mcp_servers`:

- `["*"]` — access all available MCP servers (default)
- `["gmail", "slack"]` — access only named servers
- When not `*`, the `--allowed-mcp-server-names` flag is passed to Gemini CLI

### MCP Server Definitions

MCP servers are configured as YAML files in `config/mcp/`:

```yaml
# config/mcp/gmail.yaml
name: gmail
enabled: true
tool_patterns:
  - find.*
  - search.*
command: python
args:
  - -m
  - gmail_mcp.server
```

The `MCPManager` loads these at startup and resolves agent-requested servers against available ones.

### Delegation MCP Server

g3lobster auto-registers a delegation MCP server that lets agents call each other:

```json
// .gemini/settings.json (auto-generated)
{
  "mcpServers": {
    "g3lobster-delegation": {
      "command": "python",
      "args": ["-m", "g3lobster.mcp.delegation_server", "--base-url", "http://127.0.0.1:20001"]
    }
  }
}
```

This exposes two tools to all agents:
- `delegate_to_agent(agent_id, task, timeout_s)` — call another agent
- `list_agents()` — discover available agents

## Prompt Assembly

When an agent receives a task, `ContextBuilder.build()` creates the full prompt:

```
# G3Lobster Agent Environment
File paths, session info, cron task structure...

# Agent Persona
<SOUL.md content>

# Available Agents for Delegation
- 🦀 Luna (id: `luna`): Research assistant
- 📊 DataBot (id: `data-bot`): Data analysis

# User Preferences
<USER.md content>

# Agent Memory
<MEMORY.md content>

# Known Procedures
## Deploy Application
Trigger: deploy app
Steps: 1. Check git status  2. Run tests  3. Push

# Compaction Summary
- Previously discussed deployment strategy...

# Recent Conversation
user: How's the project going?
assistant: All tests passing, ready for review.

# New User Prompt
<current user message>
```

## Compaction and Gemini

The memory compaction engine also uses Gemini CLI — but **synchronously** via `subprocess.run()`:

```python
command = [gemini_command, *gemini_args, "-p", summarization_prompt]
result = subprocess.run(command, capture_output=True, text=True, timeout=gemini_timeout_s)
```

Summaries are normalized to 2-3 bullet points. If Gemini is unavailable, a metadata-only fallback is used.

## Configuration

```yaml
gemini:
  command: gemini               # CLI binary path
  args: ["-y"]                  # Base CLI arguments
  workspace_dir: "."            # Working directory for CLI
  response_timeout_s: 120.0     # Max seconds per prompt
  idle_read_window_s: 0.6       # Buffer read window

mcp:
  config_dir: "./config/mcp"    # MCP server YAML directory
```

**Environment overrides:**
```bash
G3LOBSTER_GEMINI_COMMAND=/usr/local/bin/gemini
G3LOBSTER_GEMINI_ARGS="-y,--model,gemini-2.5-pro"
G3LOBSTER_GEMINI_RESPONSE_TIMEOUT_S=180
G3LOBSTER_GEMINI_WORKSPACE_DIR=/workspace
```
