# Video A: Best Vibe-Coded Application (3:00)

## Overview

Demonstrate that g3lobster — a multi-agent platform for Google Chat — was built almost entirely through conversational prompts with Claude Code. Show the live product, then replay three concrete prompt-to-feature cycles to prove the vibe-coding workflow is real.

## Pre-Recording Setup

1. g3lobster running locally: `python -m g3lobster` (server on `localhost:40000`)
2. Three agents pre-configured and running:
   - **Luna** (`luna`) — team assistant, emoji: moon-face
   - **DataBot** (`data-bot`) — analytics summarizer, emoji: bar-chart
   - **Scheduler** (`scheduler`) — cron and calendar ops, emoji: calendar
3. Each agent has at least 5 MEMORY.md entries and 1+ cron task
4. Dashboard open at `http://localhost:40000/ui` in Chrome (dark mode, 1920x1080)
5. Claude Code terminal open in the g3lobster repo root, split-pane ready
6. Google Chat space visible in a browser tab with recent agent messages
7. Pre-record all three ACT 3 demos as backup screen captures

---

## ACT 1 — The Meta Moment (0:00-0:30)

**Goal:** Hook the audience instantly — show a Claude Code prompt generating g3lobster code while the result runs live beside it.

[TIMING: 30s total]

[SCREEN: Split — left 60% Claude Code terminal, right 40% dashboard at localhost:40000/ui showing three agents with green status dots]

**Narration (0:00-0:08):**
"This is g3lobster — a multi-agent platform for Google Chat. Every feature you are about to see was built by typing plain English into Claude Code."

[SCREEN: Left pane shows a real past prompt scrolling into view: `Add a /cron add slash command that lets users create scheduled tasks directly from Google Chat using quoted arguments and shlex parsing`]

[TIMING: 8s]

**Narration (0:08-0:18):**
"One prompt like this produces a full implementation — argument parsing, cron store integration, confirmation messages — landed in a single commit."

[SCREEN: Left pane shows the resulting diff in `g3lobster/chat/commands.py` — the `_cron_add` function with `shlex.split`, `cron_store.add_task`, and the confirmation string. Highlight the key lines briefly.]

[TIMING: 10s]

**Narration (0:18-0:30):**
"And it just works. Watch."

[SCREEN: Switch to Google Chat. Type `/cron add "0 9 * * 1" "Send weekly standup summary"` in the Luna space. The response appears: checkmark, task ID, schedule, instruction. Quick cut back to the dashboard cron panel showing the new entry.]

[TIMING: 12s]

---

## ACT 2 — What Got Built (0:30-1:30)

**Goal:** Speed tour of the finished product. Prove this is a real, working system — not a toy.

[TIMING: 60s total]

### Agent Roster (0:30-0:45)

[SCREEN: Dashboard at `localhost:40000/ui` — agent list view showing Luna, DataBot, Scheduler cards with emoji, name, model, state, uptime]

**Narration (0:30-0:38):**
"Three named agents, each with its own identity. Luna handles team questions. DataBot summarizes analytics. Scheduler manages cron jobs and calendar ops."

[SCREEN: Click Luna card. Detail panel opens showing SOUL.md content, model field set to `gemini`, MCP servers list, enabled toggle, bridge status.]

[TIMING: 8s]

**Narration (0:38-0:45):**
"Every agent has a persistent soul — a SOUL.md file that defines its personality, role, and boundaries. This is the agent's identity across restarts."

[TIMING: 7s]

### Memory Panel (0:45-1:00)

[SCREEN: Click Memory tab for Luna. MEMORY.md content visible in the editor — showing sections like `## User Preferences`, `## Recent Decisions`, `## Project Context` with real entries.]

**Narration (0:45-0:52):**
"Agents remember. MEMORY.md stores short-term context — user preferences, recent decisions, project facts. It persists across sessions."

[SCREEN: Switch to Procedures tab. PROCEDURES.md visible with entries like `## Deploy to staging` with step-by-step instructions.]

**Narration (0:52-1:00):**
"PROCEDURES.md captures learned workflows. When an agent repeats a task three times, the compaction engine extracts it into a reusable procedure — automatically."

[TIMING: 8s]

### Cron and Delegation (1:00-1:30)

[SCREEN: Click Cron tab for Scheduler agent. List shows two tasks: `0 9 * * *` "Send morning briefing" (last run timestamp visible), `0 */6 * * *` "Check service health" with run history.]

**Narration (1:00-1:08):**
"Cron tasks run on standard five-field expressions — APScheduler under the hood. Agents wake up, execute, and go back to sleep."

[SCREEN: Open Sessions tab. Click a session entry. Session viewer shows a delegation exchange — Scheduler calling `delegate_task` to DataBot with prompt text, DataBot's response flowing back.]

**Narration (1:08-1:18):**
"Agents delegate to each other. Scheduler asked DataBot to pull last week's metrics — the `delegate_task` MCP tool routed the request through the control plane, DataBot executed, and the result came back in-context."

[SCREEN: Quick flash of `GET /agents` response in a terminal — JSON array with three agent objects showing id, name, emoji, state fields. Then `GET /agents/luna/memory` showing the MEMORY.md content.]

**Narration (1:18-1:30):**
"Everything is API-first. REST endpoints at `localhost:40000` — agents, memory, crons, sessions, delegation. The dashboard is just a client. You could wire up Slack, Discord, or your own frontend in an afternoon."

[TIMING: 12s]

---

## ACT 3 — The Vibe-Coding Process (1:30-2:30)

**Goal:** Three fast prompt-to-feature demos proving this was vibe-coded, not hand-written.

[TIMING: 60s total, 20s each]

### Demo 1: Adding a New Agent Persona (1:30-1:50)

[SCREEN: Claude Code terminal. Type the prompt.]

**Prompt shown on screen:**
```
Add a POST /agents endpoint that creates a new agent from a name,
emoji, and soul text. It should slugify the name into an ID,
ensure uniqueness, write agent.json and SOUL.md to the data
directory, and return the full agent detail response.
```

[TIMING: 5s — prompt visible]

**Narration (1:30-1:37):**
"Prompt one: create an agent via API. Claude Code generates the route, the slugify logic, the file persistence — all of it."

[SCREEN: Show the diff — `routes_agents.py` with the `create_agent` function: `slugify_agent_id(payload.name)`, `ensure_unique_agent_id`, `save_persona`, response construction. Scroll through key lines quickly.]

[TIMING: 7s]

**Narration (1:37-1:43):**
"Slugification, collision handling, SOUL.md file writes — fifty lines of production code from one sentence."

[SCREEN: Terminal — run `curl -X POST localhost:40000/agents -H 'Content-Type: application/json' -d '{"name":"Auditor","emoji":"magnifying-glass","soul":"You audit code changes for security issues."}'`. JSON response appears with `"id": "auditor"`, `"name": "Auditor"`, `"state": "stopped"`.]

**Narration (1:43-1:50):**
"Hit the endpoint. Agent created. It shows up in the dashboard immediately."

[SCREEN: Dashboard refreshes — four agents now visible including Auditor.]

[TIMING: 7s]

### Demo 2: Memory Compaction (1:50-2:10)

[SCREEN: Split — left shows a MEMORY.md file with many entries, right shows terminal.]

**Narration (1:50-1:57):**
"Prompt two: auto-compact memory when sessions get long. The compaction engine triggers at forty messages."

**Prompt shown on screen:**
```
Implement a CompactionEngine that triggers when a session exceeds
compact_threshold messages. It should chunk old messages, summarize
each chunk via Gemini CLI, rewrite the session with a compaction
record, and extract procedure candidates.
```

[TIMING: 7s]

[SCREEN: Show the before — a session JSONL file with 45 message entries scrolling past. Then the after — same file now starts with a `"type": "compaction"` record containing `"summary"` with bullet points and `"compacted_messages": 34`, followed by 11 kept messages.]

**Narration (1:57-2:04):**
"Before: forty-five raw messages. After: a compaction summary plus the eleven most recent turns. The oldest thirty-four messages are distilled into bullet points by Gemini."

[SCREEN: Show PROCEDURES.md gaining a new entry — `## Deploy to staging` — that was extracted from the compacted messages because it appeared three or more times.]

**Narration (2:04-2:10):**
"And repeated patterns get promoted to PROCEDURES.md automatically — learned workflows the agent can reference forever."

[TIMING: 6s]

### Demo 3: Cron via Google Chat (2:10-2:30)

[SCREEN: Google Chat window with Scheduler agent space open.]

**Narration (2:10-2:15):**
"Prompt three: let users manage cron tasks without leaving Google Chat."

**Prompt shown on screen:**
```
Add slash command handling so /cron add "<schedule>" "<instruction>"
creates a cron task, /cron list shows all tasks, and /cron delete <id>
removes one. Use shlex for quote parsing.
```

[TIMING: 5s]

[SCREEN: In Google Chat, type `/cron list`. Response appears showing the current tasks with schedule, last-run time, and instruction text formatted with status icons.]

**Narration (2:15-2:22):**
"Slash commands parsed with shlex, routed before they hit the AI. List, add, delete, enable, disable — all from the chat window."

[SCREEN: Type `/cron add "*/30 * * * *" "Check deployment health and alert if any service is down"`. Confirmation appears with checkmark, task ID, schedule, instruction.]

**Narration (2:22-2:30):**
"New cron task, every thirty minutes, created in one message. APScheduler picks it up immediately — no restart needed."

[SCREEN: Dashboard cron panel refreshes showing the new task. Quick cut to terminal: `GET /agents/scheduler/crons` returns JSON with the new task entry including `"schedule": "*/30 * * * *"` and `"enabled": true`.]

[TIMING: 8s]

---

## ACT 4 — Close (2:30-3:00)

[TIMING: 30s total]

[SCREEN: Dashboard wide shot — all agents visible, memory panels populated, cron tasks listed, session history showing delegation chains.]

**Narration (2:30-2:42):**
"g3lobster started as a conversation with Claude Code and grew into a production agent platform. Persistent identity, layered memory, autonomous scheduling, inter-agent delegation — every feature prompted into existence, tested, and shipped without writing boilerplate by hand."

[TIMING: 12s]

[SCREEN: Slow zoom into the dashboard header — "g3lobster" eyebrow text and "Google Chat Agent Console" title.]

**Narration (2:42-2:52):**
"The agents remember who they are. They learn from their own conversations. They wake themselves up on schedule. And they ask each other for help."

[TIMING: 10s]

[SCREEN: Black screen. Tagline fades in, white text, centered:]

> **Built by agents. For agents. Running agents.**

**Narration (2:52-3:00):**
"Built by agents. For agents. Running agents."

[SCREEN: Hold tagline for 3 seconds. Fade to black.]

[TIMING: 8s]

---

## Backup Plans

### ACT 1 — The Meta Moment
- **If Claude Code is slow or errors:** Use a pre-recorded screen capture of the prompt + diff cycle. Voice the narration live over the recording.
- **If the `/cron add` command fails in Google Chat:** Switch to a `curl` command in terminal hitting `POST /agents/scheduler/crons` with `{"schedule": "0 9 * * 1", "instruction": "Send weekly standup summary"}` — same result, different interface.

### ACT 2 — What Got Built
- **If the dashboard is unresponsive:** Pre-record the full dashboard walkthrough as a 60-second clip. Narrate live.
- **If an agent shows as stopped:** Run `POST /agents/luna/start` via curl before recording. Keep a terminal tab ready with start commands for all three agents.
- **If delegation session data is empty:** Pre-populate a session JSONL file with a realistic delegation exchange before the recording.

### ACT 3 — The Vibe-Coding Process
- **Demo 1 (agent creation) fallback:** Pre-record the curl + dashboard refresh. The diff can be shown from git history: `git log --oneline --all | head` then `git show <commit>:g3lobster/api/routes_agents.py`.
- **Demo 2 (compaction) fallback:** Prepare before/after snapshots of a session JSONL file and PROCEDURES.md as static screenshots. Show them side-by-side with narration.
- **Demo 3 (cron via chat) fallback:** If Google Chat bridge is down, demo via REST: `curl -X POST localhost:40000/agents/scheduler/crons -H 'Content-Type: application/json' -d '{"schedule": "*/30 * * * *", "instruction": "Check deployment health"}'`. Show the JSON response and dashboard update.

### ACT 4 — Close
- **No fallback needed.** This is pure narration over a static dashboard shot and a text card. Record the tagline card as a separate asset in advance.

### General
- Keep a second terminal running `curl localhost:40000/health` to verify the server is up before each take.
- Have `python -m g3lobster` ready to restart in under 10 seconds if the process dies.
- All backup clips should be rendered at the same resolution (1920x1080) and frame rate (30fps) as the live recording.
