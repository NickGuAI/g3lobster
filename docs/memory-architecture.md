# Memory Architecture

g3lobster uses a layered memory system that gives each agent persistent knowledge, learned procedures, and conversation continuity — while sharing cross-agent user preferences and global procedures.

## Directory Layout

```
data/
├── .memory/                          # Global (cross-agent) memory
│   ├── USER.md                       # Shared user preferences
│   ├── PROCEDURES.md                 # Global procedures (all agents)
│   ├── users/
│   │   └── {user_id}/
│   │       └── USER.md               # Per-user preferences
│   └── knowledge/
│       └── *.md                      # Custom knowledge files
│
└── agents/{agent_id}/
    ├── .memory/
    │   ├── MEMORY.md                 # Agent short-term memory
    │   ├── PROCEDURES.md             # Agent-specific permanent procedures
    │   ├── CANDIDATES.json           # Candidate procedures (learning)
    │   ├── associations.jsonl            # Association graph edges
    │   └── daily/
    │       ├── YYYY-MM-DD.md         # Long-term daily notes
    │       └── YYYY-MM-DD.jsonl      # Structured journal entries
    └── sessions/
        └── {session_id}.jsonl        # Append-only session transcripts
```

## Layers

### 1. SessionStore — Conversation Transcripts

Each conversation is stored as a JSONL file (one JSON object per line). Two entry types exist:

**Message entry:**
```json
{"type": "message", "timestamp": "2026-02-14T12:30:00+00:00", "message": {"role": "user", "content": "..."}, "metadata": {"task_id": "..."}}
```

**Compaction entry** (inserted when old messages are summarized):
```json
{"type": "compaction", "timestamp": "2026-02-14T12:40:00+00:00", "summary": "- bullet 1\n- bullet 2", "compacted_messages": 30, "kept_messages": 8}
```

Session IDs are sanitized for filesystem safety — non-alphanumeric characters become underscores.

All writes use atomic temp-file + `os.replace()` to prevent corruption. Per-session `RLock`s serialize concurrent appends.

**Key class:** `SessionStore` in `memory/sessions.py`

### 2. MemoryManager — Per-Agent Memory

Each agent has a `MemoryManager` that orchestrates:

- **MEMORY.md** — Short-term accumulated facts, trimmed to `memory_max_sections` (default 50) newest `##` sections.
- **PROCEDURES.md** — Permanent procedures extracted from conversation patterns.
- **Daily notes** — Long-term archive under `.memory/daily/`.
- **Session management** — Delegates to `SessionStore` for transcript I/O.

When a session exceeds `compact_threshold` messages (default 40), the `CompactionEngine` kicks in:

```
Session reaches 40 messages
  → Keep newest 25% (compact_keep_ratio = 0.25)
  → Summarize oldest 75% via Gemini CLI in chunks of 10
  → Write compaction record + kept messages back to JSONL
  → Append summary highlights to MEMORY.md
  → Extract procedure candidates from compacted messages
```

**Key class:** `MemoryManager` in `memory/manager.py`

### 3. Procedure System — Learning from Patterns

Procedures are reusable step-by-step workflows extracted from conversations. They follow a three-stage lifecycle:

```
candidate (weight < 3)  →  usable (3 ≤ weight < 10)  →  permanent (weight ≥ 10)
```

**CandidateStore** (`CANDIDATES.json`):
- Stores procedures still being learned
- On each observation: apply 30-day half-life decay to old weight, then add +1.0
- Once weight crosses 10.0, promote to PROCEDURES.md

**ProcedureStore** (`PROCEDURES.md`):
- Permanent procedures — human-curated or heavily-used automated extracts
- Never decay
- Matched against user queries via token overlap + sequence similarity (threshold ≥ 0.45)

**Procedure format in PROCEDURES.md:**
```markdown
## Deploy App
Trigger: deploy production
Weight: 10.5
Status: permanent

Steps:
1. Check git status
2. Run test suite
3. Push to main
```

**Key classes:** `Procedure`, `ProcedureStore`, `CandidateStore` in `memory/procedures.py`

### 4. Journal System — Salience-Classified Entries

The journal layer adds structured, salience-classified entries on top of the existing daily notes. Each entry has metadata (salience, tags, session origin) and participates in an association graph.

**Salience levels** (search ranking multipliers):

| Level | Weight | Description |
|-------|--------|-------------|
| critical | 5× | Must-not-forget facts |
| high | 3× | User preferences, important decisions |
| normal | 1× | Standard observations |
| low | 0.5× | Chitchat, acknowledgments |
| noise | 0.1× | Effectively hidden from search |

**Storage:** JSONL files at `daily/YYYY-MM-DD.jsonl` alongside the existing `.md` files. Each line is a JSON object:

```json
{"id": "uuid", "timestamp": "ISO-8601", "content": "...", "salience": "normal", "tags": ["compaction"], "source_session": "abc", "associations": []}
```

**Association graph:** Stored in `.memory/associations.jsonl` with edges `{source_id, target_id, relation_type, weight}`. Auto-linked when entries share tags. Supports BFS traversal up to configurable depth.

**Backward compatibility:** `append_journal_entry()` also writes a human-readable line to the `.md` daily note. Existing `.md` files continue to work — they are treated as `normal`-salience unlinked entries by the search engine.

**API endpoints:**
- `GET /agents/{id}/journal` — Query with salience/date/tag filters
- `POST /agents/{id}/journal` — Create a manual entry
- `GET /agents/{id}/journal/{entry_id}/associations` — Graph traversal

**Key classes:** `SalienceLevel`, `JournalEntry`, `JournalStore`, `AssociationGraph` in `memory/journal.py`

### 5. GlobalMemoryManager — Cross-Agent Memory

Manages shared state under `data/.memory/`:

| File | Purpose |
|------|---------|
| `USER.md` | Global user preferences (fallback for all agents) |
| `users/{id}/USER.md` | Per-user preferences (overrides global) |
| `PROCEDURES.md` | Global procedures shared across all agents |
| `knowledge/*.md` | Custom knowledge files |

User IDs are sanitized (`re.sub(r"[^a-zA-Z0-9_.-]", "_", user_id)`) to prevent path traversal.

**Key class:** `GlobalMemoryManager` in `memory/global_memory.py`

### 6. ContextBuilder — Prompt Assembly

When an agent receives a task, `ContextBuilder.build()` assembles the full prompt from all memory layers:

```
┌─────────────────────────────────────────┐
│ 1. Structure preamble (file paths)      │
│ 2. Agent persona (SOUL.md)              │
│ 3. Available agents for delegation      │
│ 4. User preferences (USER.md)          │
│ 5. Agent memory (MEMORY.md)            │
│ 6. Matched procedures (top 3)          │
│ 7. Latest compaction summary           │
│ 8. Recent conversation (last N msgs)   │
│ 9. New user prompt                     │
└─────────────────────────────────────────┘
```

The `context_messages` config (default 12) controls how many recent messages are included.

**Key class:** `ContextBuilder` in `memory/context.py`

## Configuration

All memory settings live in the `agents` section of `config.yaml`:

```yaml
agents:
  compact_threshold: 40        # Messages before compaction triggers
  compact_keep_ratio: 0.25     # Keep 25%, summarize 75%
  compact_chunk_size: 10       # Gemini summarizes in chunks of 10
  procedure_min_frequency: 3   # Min observations before extraction
  memory_max_sections: 50      # Max ## sections in MEMORY.md
  context_messages: 12         # Recent messages in prompt context
  journal_salience_default: normal  # Default salience for new entries
  journal_association_decay_days: 90  # Days before old associations fade
```

Environment overrides: `G3LOBSTER_AGENTS_COMPACT_THRESHOLD=60`, etc.

## Data Flow

```
User message arrives
    │
    ▼
MemoryManager.append_message(session_id, "user", text)
    │
    ├──▶ SessionStore appends to {session_id}.jsonl
    │
    ├──▶ Message count ≥ threshold?
    │       │
    │       ▼  YES
    │    CompactionEngine.maybe_compact()
    │       ├── Summarize old messages (Gemini CLI)
    │       ├── Rewrite JSONL (compaction record + kept messages)
    │       ├── Append highlights to MEMORY.md
    │       └── Extract + ingest procedure candidates
    │
    ▼
ContextBuilder.build(session_id, prompt)
    ├── Read MEMORY.md, USER.md, PROCEDURES.md
    ├── Match procedures against query
    ├── Read last N session messages
    └── Assemble full prompt string
    │
    ▼
Send to Gemini CLI → get response
    │
    ▼
MemoryManager.append_message(session_id, "assistant", response)
```

## Thread Safety

| Component | Lock Type | Scope |
|-----------|-----------|-------|
| SessionStore | Per-session RLock | Serializes appends to same session |
| MemoryManager | threading.Lock | Protects MEMORY.md writes |
| GlobalMemoryManager | threading.Lock | Protects PROCEDURES.md writes |
| CronStore | Atomic file ops | tempfile + os.replace per write |
