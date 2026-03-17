import {
  completeBoardTask,
  createBoardTask,
  createAgent,
  createCronTask,
  deleteBoardTask,
  deleteAgent,
  deleteCronTask,
  exportAgentUrl,
  getAgent,
  getAgentMemory,
  getAgentProcedures,
  getCronTaskHistory,
  getAgentSession,
  getGlobalProcedures,
  getGlobalUserMemory,
  getSetupStatus,
  importAgent,
  listAgentSessions,
  listAgents,
  listAllCrons,
  listBoardTasks,
  listCronTasks,
  listGlobalKnowledge,
  listMcpServers,
  restartAgent,
  runCronTask,
  startAgent,
  startBridge,
  stopAgent,
  stopBridge,
  testAgent,
  triggerHeartbeat,
  updateAgent,
  updateAgentMemory,
  updateAgentProcedures,
  updateBoardTask,
  updateCronTask,
  updateGlobalProcedures,
  updateGlobalUserMemory,
  validateCronSchedule,
} from "./api.js";

// ============================================================
// UTILITIES
// ============================================================

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatDateTime(value) {
  if (!value) return "Never";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString();
}

function formatRelativeTime(value) {
  if (!value) return "Never";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return formatDateTime(value);
  const diffMs = parsed.getTime() - Date.now();
  const absMs = Math.abs(diffMs);
  const units = [
    ["day", 86_400_000],
    ["hour", 3_600_000],
    ["minute", 60_000],
    ["second", 1_000],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, divisor] of units) {
    if (absMs >= divisor || unit === "second") {
      return formatter.format(Math.round(diffMs / divisor), unit);
    }
  }
  return formatDateTime(value);
}

function formatDuration(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(1)}s`;
}

function stateBadgeClass(state) {
  const s = String(state || "").toLowerCase();
  if (["running", "active"].includes(s)) return "badge-success";
  if (["idle", "starting"].includes(s)) return "badge-warning";
  if (["error", "failed", "dead"].includes(s)) return "badge-danger";
  return "badge-default";
}

function stateStatusDotClass(state) {
  const s = String(state || "").toLowerCase();
  if (["running", "active"].includes(s)) return "running";
  if (["idle", "starting"].includes(s)) return "idle";
  if (["error", "failed", "dead"].includes(s)) return "error";
  return "stopped";
}

function priorityBadgeClass(priority) {
  const p = String(priority || "").toLowerCase();
  if (p === "critical") return "badge-danger";
  if (p === "high") return "badge-warning";
  if (p === "low") return "badge-default";
  return "badge-info";
}

function statusBadgeClass(status) {
  const s = String(status || "").toLowerCase();
  if (s === "done") return "badge-success";
  if (s === "in_progress") return "badge-warning";
  if (s === "blocked") return "badge-danger";
  return "badge-default";
}

// ============================================================
// HTML BUILDERS
// ============================================================

function buildShell() {
  return `
    <div class="app-shell">
      <header class="app-header">
        <span class="app-header-brand">&#x26E9; G3LOBSTER</span>
        <div class="app-header-meta">
          <span id="hdr-agent-count">AGENTS: &mdash;</span>
          <span id="hdr-running-count">RUNNING: &mdash;</span>
          <span id="hdr-clock"></span>
        </div>
      </header>
      <div class="app-body">
        <div class="main-panel">
          <nav class="tab-nav">
            <button class="tab-btn active" data-tab="agents">Agents</button>
            <button class="tab-btn" data-tab="board">Board</button>
            <button class="tab-btn" data-tab="cron">Cron</button>
            <button class="tab-btn" data-tab="memory">Memory</button>
            <button class="tab-btn" data-tab="settings">Settings</button>
          </nav>
          <div class="tab-panel active" id="panel-agents"></div>
          <div class="tab-panel" id="panel-board"></div>
          <div class="tab-panel" id="panel-cron"></div>
          <div class="tab-panel" id="panel-memory"></div>
          <div class="tab-panel" id="panel-settings"></div>
        </div>
      </div>
    </div>
  `;
}

function buildAgentsPanel() {
  return `
    <div style="padding: 24px">
      <div id="agents-notice"></div>
      <div class="section-header">
        <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
          <span class="section-title" style="flex-shrink:0">Active Agents</span>
          <select id="agent-select" class="form-select" style="max-width:280px">
            <option value="">— Select Agent —</option>
          </select>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
          <label class="btn btn-ghost btn-sm" style="cursor:pointer;position:relative">
            Import
            <input type="file" id="import-agent-file" accept=".g3agent,.zip" style="position:absolute;inset:0;opacity:0;cursor:pointer" />
          </label>
          <button class="btn btn-vermillion" id="btn-add-agent">+ Add Agent</button>
        </div>
      </div>
      <div id="add-agent-form" style="display:none" class="form-panel">
        <h3>New Agent</h3>
        <form id="create-agent-form">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
            <div class="form-row">
              <label class="form-label">Name</label>
              <input class="form-input" name="name" placeholder="Kitsune" required />
            </div>
            <div class="form-row">
              <label class="form-label">Emoji</label>
              <input class="form-input" name="emoji" placeholder="&#129418;" />
            </div>
            <div class="form-row">
              <label class="form-label">Model</label>
              <input class="form-input" name="model" placeholder="gemini" value="gemini" />
            </div>
            <div class="form-row">
              <label class="form-label">Space ID</label>
              <input class="form-input" name="space_id" placeholder="spaces/AAAA..." />
            </div>
            <div class="form-row">
              <label class="form-label">Heartbeat Enabled</label>
              <select class="form-select" name="heartbeat_enabled">
                <option value="false" selected>false</option>
                <option value="true">true</option>
              </select>
            </div>
            <div class="form-row">
              <label class="form-label">Heartbeat Interval (s)</label>
              <input class="form-input" name="heartbeat_interval_s" type="number" min="30" step="1" value="300" />
            </div>
          </div>
          <div class="form-row">
            <label class="form-label">SOUL.md</label>
            <textarea class="form-textarea" name="soul" placeholder="You are a wise kitsune spirit..."></textarea>
          </div>
          <div class="form-actions">
            <button type="button" class="btn btn-ghost" id="btn-cancel-add">Cancel</button>
            <button type="submit" class="btn btn-primary">Create Agent</button>
          </div>
        </form>
      </div>
      <div class="agent-grid" id="agent-grid">
        <div class="loading"><span class="spinner"></span></div>
      </div>
      <div id="agent-detail"></div>
    </div>
  `;
}

function buildAgentCard(agent) {
  const stateLabel = agent.state || "unknown";
  const badgeClass = stateBadgeClass(stateLabel);
  const dotClass = stateStatusDotClass(stateLabel);
  return `
    <div class="agent-card card" data-agent-id="${escapeHtml(agent.id)}">
      <div class="agent-card-header">
        <div class="agent-emoji">${escapeHtml(agent.emoji || "&#129302;")}</div>
        <div style="min-width:0;flex:1">
          <div class="agent-name truncate">${escapeHtml(agent.name)}</div>
          <div class="agent-id truncate">${escapeHtml(agent.id)}</div>
        </div>
        <div style="margin-left:auto;flex-shrink:0">
          <span class="badge ${escapeHtml(badgeClass)}">
            <span class="status-dot ${escapeHtml(dotClass)}"></span>
            ${escapeHtml(stateLabel)}
          </span>
        </div>
      </div>
      <div class="agent-meta">
        <span>MODEL: ${escapeHtml(agent.model || "&#8212;")}</span>
        ${agent.space_id ? `<span class="truncate">SPACE: ${escapeHtml(agent.space_id)}</span>` : ""}
      </div>
      <div class="agent-actions">
        <button class="btn btn-ghost btn-sm" data-action="view" data-agent-id="${escapeHtml(agent.id)}">View</button>
        <button class="btn btn-ghost btn-sm" data-action="heartbeat" data-agent-id="${escapeHtml(agent.id)}">&#9829; Heartbeat</button>
        <button class="btn btn-ghost btn-sm" data-action="test" data-agent-id="${escapeHtml(agent.id)}">Test</button>
        <button class="btn btn-danger btn-sm" data-action="stop" data-agent-id="${escapeHtml(agent.id)}">Stop</button>
        <button class="btn btn-ghost btn-sm" data-action="restart" data-agent-id="${escapeHtml(agent.id)}">Restart</button>
        <a class="btn btn-ghost btn-sm" href="${exportAgentUrl(agent.id)}" download="${escapeHtml(agent.id)}.g3agent">Export</a>
        <button class="btn btn-danger btn-sm" data-action="delete" data-agent-id="${escapeHtml(agent.id)}">Delete</button>
      </div>
    </div>
  `;
}

function buildAgentDetail(agent, detail, tab, tabData) {
  const tabs = ["Info", "Tasks", "Memory", "Procedures", "Sessions"];
  const tabBtns = tabs.map((t) => {
    const key = t.toLowerCase();
    return `<button class="detail-tab${tab === key ? " active" : ""}" data-detail-tab="${key}" data-agent-id="${escapeHtml(agent.id)}">${escapeHtml(t)}</button>`;
  }).join("");

  let body = "";
  if (tab === "info") {
    body = buildInfoTab(agent, detail);
  } else if (tab === "tasks") {
    body = buildTasksTab(agent, tabData || []);
  } else if (tab === "memory") {
    body = buildMemoryTab(agent, tabData || "");
  } else if (tab === "procedures") {
    body = buildProceduresTab(agent, tabData || "");
  } else if (tab === "sessions") {
    body = buildSessionsTab(agent, tabData || { sessions: [], transcript: null });
  }

  return `
    <div class="detail-panel" id="agent-detail-panel">
      <div class="detail-panel-header">
        <div class="agent-emoji">${escapeHtml(agent.emoji || "&#129302;")}</div>
        <div>
          <div class="agent-name">${escapeHtml(agent.name)}</div>
          <div class="agent-id">${escapeHtml(agent.id)}</div>
        </div>
        <button class="btn btn-ghost btn-sm" style="margin-left:auto" data-action="close-detail" data-agent-id="${escapeHtml(agent.id)}">&#x2715; Close</button>
      </div>
      <div class="detail-tabs">${tabBtns}</div>
      <div class="detail-body">${body}</div>
    </div>
  `;
}

function buildInfoTab(agent, detail) {
  const d = detail || agent;
  const mcpList = Array.isArray(d.mcp_servers) ? d.mcp_servers.join(", ") : "*";
  const dmAllowlist = Array.isArray(d.dm_allowlist) ? d.dm_allowlist.join("\n") : "";
  return `
    <form id="edit-agent-form" data-agent-id="${escapeHtml(agent.id)}">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="form-row">
          <label class="form-label">Name</label>
          <input class="form-input" name="name" value="${escapeHtml(d.name || "")}" required />
        </div>
        <div class="form-row">
          <label class="form-label">Emoji</label>
          <input class="form-input" name="emoji" value="${escapeHtml(d.emoji || "&#129302;")}" />
        </div>
        <div class="form-row">
          <label class="form-label">Model</label>
          <input class="form-input" name="model" value="${escapeHtml(d.model || "gemini")}" />
        </div>
        <div class="form-row">
          <label class="form-label">Space ID</label>
          <input class="form-input" name="space_id" value="${escapeHtml(d.space_id || "")}" placeholder="spaces/AAAA..." />
        </div>
        <div class="form-row">
          <label class="form-label">Bridge Enabled</label>
          <select class="form-select" name="bridge_enabled">
            <option value="true" ${d.bridge_enabled ? "selected" : ""}>true</option>
            <option value="false" ${d.bridge_enabled ? "" : "selected"}>false</option>
          </select>
        </div>
        <div class="form-row">
          <label class="form-label">Enabled</label>
          <select class="form-select" name="enabled">
            <option value="true" ${d.enabled !== false ? "selected" : ""}>true</option>
            <option value="false" ${d.enabled === false ? "selected" : ""}>false</option>
          </select>
        </div>
        <div class="form-row">
          <label class="form-label">Heartbeat Enabled</label>
          <select class="form-select" name="heartbeat_enabled">
            <option value="true" ${d.heartbeat_enabled === true ? "selected" : ""}>true</option>
            <option value="false" ${d.heartbeat_enabled !== true ? "selected" : ""}>false</option>
          </select>
        </div>
        <div class="form-row">
          <label class="form-label">Heartbeat Interval (s)</label>
          <input class="form-input" name="heartbeat_interval_s" type="number" min="30" step="1" value="${escapeHtml(String(d.heartbeat_interval_s || 300))}" />
        </div>
        <div class="form-row" style="grid-column:1/-1">
          <label class="form-label">MCP Servers (comma-separated, * for all)</label>
          <input class="form-input" name="mcp_servers" value="${escapeHtml(mcpList)}" placeholder="* or server1, server2" />
        </div>
      </div>
      <div class="form-row">
        <label class="form-label">SOUL.md</label>
        <textarea class="form-textarea" name="soul" style="min-height:160px">${escapeHtml(d.soul || "")}</textarea>
      </div>
      <div class="form-row">
        <label class="form-label">DM Allowlist (one sender ID per line)</label>
        <textarea class="form-textarea" name="dm_allowlist" placeholder="users/abc123&#10;user@example.com">${escapeHtml(dmAllowlist)}</textarea>
      </div>
      <div class="form-actions" style="justify-content:flex-start">
        <button type="submit" class="btn btn-primary">Save Changes</button>
        <button type="button" class="btn btn-ghost btn-sm" data-action="start" data-agent-id="${escapeHtml(agent.id)}">Start</button>
        <button type="button" class="btn btn-ghost btn-sm" data-action="stop" data-agent-id="${escapeHtml(agent.id)}">Stop</button>
        <button type="button" class="btn btn-ghost btn-sm" data-action="restart" data-agent-id="${escapeHtml(agent.id)}">Restart</button>
      </div>
    </form>
  `;
}

function buildTasksTab(agent, tasks) {
  const rows = tasks.length
    ? tasks.map((task) => {
        const status = String(task.status || "todo");
        const updated = String(task.updated_at || "").replace("T", " ").slice(0, 19) || "&#8212;";
        return `
          <tr>
            <td><code>${escapeHtml(String(task.id || "").slice(0, 8))}</code></td>
            <td>${escapeHtml(task.title || "")}</td>
            <td><span class="badge badge-default">${escapeHtml(task.type || "chore")}</span></td>
            <td><span class="badge ${escapeHtml(priorityBadgeClass(task.priority))}">${escapeHtml(task.priority || "normal")}</span></td>
            <td><span class="badge ${escapeHtml(statusBadgeClass(status))}">${escapeHtml(status)}</span></td>
            <td class="text-muted">${escapeHtml(updated)}</td>
            <td>
              <div style="display:flex;gap:6px">
                <button class="btn btn-ghost btn-sm" data-action="task-set-status" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(task.id)}" data-next-status="in_progress">Start</button>
                <button class="btn btn-ghost btn-sm" data-action="task-set-status" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(task.id)}" data-next-status="blocked">Block</button>
                <button class="btn btn-ghost btn-sm" data-action="task-complete" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(task.id)}" ${status === "done" ? "disabled" : ""}>Done</button>
                <button class="btn btn-danger btn-sm" data-action="task-delete" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(task.id)}">Delete</button>
              </div>
            </td>
          </tr>
        `;
      }).join("")
    : `<tr><td colspan="7" class="empty-state">No board tasks for this agent.</td></tr>`;

  return `
    <div style="margin-bottom:16px;display:flex;justify-content:flex-end">
      <button class="btn btn-ghost btn-sm" data-action="load-board-tasks" data-agent-id="${escapeHtml(agent.id)}">Refresh</button>
    </div>
    <div style="overflow-x:auto">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th><th>Title</th><th>Type</th><th>Priority</th><th>Status</th><th>Updated</th><th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function buildMemoryTab(agent, content) {
  return `
    <div class="form-row">
      <label class="form-label">Agent Memory (MEMORY.md)</label>
      <textarea class="form-textarea" id="memory-editor-${escapeHtml(agent.id)}" style="min-height:240px;font-family:'JetBrains Mono','SF Mono',monospace;font-size:13px">${escapeHtml(content)}</textarea>
    </div>
    <div class="form-actions" style="justify-content:flex-start">
      <button class="btn btn-ghost btn-sm" data-action="load-memory" data-agent-id="${escapeHtml(agent.id)}">Load</button>
      <button class="btn btn-primary" data-action="save-memory" data-agent-id="${escapeHtml(agent.id)}">Save Memory</button>
    </div>
  `;
}

function buildProceduresTab(agent, content) {
  return `
    <div class="form-row">
      <label class="form-label">Agent Procedures (PROCEDURES.md)</label>
      <textarea class="form-textarea" id="procedures-editor-${escapeHtml(agent.id)}" style="min-height:240px;font-family:'JetBrains Mono','SF Mono',monospace;font-size:13px">${escapeHtml(content)}</textarea>
    </div>
    <div class="form-actions" style="justify-content:flex-start">
      <button class="btn btn-ghost btn-sm" data-action="load-procedures" data-agent-id="${escapeHtml(agent.id)}">Load</button>
      <button class="btn btn-primary" data-action="save-procedures" data-agent-id="${escapeHtml(agent.id)}">Save Procedures</button>
    </div>
  `;
}

function buildSessionsTab(agent, { sessions, transcript }) {
  const options = sessions.length
    ? sessions.map((sid) => `<option value="${escapeHtml(sid)}">${escapeHtml(sid)}</option>`).join("")
    : `<option value="">No sessions found</option>`;

  let transcriptHtml = "";
  if (transcript) {
    const entries = Array.isArray(transcript.entries) ? transcript.entries : [];
    const messages = entries.filter((e) => e && e.type === "message" && e.message);
    if (messages.length) {
      transcriptHtml = messages.map((e) => {
        const role = String(e.message?.role || "unknown");
        const content = escapeHtml(String(e.message?.content || ""));
        const ts = e.timestamp ? `<span class="msg-ts">${escapeHtml(e.timestamp.replace("T", " ").slice(0, 19))}</span>` : "";
        return `
          <div class="msg">
            <div class="msg-header">
              <span class="msg-role ${escapeHtml(role)}">${escapeHtml(role)}</span>${ts}
            </div>
            <div class="msg-body">${content}</div>
          </div>
        `;
      }).join("");
    } else {
      transcriptHtml = `<p class="text-muted">No messages in this session.</p>`;
    }
  }

  return `
    <div class="form-row">
      <label class="form-label">Sessions</label>
      <select class="form-select" id="sessions-select-${escapeHtml(agent.id)}">${options}</select>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:16px">
      <button class="btn btn-ghost btn-sm" data-action="load-sessions" data-agent-id="${escapeHtml(agent.id)}">Refresh Sessions</button>
      <button class="btn btn-ghost btn-sm" data-action="open-session" data-agent-id="${escapeHtml(agent.id)}">Open Session</button>
    </div>
    ${transcriptHtml ? `<div class="session-transcript">${transcriptHtml}</div>` : ""}
  `;
}

function agentSelectOptions(agents, includeBlank) {
  const blank = includeBlank ? `<option value="">— Unassigned —</option>` : "";
  return blank + (agents || []).map((a) =>
    `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name || a.id)}</option>`
  ).join("");
}

function buildBoardPanel(tasks, agents) {
  const cols = [
    { key: "todo", label: "TODO" },
    { key: "in_progress", label: "In Progress" },
    { key: "blocked", label: "Blocked" },
    { key: "done", label: "Done" },
  ];

  const colHtml = cols.map(({ key, label }) => {
    const colTasks = tasks.filter((t) => (t.status || "todo") === key);
    const items = colTasks.map((task) => `
      <div class="kanban-item">
        <div class="kanban-item-title">${escapeHtml(task.title || "")}</div>
        <div class="kanban-item-meta">
          <span class="badge badge-default">${escapeHtml(task.type || "chore")}</span>
          <span class="badge ${escapeHtml(priorityBadgeClass(task.priority))}">${escapeHtml(task.priority || "normal")}</span>
          ${task.agent_id ? `<span class="text-muted">${escapeHtml(String(task.agent_id).slice(0, 8))}</span>` : ""}
        </div>
        <div style="display:flex;gap:6px;margin-top:8px">
          ${key !== "done" ? `<button class="btn btn-ghost btn-sm" data-action="board-task-complete" data-task-id="${escapeHtml(task.id)}">Done</button>` : ""}
          <button class="btn btn-danger btn-sm" data-action="board-task-delete" data-task-id="${escapeHtml(task.id)}">Delete</button>
        </div>
      </div>
    `).join("");
    return `
      <div class="kanban-col">
        <div class="kanban-col-header">
          <span>${escapeHtml(label)}</span>
          <span class="badge badge-default">${colTasks.length}</span>
        </div>
        ${items || `<p class="text-muted" style="padding:8px 0">No tasks</p>`}
      </div>
    `;
  }).join("");

  return `
    <div style="padding:24px">
      <div id="board-notice"></div>
      <div class="section-header">
        <span class="section-title">Task Board</span>
        <button class="btn btn-vermillion" id="btn-add-task">+ Add Task</button>
      </div>
      <div id="add-task-form" style="display:none" class="form-panel">
        <h3>New Task</h3>
        <form id="create-task-form">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
            <div class="form-row" style="grid-column:1/-1">
              <label class="form-label">Title</label>
              <input class="form-input" name="title" placeholder="Follow up on incident report" required />
            </div>
            <div class="form-row">
              <label class="form-label">Type</label>
              <select class="form-select" name="type">
                <option value="chore">chore</option>
                <option value="feature">feature</option>
                <option value="bug">bug</option>
                <option value="research">research</option>
                <option value="reminder">reminder</option>
              </select>
            </div>
            <div class="form-row">
              <label class="form-label">Priority</label>
              <select class="form-select" name="priority">
                <option value="normal">normal</option>
                <option value="high">high</option>
                <option value="critical">critical</option>
                <option value="low">low</option>
              </select>
            </div>
            <div class="form-row" style="grid-column:1/-1">
              <label class="form-label">Assign to Agent (optional)</label>
              <select class="form-select" name="agent_id">
                ${agentSelectOptions(agents, true)}
              </select>
            </div>
          </div>
          <div class="form-actions">
            <button type="button" class="btn btn-ghost" id="btn-cancel-task">Cancel</button>
            <button type="submit" class="btn btn-primary">Create Task</button>
          </div>
        </form>
      </div>
      <div class="kanban-board">${colHtml}</div>
    </div>
  `;
}

function buildCronPanel(allCrons, agents) {
  const rows = allCrons.length
    ? allCrons.map((task) => {
        const lastRun = task.last_run ? formatRelativeTime(task.last_run) : "Never";
        const nextRun = task.next_run ? formatRelativeTime(task.next_run) : "&#8212;";
        const enabledBadge = task.enabled !== false
          ? `<span class="badge badge-success">enabled</span>`
          : `<span class="badge badge-default">disabled</span>`;
        return `
          <tr>
            <td><code>${escapeHtml(String(task.id || "").slice(0, 8))}</code></td>
            <td class="text-muted">${escapeHtml(task.agent_id || "&#8212;")}</td>
            <td><code>${escapeHtml(task.schedule || "")}</code></td>
            <td class="truncate" style="max-width:200px" title="${escapeHtml(task.instruction || "")}">${escapeHtml(task.instruction || "")}</td>
            <td>${enabledBadge}</td>
            <td class="text-muted">${escapeHtml(lastRun)}</td>
            <td class="text-muted">${escapeHtml(nextRun)}</td>
            <td>
              <div style="display:flex;gap:6px">
                <button class="btn btn-ghost btn-sm" data-action="cron-run" data-agent-id="${escapeHtml(task.agent_id || "")}" data-task-id="${escapeHtml(task.id)}">Run</button>
                <button class="btn btn-ghost btn-sm" data-action="cron-toggle" data-agent-id="${escapeHtml(task.agent_id || "")}" data-task-id="${escapeHtml(task.id)}" data-enabled="${task.enabled !== false ? "1" : "0"}">${task.enabled !== false ? "Disable" : "Enable"}</button>
                <button class="btn btn-danger btn-sm" data-action="cron-delete" data-agent-id="${escapeHtml(task.agent_id || "")}" data-task-id="${escapeHtml(task.id)}">Delete</button>
              </div>
            </td>
          </tr>
        `;
      }).join("")
    : `<tr><td colspan="8" class="empty-state">No cron jobs configured.</td></tr>`;

  return `
    <div style="padding:24px">
      <div id="cron-notice"></div>
      <div class="section-header">
        <span class="section-title">Cron Jobs</span>
        <button class="btn btn-vermillion" id="btn-add-cron">+ Add Cron</button>
      </div>
      <div id="add-cron-form" style="display:none" class="form-panel">
        <h3>New Cron Job</h3>
        <form id="create-cron-form">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
            <div class="form-row">
              <label class="form-label">Agent</label>
              <select class="form-select" name="agent_id" required>
                <option value="">— Select Agent —</option>
                ${agentSelectOptions(agents, false)}
              </select>
            </div>
            <div class="form-row">
              <label class="form-label">Schedule (cron)</label>
              <div class="cron-schedule-row">
                <input class="form-input" name="schedule" placeholder="0 9 * * 1-5" required />
                <button type="button" class="btn btn-ghost btn-sm" id="btn-validate-cron">Validate</button>
              </div>
              <div id="cron-validate-hint" class="cron-validation-hint"></div>
            </div>
            <div class="form-row" style="grid-column:1/-1">
              <label class="form-label">Instruction</label>
              <input class="form-input" name="instruction" placeholder="Generate morning briefing" required />
            </div>
            <div class="form-row">
              <label class="form-label">DM Target (optional)</label>
              <input class="form-input" name="dm_target" placeholder="nick@example.com" />
            </div>
            <div class="form-row">
              <label class="form-label">Enabled</label>
              <select class="form-select" name="enabled">
                <option value="true" selected>true</option>
                <option value="false">false</option>
              </select>
            </div>
          </div>
          <div class="form-actions">
            <button type="button" class="btn btn-ghost" id="btn-cancel-cron">Cancel</button>
            <button type="submit" class="btn btn-primary">Create Cron Job</button>
          </div>
        </form>
      </div>
      <div style="overflow-x:auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>ID</th><th>Agent</th><th>Schedule</th><th>Instruction</th><th>Status</th><th>Last Run</th><th>Next Run</th><th></th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

function buildMemoryPanel(globalUserMemory, globalProcedures, globalKnowledge) {
  const knowledgeItems = globalKnowledge.length
    ? globalKnowledge.map((item) => `<li class="knowledge-item">${escapeHtml(String(item.name || item.id || item))}</li>`).join("")
    : `<li class="knowledge-item text-muted">No knowledge files configured.</li>`;

  return `
    <div style="padding:24px">
      <div id="memory-notice"></div>
      <div class="section-header">
        <span class="section-title">Global Memory</span>
      </div>
      <div class="card" style="margin-bottom:12px">
        <div class="section-header" style="margin-bottom:16px">
          <span class="section-title">User Memory</span>
          <button class="btn btn-ghost btn-sm" data-action="reload-global-memory">Reload</button>
        </div>
        <div class="form-row">
          <textarea class="form-textarea" id="global-user-memory-editor" style="min-height:200px;font-family:'JetBrains Mono','SF Mono',monospace;font-size:13px">${escapeHtml(globalUserMemory)}</textarea>
        </div>
        <div class="form-actions" style="justify-content:flex-start">
          <button class="btn btn-primary" data-action="save-global-user-memory">Save User Memory</button>
        </div>
      </div>
      <div class="card" style="margin-bottom:12px">
        <div class="section-header" style="margin-bottom:16px">
          <span class="section-title">Global Procedures</span>
          <button class="btn btn-ghost btn-sm" data-action="reload-global-procedures">Reload</button>
        </div>
        <div class="form-row">
          <textarea class="form-textarea" id="global-procedures-editor" style="min-height:200px;font-family:'JetBrains Mono','SF Mono',monospace;font-size:13px">${escapeHtml(globalProcedures)}</textarea>
        </div>
        <div class="form-actions" style="justify-content:flex-start">
          <button class="btn btn-primary" data-action="save-global-procedures">Save Procedures</button>
        </div>
      </div>
      <div class="card">
        <div class="section-title" style="margin-bottom:12px">Knowledge Files</div>
        <ul class="knowledge-list">${knowledgeItems}</ul>
      </div>
    </div>
  `;
}

function buildSettingsPanel(status) {
  const agents = Array.isArray(status?.agents) ? status.agents : [];

  const bridgeRows = agents.map((agent) => {
    const isRunning = agent.bridge_running || false;
    const hasSpace = Boolean(agent.space_id);
    let statusLabel = "not configured";
    let badgeClass = "badge-default";
    if (hasSpace && isRunning) { statusLabel = "running"; badgeClass = "badge-success"; }
    else if (hasSpace && agent.bridge_enabled && !isRunning) { statusLabel = "stopped"; badgeClass = "badge-danger"; }
    else if (hasSpace && !agent.bridge_enabled) { statusLabel = "disabled"; badgeClass = "badge-warning"; }

    return `
      <div class="bridge-row">
        <span class="bridge-agent-name">${escapeHtml(agent.emoji || "&#129302;")} ${escapeHtml(agent.name)}</span>
        <span class="bridge-space-id">${escapeHtml(agent.space_id || "(not set)")}</span>
        <span class="badge ${badgeClass}">${escapeHtml(statusLabel)}</span>
        <button class="btn btn-ghost btn-sm" data-action="bridge-start" data-agent-id="${escapeHtml(agent.id)}" ${isRunning || !hasSpace ? "disabled" : ""}>Start</button>
        <button class="btn btn-danger btn-sm" data-action="bridge-stop" data-agent-id="${escapeHtml(agent.id)}" ${!isRunning ? "disabled" : ""}>Stop</button>
      </div>
    `;
  }).join("");

  const setupPhase = status?.phase || "unknown";
  const phaseBadge = setupPhase === "running" ? "badge-success" : setupPhase === "error" ? "badge-danger" : "badge-warning";

  return `
    <div style="padding:24px">
      <div id="settings-notice"></div>
      <div class="section-header">
        <span class="section-title">Settings</span>
        <span class="badge ${phaseBadge}">${escapeHtml(setupPhase)}</span>
      </div>
      <div class="card card-shrine" style="margin-bottom:12px">
        <div class="section-title" style="margin-bottom:16px">Setup Status</div>
        <div class="form-row">
          <label class="form-label">Phase</label>
          <p style="color:#E8ECF0;font-size:14px">${escapeHtml(status?.phase || "&#8212;")}</p>
        </div>
        ${status?.message ? `<div class="form-row"><label class="form-label">Message</label><p class="text-muted">${escapeHtml(status.message)}</p></div>` : ""}
        <div class="form-actions" style="justify-content:flex-start">
          <button class="btn btn-ghost btn-sm" data-action="refresh-settings">Refresh Status</button>
        </div>
      </div>
      ${agents.length ? `
        <div class="card">
          <div class="section-title" style="margin-bottom:16px">Bridge Controls</div>
          ${bridgeRows}
        </div>
      ` : ""}
    </div>
  `;
}

// ============================================================
// NOTICE HELPERS
// ============================================================

function showNotice(containerId, type, text) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `<div class="notice ${escapeHtml(type)}">${escapeHtml(text)}</div>`;
  if (type !== "error") {
    setTimeout(() => { if (el) el.innerHTML = ""; }, 4000);
  }
}

// ============================================================
// MAIN EXPORT
// ============================================================

export async function render(root, { status, onSetupChange }) {
  root.innerHTML = buildShell();

  // ---- STATE ----
  let disposed = false;
  let agentPollingId = null;
  let clockId = null;
  let agentsCache = [];

  // ---- CLOCK ----
  function tickClock() {
    const el = document.getElementById("hdr-clock");
    if (el) el.textContent = new Date().toLocaleTimeString();
  }
  tickClock();
  clockId = window.setInterval(tickClock, 1000);

  // ---- HEADER COUNTS ----
  function updateHeaderCounts(agents) {
    const countEl = document.getElementById("hdr-agent-count");
    const runningEl = document.getElementById("hdr-running-count");
    if (countEl) countEl.textContent = `AGENTS: ${agents.length}`;
    if (runningEl) {
      const running = agents.filter((a) => {
        const s = String(a.state || "").toLowerCase();
        return ["running", "active"].includes(s);
      }).length;
      runningEl.textContent = `RUNNING: ${running}`;
    }
  }

  // ---- TAB SWITCHING ----
  function switchTab(tabName) {
    root.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    root.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `panel-${tabName}`);
    });
    loadTabContent(tabName);
  }

  async function loadTabContent(tabName) {
    if (disposed) return;
    if (tabName === "agents") {
      await loadAgentsTab();
    } else if (tabName === "board") {
      await loadBoardTab();
    } else if (tabName === "cron") {
      await loadCronTab();
    } else if (tabName === "memory") {
      await loadMemoryTab();
    } else if (tabName === "settings") {
      await loadSettingsTab();
    }
  }

  // ---- AGENTS TAB ----
  async function loadAgentsTab() {
    const panel = document.getElementById("panel-agents");
    if (!panel) return;

    if (!panel.querySelector("#agent-grid")) {
      panel.innerHTML = buildAgentsPanel();
      wireAgentsForm(panel);
    }

    await refreshAgentGrid();
  }

  function wireAgentsForm(panel) {
    const btnAdd = panel.querySelector("#btn-add-agent");
    const btnCancel = panel.querySelector("#btn-cancel-add");
    const addForm = panel.querySelector("#add-agent-form");
    const createForm = panel.querySelector("#create-agent-form");
    const importFile = panel.querySelector("#import-agent-file");
    const agentSel = panel.querySelector("#agent-select");

    if (agentSel) {
      agentSel.addEventListener("change", async () => {
        const id = agentSel.value;
        if (id) await showAgentDetail(id, "info");
      });
    }

    if (btnAdd && addForm) {
      btnAdd.addEventListener("click", () => {
        addForm.style.display = addForm.style.display === "none" ? "block" : "none";
      });
    }
    if (btnCancel && addForm) {
      btnCancel.addEventListener("click", () => { addForm.style.display = "none"; });
    }
    if (createForm) {
      createForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(createForm);
        const payload = {
          name: fd.get("name"),
          emoji: fd.get("emoji") || "&#129302;",
          soul: fd.get("soul") || "",
          model: fd.get("model") || "gemini",
          space_id: fd.get("space_id") || "",
          heartbeat_enabled: fd.get("heartbeat_enabled") === "true",
          heartbeat_interval_s: parseInt(fd.get("heartbeat_interval_s") || "300", 10),
        };
        try {
          await createAgent(payload);
          createForm.reset();
          if (addForm) addForm.style.display = "none";
          showNotice("agents-notice", "success", `Agent "${payload.name}" created.`);
          await refreshAgentGrid();
        } catch (err) {
          showNotice("agents-notice", "error", `Failed to create agent: ${err.message}`);
        }
      });
    }
    if (importFile) {
      importFile.addEventListener("change", async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        try {
          const result = await importAgent(file, false);
          showNotice("agents-notice", "success", `Agent imported: ${result?.id || "ok"}`);
          await refreshAgentGrid();
        } catch (err) {
          if (err.status === 409) {
            if (confirm("An agent with this ID already exists. Overwrite?")) {
              try {
                const result = await importAgent(file, true);
                showNotice("agents-notice", "success", `Agent overwritten: ${result?.id || "ok"}`);
                await refreshAgentGrid();
              } catch (err2) {
                showNotice("agents-notice", "error", `Import failed: ${err2.message}`);
              }
            }
          } else {
            showNotice("agents-notice", "error", `Import failed: ${err.message}`);
          }
        } finally {
          importFile.value = "";
        }
      });
    }
  }

  async function refreshAgentGrid() {
    if (disposed) return;
    try {
      const agents = await listAgents();
      agentsCache = agents;
      updateHeaderCounts(agents);
      const grid = document.getElementById("agent-grid");
      if (!grid) return;
      if (!agents.length) {
        grid.innerHTML = `<div class="empty-state">No agents configured. Add your first agent above.</div>`;
        return;
      }
      grid.innerHTML = agents.map(buildAgentCard).join("");
      const sel = document.getElementById("agent-select");
      if (sel) {
        const current = sel.value;
        sel.innerHTML = `<option value="">— Select Agent —</option>` +
          agents.map((a) => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name || a.id)}</option>`).join("");
        if (current) sel.value = current;
      }
    } catch (err) {
      showNotice("agents-notice", "error", `Failed to load agents: ${err.message}`);
    }
  }

  async function showAgentDetail(agentId, tab) {
    const agent = agentsCache.find((a) => a.id === agentId) || { id: agentId, name: agentId };
    let tabData = null;

    try {
      if (tab === "info") {
        tabData = await getAgent(agentId);
      } else if (tab === "tasks") {
        tabData = await listBoardTasks({ agent_id: agentId, limit: 200 });
      } else if (tab === "memory") {
        const payload = await getAgentMemory(agentId);
        tabData = payload.content || "";
      } else if (tab === "procedures") {
        const payload = await getAgentProcedures(agentId);
        tabData = payload.content || "";
      } else if (tab === "sessions") {
        const sessions = await listAgentSessions(agentId);
        tabData = { sessions, transcript: null };
      }
    } catch (err) {
      showNotice("agents-notice", "error", `Failed to load ${tab}: ${err.message}`);
    }

    const detailEl = document.getElementById("agent-detail");
    if (!detailEl) return;
    detailEl.innerHTML = buildAgentDetail(agent, tab === "info" ? tabData : null, tab, tabData);
    wireDetailForms(agentId);
  }

  function wireDetailForms(agentId) {
    const editForm = document.getElementById("edit-agent-form");
    if (editForm) {
      editForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(editForm);
        const mcpRaw = fd.get("mcp_servers") || "*";
        const mcpServers = mcpRaw.trim() === "*"
          ? ["*"]
          : mcpRaw.split(",").map((s) => s.trim()).filter(Boolean);
        const dmRaw = fd.get("dm_allowlist") || "";
        const dmAllowlist = dmRaw.split("\n").map((s) => s.trim()).filter(Boolean);
        const payload = {
          name: fd.get("name"),
          emoji: fd.get("emoji") || "&#129302;",
          model: fd.get("model") || "gemini",
          space_id: fd.get("space_id") || "",
          soul: fd.get("soul") || "",
          bridge_enabled: fd.get("bridge_enabled") === "true",
          enabled: fd.get("enabled") === "true",
          heartbeat_enabled: fd.get("heartbeat_enabled") === "true",
          heartbeat_interval_s: parseInt(fd.get("heartbeat_interval_s") || "300", 10),
          mcp_servers: mcpServers,
          dm_allowlist: dmAllowlist,
        };
        try {
          await updateAgent(agentId, payload);
          showNotice("agents-notice", "success", "Agent updated.");
          await refreshAgentGrid();
          await showAgentDetail(agentId, "info");
        } catch (err) {
          showNotice("agents-notice", "error", `Failed to update agent: ${err.message}`);
        }
      });
    }
  }

  // ---- BOARD TAB ----
  async function loadBoardTab() {
    const panel = document.getElementById("panel-board");
    if (!panel) return;
    panel.innerHTML = `<div class="loading"><span class="spinner"></span></div>`;
    try {
      const tasks = await listBoardTasks({ limit: 500 });
      panel.innerHTML = buildBoardPanel(tasks, agentsCache);
      wireBoardForms(panel);
    } catch (err) {
      panel.innerHTML = `<div style="padding:24px"><div class="notice error">Failed to load board: ${escapeHtml(err.message)}</div></div>`;
    }
  }

  function wireBoardForms(panel) {
    const btnAdd = panel.querySelector("#btn-add-task");
    const btnCancel = panel.querySelector("#btn-cancel-task");
    const addForm = panel.querySelector("#add-task-form");
    const createForm = panel.querySelector("#create-task-form");

    if (btnAdd && addForm) {
      btnAdd.addEventListener("click", () => {
        addForm.style.display = addForm.style.display === "none" ? "block" : "none";
      });
    }
    if (btnCancel && addForm) {
      btnCancel.addEventListener("click", () => { addForm.style.display = "none"; });
    }
    if (createForm) {
      createForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(createForm);
        const payload = {
          title: fd.get("title"),
          type: fd.get("type") || "chore",
          priority: fd.get("priority") || "normal",
        };
        const agentId = (fd.get("agent_id") || "").trim();
        if (agentId) payload.agent_id = agentId;
        try {
          await createBoardTask(payload);
          createForm.reset();
          if (addForm) addForm.style.display = "none";
          await loadBoardTab();
        } catch (err) {
          showNotice("board-notice", "error", `Failed to create task: ${err.message}`);
        }
      });
    }
  }

  // ---- CRON TAB ----
  async function loadCronTab() {
    const panel = document.getElementById("panel-cron");
    if (!panel) return;
    panel.innerHTML = `<div class="loading"><span class="spinner"></span></div>`;
    try {
      const allCrons = await listAllCrons();
      panel.innerHTML = buildCronPanel(allCrons, agentsCache);
      wireCronForms(panel);
    } catch (err) {
      panel.innerHTML = `<div style="padding:24px"><div class="notice error">Failed to load crons: ${escapeHtml(err.message)}</div></div>`;
    }
  }

  function wireCronForms(panel) {
    const btnAdd = panel.querySelector("#btn-add-cron");
    const btnCancel = panel.querySelector("#btn-cancel-cron");
    const addForm = panel.querySelector("#add-cron-form");
    const createForm = panel.querySelector("#create-cron-form");
    const btnValidate = panel.querySelector("#btn-validate-cron");

    if (btnAdd && addForm) {
      btnAdd.addEventListener("click", () => {
        addForm.style.display = addForm.style.display === "none" ? "block" : "none";
      });
    }
    if (btnCancel && addForm) {
      btnCancel.addEventListener("click", () => { addForm.style.display = "none"; });
    }
    if (btnValidate && createForm) {
      btnValidate.addEventListener("click", async () => {
        const scheduleInput = createForm.querySelector("[name='schedule']");
        const hint = panel.querySelector("#cron-validate-hint");
        if (!scheduleInput || !hint) return;
        hint.textContent = "Validating...";
        hint.className = "cron-validation-hint";
        try {
          const result = await validateCronSchedule(scheduleInput.value.trim());
          if (result?.valid) {
            hint.textContent = `Valid.${result.next_run ? " Next: " + formatDateTime(result.next_run) : ""}`;
            hint.className = "cron-validation-hint valid";
          } else {
            hint.textContent = `Invalid: ${result?.error || "unknown error"}`;
            hint.className = "cron-validation-hint invalid";
          }
        } catch (err) {
          hint.textContent = `Validation error: ${err.message}`;
          hint.className = "cron-validation-hint invalid";
        }
      });
    }
    if (createForm) {
      createForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(createForm);
        const agentId = (fd.get("agent_id") || "").trim();
        if (!agentId) {
          showNotice("cron-notice", "error", "Agent ID is required.");
          return;
        }
        const payload = {
          schedule: fd.get("schedule"),
          instruction: fd.get("instruction"),
          dm_target: fd.get("dm_target") || "",
          enabled: fd.get("enabled") === "true",
        };
        try {
          await createCronTask(agentId, payload);
          createForm.reset();
          if (addForm) addForm.style.display = "none";
          showNotice("cron-notice", "success", "Cron job created.");
          await loadCronTab();
        } catch (err) {
          showNotice("cron-notice", "error", `Failed to create cron: ${err.message}`);
        }
      });
    }
  }

  // ---- MEMORY TAB ----
  async function loadMemoryTab() {
    const panel = document.getElementById("panel-memory");
    if (!panel) return;
    panel.innerHTML = `<div class="loading"><span class="spinner"></span></div>`;
    try {
      const [memPayload, procPayload, knowledgePayload] = await Promise.all([
        getGlobalUserMemory(),
        getGlobalProcedures(),
        listGlobalKnowledge().catch(() => ({ items: [] })),
      ]);
      panel.innerHTML = buildMemoryPanel(
        memPayload.content || "",
        procPayload.content || "",
        knowledgePayload.items || []
      );
    } catch (err) {
      panel.innerHTML = `<div style="padding:24px"><div class="notice error">Failed to load memory: ${escapeHtml(err.message)}</div></div>`;
    }
  }

  // ---- SETTINGS TAB ----
  async function loadSettingsTab() {
    const panel = document.getElementById("panel-settings");
    if (!panel) return;
    panel.innerHTML = `<div class="loading"><span class="spinner"></span></div>`;
    try {
      const setupStatus = await getSetupStatus();
      const mergedStatus = {
        ...setupStatus,
        agents: agentsCache.length ? agentsCache : (setupStatus.agents || []),
      };
      panel.innerHTML = buildSettingsPanel(mergedStatus);
    } catch (err) {
      panel.innerHTML = `<div style="padding:24px"><div class="notice error">Failed to load settings: ${escapeHtml(err.message)}</div></div>`;
    }
  }

  // ---- DELEGATED EVENT HANDLER ----
  async function handleClick(e) {
    if (disposed) return;

    // Tab nav
    const tabBtn = e.target.closest(".tab-btn[data-tab]");
    if (tabBtn) {
      switchTab(tabBtn.dataset.tab);
      return;
    }

    // Detail tab switch
    const detailTabBtn = e.target.closest(".detail-tab[data-detail-tab]");
    if (detailTabBtn) {
      const agentId = detailTabBtn.dataset.agentId;
      const tab = detailTabBtn.dataset.detailTab;
      if (agentId && tab) await showAgentDetail(agentId, tab);
      return;
    }

    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    const agentId = btn.dataset.agentId;
    const taskId = btn.dataset.taskId;

    // ---- AGENT ACTIONS ----
    if (action === "view" && agentId) {
      await showAgentDetail(agentId, "info");
      return;
    }

    if (action === "close-detail") {
      const detailEl = document.getElementById("agent-detail");
      if (detailEl) detailEl.innerHTML = "";
      return;
    }

    if (action === "start" && agentId) {
      try {
        btn.disabled = true;
        await startAgent(agentId);
        showNotice("agents-notice", "success", "Agent started.");
        await refreshAgentGrid();
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to start: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
      return;
    }

    if (action === "stop" && agentId) {
      try {
        btn.disabled = true;
        await stopAgent(agentId);
        showNotice("agents-notice", "success", "Agent stopped.");
        await refreshAgentGrid();
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to stop: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
      return;
    }

    if (action === "restart" && agentId) {
      try {
        btn.disabled = true;
        await restartAgent(agentId);
        showNotice("agents-notice", "success", "Agent restarted.");
        await refreshAgentGrid();
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to restart: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
      return;
    }

    if (action === "heartbeat" && agentId) {
      try {
        btn.disabled = true;
        await triggerHeartbeat(agentId);
        showNotice("agents-notice", "success", "Heartbeat triggered.");
      } catch (err) {
        showNotice("agents-notice", "error", `Heartbeat failed: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
      return;
    }

    if (action === "test" && agentId) {
      try {
        btn.disabled = true;
        const result = await testAgent(agentId, "ping");
        showNotice("agents-notice", "success", `Test sent: ${result?.response || "ok"}`);
      } catch (err) {
        showNotice("agents-notice", "error", `Test failed: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
      return;
    }

    if (action === "delete" && agentId) {
      if (!confirm(`Delete agent "${agentId}"? This cannot be undone.`)) return;
      try {
        await deleteAgent(agentId);
        showNotice("agents-notice", "success", "Agent deleted.");
        const detailEl = document.getElementById("agent-detail");
        if (detailEl) detailEl.innerHTML = "";
        await refreshAgentGrid();
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to delete: ${err.message}`);
      }
      return;
    }

    // ---- MEMORY / PROCEDURES ----
    if (action === "load-memory" && agentId) {
      try {
        const payload = await getAgentMemory(agentId);
        const editor = document.getElementById(`memory-editor-${agentId}`);
        if (editor) editor.value = payload.content || "";
        showNotice("agents-notice", "success", "Memory loaded.");
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to load memory: ${err.message}`);
      }
      return;
    }

    if (action === "save-memory" && agentId) {
      try {
        const editor = document.getElementById(`memory-editor-${agentId}`);
        await updateAgentMemory(agentId, editor ? editor.value : "");
        showNotice("agents-notice", "success", "Memory saved.");
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to save memory: ${err.message}`);
      }
      return;
    }

    if (action === "load-procedures" && agentId) {
      try {
        const payload = await getAgentProcedures(agentId);
        const editor = document.getElementById(`procedures-editor-${agentId}`);
        if (editor) editor.value = payload.content || "";
        showNotice("agents-notice", "success", "Procedures loaded.");
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to load procedures: ${err.message}`);
      }
      return;
    }

    if (action === "save-procedures" && agentId) {
      try {
        const editor = document.getElementById(`procedures-editor-${agentId}`);
        await updateAgentProcedures(agentId, editor ? editor.value : "");
        showNotice("agents-notice", "success", "Procedures saved.");
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to save procedures: ${err.message}`);
      }
      return;
    }

    // ---- SESSIONS ----
    if (action === "load-sessions" && agentId) {
      try {
        const sessions = await listAgentSessions(agentId);
        const select = document.getElementById(`sessions-select-${agentId}`);
        if (select) {
          select.innerHTML = sessions.length
            ? sessions.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("")
            : `<option value="">No sessions found</option>`;
        }
        showNotice("agents-notice", "success", `${sessions.length} sessions loaded.`);
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to load sessions: ${err.message}`);
      }
      return;
    }

    if (action === "open-session" && agentId) {
      const select = document.getElementById(`sessions-select-${agentId}`);
      const sessionId = select?.value;
      if (!sessionId) {
        showNotice("agents-notice", "error", "No session selected.");
        return;
      }
      try {
        const transcript = await getAgentSession(agentId, sessionId);
        const agent = agentsCache.find((a) => a.id === agentId) || { id: agentId, name: agentId };
        const detailEl = document.getElementById("agent-detail");
        if (detailEl) {
          const sessions = Array.from(select.options).map((o) => o.value).filter(Boolean);
          detailEl.innerHTML = buildAgentDetail(agent, null, "sessions", { sessions, transcript });
        }
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to open session: ${err.message}`);
      }
      return;
    }

    // ---- TASK ACTIONS (agent detail panel) ----
    if (action === "load-board-tasks" && agentId) {
      try {
        const tasks = await listBoardTasks({ agent_id: agentId, limit: 200 });
        const agent = agentsCache.find((a) => a.id === agentId) || { id: agentId, name: agentId };
        const detailEl = document.getElementById("agent-detail");
        if (detailEl) detailEl.innerHTML = buildAgentDetail(agent, null, "tasks", tasks);
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to refresh tasks: ${err.message}`);
      }
      return;
    }

    if (action === "task-set-status" && taskId) {
      const nextStatus = btn.dataset.nextStatus;
      try {
        await updateBoardTask(taskId, { status: nextStatus });
        if (agentId) {
          const tasks = await listBoardTasks({ agent_id: agentId, limit: 200 });
          const agent = agentsCache.find((a) => a.id === agentId) || { id: agentId, name: agentId };
          const detailEl = document.getElementById("agent-detail");
          if (detailEl) detailEl.innerHTML = buildAgentDetail(agent, null, "tasks", tasks);
        }
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to update task: ${err.message}`);
      }
      return;
    }

    if (action === "task-complete" && taskId) {
      try {
        await completeBoardTask(taskId);
        if (agentId) {
          const tasks = await listBoardTasks({ agent_id: agentId, limit: 200 });
          const agent = agentsCache.find((a) => a.id === agentId) || { id: agentId, name: agentId };
          const detailEl = document.getElementById("agent-detail");
          if (detailEl) detailEl.innerHTML = buildAgentDetail(agent, null, "tasks", tasks);
        }
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to complete task: ${err.message}`);
      }
      return;
    }

    if (action === "task-delete" && taskId) {
      if (!confirm("Delete this task?")) return;
      try {
        await deleteBoardTask(taskId);
        if (agentId) {
          const tasks = await listBoardTasks({ agent_id: agentId, limit: 200 });
          const agent = agentsCache.find((a) => a.id === agentId) || { id: agentId, name: agentId };
          const detailEl = document.getElementById("agent-detail");
          if (detailEl) detailEl.innerHTML = buildAgentDetail(agent, null, "tasks", tasks);
        }
      } catch (err) {
        showNotice("agents-notice", "error", `Failed to delete task: ${err.message}`);
      }
      return;
    }

    // ---- BOARD PANEL TASK ACTIONS ----
    if (action === "board-task-complete" && taskId) {
      try {
        await completeBoardTask(taskId);
        await loadBoardTab();
      } catch (err) {
        showNotice("board-notice", "error", `Failed to complete task: ${err.message}`);
      }
      return;
    }

    if (action === "board-task-delete" && taskId) {
      if (!confirm("Delete this task?")) return;
      try {
        await deleteBoardTask(taskId);
        await loadBoardTab();
      } catch (err) {
        showNotice("board-notice", "error", `Failed to delete task: ${err.message}`);
      }
      return;
    }

    // ---- CRON ACTIONS ----
    if (action === "cron-run" && taskId) {
      const cronAgentId = agentId;
      if (!cronAgentId) {
        showNotice("cron-notice", "error", "Unknown agent for this cron.");
        return;
      }
      try {
        btn.disabled = true;
        await runCronTask(cronAgentId, taskId);
        showNotice("cron-notice", "success", "Cron job triggered.");
        await loadCronTab();
      } catch (err) {
        showNotice("cron-notice", "error", `Failed to run cron: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
      return;
    }

    if (action === "cron-toggle" && taskId) {
      const cronAgentId = agentId;
      if (!cronAgentId) return;
      const currentlyEnabled = btn.dataset.enabled === "1";
      try {
        await updateCronTask(cronAgentId, taskId, { enabled: !currentlyEnabled });
        await loadCronTab();
      } catch (err) {
        showNotice("cron-notice", "error", `Failed to toggle cron: ${err.message}`);
      }
      return;
    }

    if (action === "cron-delete" && taskId) {
      const cronAgentId = agentId;
      if (!cronAgentId) return;
      if (!confirm("Delete this cron job?")) return;
      try {
        await deleteCronTask(cronAgentId, taskId);
        showNotice("cron-notice", "success", "Cron job deleted.");
        await loadCronTab();
      } catch (err) {
        showNotice("cron-notice", "error", `Failed to delete cron: ${err.message}`);
      }
      return;
    }

    // ---- GLOBAL MEMORY ACTIONS ----
    if (action === "save-global-user-memory") {
      try {
        const editor = document.getElementById("global-user-memory-editor");
        await updateGlobalUserMemory(editor ? editor.value : "");
        showNotice("memory-notice", "success", "Global user memory saved.");
      } catch (err) {
        showNotice("memory-notice", "error", `Failed to save: ${err.message}`);
      }
      return;
    }

    if (action === "save-global-procedures") {
      try {
        const editor = document.getElementById("global-procedures-editor");
        await updateGlobalProcedures(editor ? editor.value : "");
        showNotice("memory-notice", "success", "Global procedures saved.");
      } catch (err) {
        showNotice("memory-notice", "error", `Failed to save: ${err.message}`);
      }
      return;
    }

    if (action === "reload-global-memory") {
      try {
        const payload = await getGlobalUserMemory();
        const editor = document.getElementById("global-user-memory-editor");
        if (editor) editor.value = payload.content || "";
        showNotice("memory-notice", "success", "Reloaded.");
      } catch (err) {
        showNotice("memory-notice", "error", `Failed to reload: ${err.message}`);
      }
      return;
    }

    if (action === "reload-global-procedures") {
      try {
        const payload = await getGlobalProcedures();
        const editor = document.getElementById("global-procedures-editor");
        if (editor) editor.value = payload.content || "";
        showNotice("memory-notice", "success", "Reloaded.");
      } catch (err) {
        showNotice("memory-notice", "error", `Failed to reload: ${err.message}`);
      }
      return;
    }

    // ---- SETTINGS ACTIONS ----
    if (action === "bridge-start" && agentId) {
      try {
        btn.disabled = true;
        await startBridge(agentId);
        showNotice("settings-notice", "success", "Bridge started.");
        await loadSettingsTab();
      } catch (err) {
        showNotice("settings-notice", "error", `Failed to start bridge: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
      return;
    }

    if (action === "bridge-stop" && agentId) {
      try {
        btn.disabled = true;
        await stopBridge(agentId);
        showNotice("settings-notice", "success", "Bridge stopped.");
        await loadSettingsTab();
      } catch (err) {
        showNotice("settings-notice", "error", `Failed to stop bridge: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
      return;
    }

    if (action === "refresh-settings") {
      await loadSettingsTab();
      return;
    }
  }

  // ---- ATTACH EVENTS ----
  root.addEventListener("click", handleClick);

  // ---- INITIAL LOAD ----
  await loadAgentsTab();

  // ---- AGENT POLLING (30s) ----
  agentPollingId = window.setInterval(async () => {
    if (disposed) return;
    const activePanel = root.querySelector(".tab-panel.active");
    if (activePanel?.id !== "panel-agents") return;
    try {
      const agents = await listAgents();
      agentsCache = agents;
      updateHeaderCounts(agents);
      const grid = document.getElementById("agent-grid");
      if (grid) {
        grid.innerHTML = agents.length
          ? agents.map(buildAgentCard).join("")
          : `<div class="empty-state">No agents configured.</div>`;
      }
    } catch (_err) {
      // Silently ignore polling errors
    }
  }, 30_000);

  // ---- DESTROY ----
  function destroy() {
    disposed = true;
    root.removeEventListener("click", handleClick);
    if (agentPollingId !== null) {
      window.clearInterval(agentPollingId);
      agentPollingId = null;
    }
    if (clockId !== null) {
      window.clearInterval(clockId);
      clockId = null;
    }
  }

  return { destroy };
}
