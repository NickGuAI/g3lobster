# Heartbeat and Cron Job Support

g3lobster includes built-in health monitoring for agents and a cron scheduler for recurring tasks. Both systems run as background loops integrated with the asyncio event loop.

## Health Monitoring (Heartbeat)

### Health Loop

The `AgentRegistry` runs a background asyncio task (`g3lobster-agent-health`) that inspects all agents at regular intervals:

```
Every health_check_interval_s (default 30s):
  │
  ├─ 1. Inspect agent health
  │     HealthInspector checks each agent for:
  │     • DEAD: process exited unexpectedly
  │     • STUCK: state == BUSY for > stuck_timeout_s (default 300s)
  │
  ├─ 2. Auto-recover
  │     For each issue found:
  │     → Send alert (agent_dead or agent_stuck)
  │     → Restart agent
  │     → Send agent_restarted alert on success
  │
  ├─ 3. Check delegation timeouts
  │     Sweep SubagentRegistry for runs exceeding timeout_s
  │     → Mark as TIMED_OUT
  │     → Send delegation_timeout alert
  │
  └─ 4. Monitor ChatBridge liveness
        If bridge was running but stopped unexpectedly
        → Send bridge_stopped alert
```

### Alert System

The `AlertManager` routes health events to multiple sinks with severity filtering and rate limiting.

**Alert events and severity:**

| Event Type | Severity | Trigger |
|------------|----------|---------|
| `agent_dead` | CRITICAL | Process exited unexpectedly |
| `bridge_stopped` | CRITICAL | ChatBridge stopped unexpectedly |
| `agent_stuck` | ERROR | Agent busy > stuck_timeout_s |
| `delegation_timeout` | ERROR | Delegation run exceeded timeout |
| `agent_restarted` | WARNING | Agent auto-recovered |

**Alert sinks (fire concurrently):**

| Sink | Configuration | Delivery |
|------|--------------|----------|
| Google Chat | `alerts.chat_space_id` | Post to space via Chat API |
| Webhook | `alerts.webhook_url` | HTTP POST with JSON payload |
| Email | `alerts.email_address` | Gmail via EmailBridge |

**Rate limiting:** Per `{event_type}:{agent_id}` key, maximum one alert per `rate_limit_s` (default 300s / 5 minutes).

**Message format:**
```
🚨 *g3lobster alert* — agent_dead
Agent: `luna`
Detail: Agent luna detected as dead, restarting
Time: 2026-03-08T09:15:00+00:00
Dashboard: http://localhost:20001/api/agents
```

## Cron Job System

### CronStore — Task Storage

Cron tasks are stored as JSON files at `data/agents/{agent_id}/crons.json`. Each task:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "agent_id": "luna",
  "schedule": "0 9 * * *",
  "instruction": "Check inbox and summarize new emails",
  "enabled": true,
  "last_run": "2026-03-08T09:00:00+00:00",
  "next_run": "2026-03-09T09:00:00+00:00",
  "created_at": "2026-03-01T00:00:00+00:00"
}
```

All writes use atomic temp-file + `os.replace()`.

### CronManager — Scheduling

Wraps APScheduler's `AsyncIOScheduler` for event loop integration:

- **start()** — load all enabled tasks, start scheduler
- **stop()** — shutdown scheduler
- **reload()** — clear all jobs, re-read store, reschedule

Each enabled task is registered with a `CronTrigger` parsed from its 5-field cron expression. `misfire_grace_time=60` allows 60-second grace for missed fires.

### Task Execution Flow

```
Scheduled time reached (APScheduler)
  │
  ├─ Update task.last_run = now
  │
  ├─ Ensure agent is running
  │   → auto-start if needed
  │   → record failed run if start fails
  │
  ├─ Create Task(prompt=instruction, session_id="cron__{agent_id}")
  │
  ├─ Assign to agent and await result
  │
  └─ Record run to history
      {task_id, fired_at, status, duration_s, result_preview}
```

### Run History

Stored at `data/agents/{agent_id}/cron_history.json` as a ring buffer (last 20 runs per task):

```json
{
  "550e8400-...": [
    {
      "task_id": "550e8400-...",
      "fired_at": "2026-03-08T09:00:00+00:00",
      "status": "completed",
      "duration_s": 3.2,
      "result_preview": "Found 5 new emails..."
    }
  ]
}
```

### REST API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/agents/_cron/all` | List all enabled tasks across all agents |
| POST | `/agents/_cron/validate` | Validate cron expression, get next fire time |
| GET | `/agents/{id}/crons` | List tasks for an agent |
| POST | `/agents/{id}/crons` | Create new cron task |
| PUT | `/agents/{id}/crons/{tid}` | Update task (schedule, instruction, enabled) |
| DELETE | `/agents/{id}/crons/{tid}` | Delete task |
| POST | `/agents/{id}/crons/{tid}/run` | Manually trigger execution |
| GET | `/agents/{id}/crons/{tid}/history` | Fetch run history |

**Create request:**
```json
{"schedule": "0 9 * * *", "instruction": "Check inbox and summarize"}
```

**Validate response:**
```json
{"valid": true, "next_run": "2026-03-09T09:00:00+00:00"}
```

**Manual run response:**
```json
{"task_id": "...", "status": "completed", "duration_s": 3.2, "result_preview": "..."}
```

### Chat Slash Commands

Agents can also manage cron tasks via slash commands in Google Chat:

| Command | Example | Purpose |
|---------|---------|---------|
| `/cron list` | `/cron list` | Show all tasks with status |
| `/cron add` | `/cron add "0 9 * * *" "check email"` | Create task |
| `/cron delete` | `/cron delete 550e` | Delete by ID prefix |
| `/cron enable` | `/cron enable 550e` | Enable a disabled task |
| `/cron disable` | `/cron disable 550e` | Disable without deleting |

## Configuration

```yaml
agents:
  health_check_interval_s: 30     # Health loop frequency
  stuck_timeout_s: 300            # Seconds before "stuck" detection

cron:
  enabled: true                   # Enable/disable cron system

alerts:
  enabled: false                  # Enable alert delivery
  chat_space_id: ""               # Google Chat space for alerts
  webhook_url: ""                 # Webhook endpoint
  email_address: ""               # Admin email for alerts
  min_severity: "warning"         # Filter: warning | error | critical
  rate_limit_s: 300               # Rate limit between identical alerts
```

**Environment overrides:**
```bash
G3LOBSTER_AGENTS_HEALTH_CHECK_INTERVAL_S=60
G3LOBSTER_AGENTS_STUCK_TIMEOUT_S=600
G3LOBSTER_ALERTS_ENABLED=true
G3LOBSTER_ALERTS_WEBHOOK_URL=https://hooks.slack.com/services/...
G3LOBSTER_ALERTS_MIN_SEVERITY=error
```
