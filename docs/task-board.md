# Task Board

The task board is a per-agent Kanban system. Each agent maintains its own board of work items, manages them autonomously via MCP tools during execution and heartbeat check-ins, and exposes them through the web UI and REST API.

---

## Task Lifecycle

```
         create_task
              │
              ▼
           [ todo ]
              │
    update_task(status=in_progress)
              │
              ▼
       [ in_progress ]
              │
         ┌────┴────┐
         │         │
  complete_task   update_task(status=blocked)
         │         │
         ▼         ▼
    [ completed ] [ blocked ]
                      │
              update_task (unblock)
                      │
                      ▼
               [ in_progress ]
```

**Statuses:**

| Status | Meaning |
|--------|---------|
| `todo` | Created, not yet started |
| `in_progress` | Agent is actively working on it |
| `blocked` | Waiting on something external |
| `completed` | Done — result recorded |
| `canceled` | Abandoned |

---

## Task Schema

```json
{
  "id": "task-abc123",
  "agent_id": "athena",
  "title": "Summarize Q1 reports",
  "type": "research",
  "priority": "high",
  "status": "in_progress",
  "result": null,
  "created_at": "2026-03-17T09:00:00+00:00",
  "updated_at": "2026-03-17T09:05:00+00:00",
  "completed_at": null
}
```

**Type values:** `feature`, `bug`, `research`, `chore`, `reminder`

**Priority values:** `critical`, `high`, `normal`, `low`

Tasks are stored as JSON files under `data/agents/{id}/tasks/`.

---

## MCP Tools (Agent-Facing)

Agents interact with the task board through the `g3lobster-tasks` MCP server. These tools are always available — seeded as permanent procedures in every agent's `PROCEDURES.md` on first init.

### `list_tasks`

List tasks on the board.

```
Parameters:
  agent_id   (optional) filter by agent — defaults to self
  status     (optional) filter: todo | in_progress | blocked | completed | canceled
  limit      (optional) max results, default 20
```

### `create_task`

Add a new task to the board.

```
Parameters:
  title      (required) short description of the work
  type       (optional) feature | bug | research | chore | reminder
  priority   (optional) critical | high | normal | low
  agent_id   (optional) assign to a specific agent — defaults to self
```

### `update_task`

Change status, priority, or add a result note.

```
Parameters:
  task_id    (required) ID of the task to update
  status     (optional) new status
  priority   (optional) new priority
  result     (optional) notes on progress or blockers
```

### `complete_task`

Mark a task done and record the outcome.

```
Parameters:
  task_id    (required)
  result     (optional) summary of what was accomplished
```

### `delete_task`

Remove a task from the board.

```
Parameters:
  task_id    (required)
```

---

## REST API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/tasks` | List tasks (supports `?agent_id=`, `?status=`, `?limit=`) |
| `POST` | `/tasks` | Create a task |
| `PUT` | `/tasks/{id}` | Update a task |
| `POST` | `/tasks/{id}/complete` | Complete a task with optional result |
| `DELETE` | `/tasks/{id}` | Delete a task |

**Create request:**
```json
{"title": "Draft Q1 summary", "type": "research", "priority": "normal", "agent_id": "athena"}
```

**Complete request:**
```json
{"result": "Summary drafted and posted to #reports channel"}
```

---

## How Agents Use the Board

### During Work (task execution)

When an agent receives a prompt and starts working, the `Heartbeat Check-In` and `Managing Your Task Board` permanent procedures instruct it to:

1. Call `list_tasks` to see current state
2. Call `update_task(status=in_progress)` for tasks now being worked on
3. Call `complete_task` for tasks that are done
4. Call `create_task` for new work discovered during execution

### During Heartbeat

Every hour (default), the heartbeat loop fires. The agent:

1. Receives a prompt containing its current board, cron schedule, memory, and soul
2. Reviews the board — updates statuses for anything that changed
3. Writes a status message starting with `📋 Status Update:`
4. Posts the message to its Google Chat space

```
⏰ HEARTBEAT CHECK-IN

## Task Board
- [in_progress] Draft Q1 summary (task-abc123)
- [todo] Review engineer feedback (task-def456)

## Cron Schedule
- 0 9 * * 1-5 — Check inbox and summarize (next: Mon 09:00)

## Mission
You are Athena — research and synthesis specialist...

Review your board. Update task statuses. Write a status update
starting with 📋 Status Update:.
```

---

## Web UI

The task board is visible in the **Board** tab of the agent console at `http://localhost:20001/`. Tasks are displayed as Kanban columns:

```
 TODO          IN PROGRESS       BLOCKED          COMPLETED
┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────┐
│ Draft Q1 │  │ Review logs  │  │ API access │  │ Setup     │
│ summary  │  │ (athena)     │  │ pending    │  │ crons ✓   │
│          │  │              │  │            │  │           │
│ [+ New]  │  │              │  │            │  │           │
└──────────┘  └──────────────┘  └────────────┘  └───────────┘
```

Agent and status fields are dropdowns populated from live data — you never type these manually.

---

## Relationship to Cron

Cron jobs create ephemeral tasks when they fire:

```
Cron fires at scheduled time
  │
  └─▶ Create Task(prompt=instruction, session_id="cron__{agent_id}")
        │
        └─▶ Agent executes and updates task to completed/failed
```

These show up on the board and in run history — giving a unified view of both human-initiated and scheduled work.

---

## Storage

Tasks are stored as JSON files at:

```
data/agents/{agent_id}/tasks/{task_id}.json
```

All writes use atomic `tempfile + os.replace()` to prevent corruption.
