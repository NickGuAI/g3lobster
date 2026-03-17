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
  listMcpServers,
  listCronTasks,
  listGlobalKnowledge,
  restartAgent,
  runCronTask,
  startAgent,
  startBridge,
  stopAgent,
  stopBridge,
  updateAgent,
  updateAgentMemory,
  updateBoardTask,
  updateAgentProcedures,
  updateCronTask,
  validateCronSchedule,
  updateGlobalProcedures,
  updateGlobalUserMemory,
  triggerHeartbeat,
} from "./api.js";

// ─── Utilities ───────────────────────────────────────────────────────────────

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function parseMcpServers(raw) {
  const value = String(raw || "").trim();
  if (!value) return ["*"];
  return value.split(",").map((s) => s.trim()).filter(Boolean);
}

function parseDmAllowlist(raw) {
  const value = String(raw || "").trim();
  if (!value) return [];
  return value.split("\n").map((s) => s.trim()).filter(Boolean);
}

function formatDateTime(value) {
  if (!value) return "NEVER";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString();
}

function formatRelativeTime(value) {
  if (!value) return "NEVER";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return formatDateTime(value);
  const diffMs = d.getTime() - Date.now();
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

function isRunningState(state) {
  return !["stopped", "dead", "failed", "canceled", "error"].includes(String(state || "").toLowerCase());
}

function stateBadge(state, pending) {
  if (pending === "start" || pending === "restart") {
    return `<span class="badge badge-warning">STARTING...</span>`;
  }
  if (pending === "stop") {
    return `<span class="badge badge-warning">STOPPING...</span>`;
  }
  const s = String(state || "unknown").toLowerCase();
  if (s === "running" || s === "active") {
    return `<span class="badge badge-online">&#9679; RUNNING</span>`;
  }
  if (s === "error" || s === "failed" || s === "dead") {
    return `<span class="badge badge-danger">&#9632; ${escapeHtml(s.toUpperCase())}</span>`;
  }
  return `<span class="badge badge-dim">&#9632; ${escapeHtml(s.toUpperCase())}</span>`;
}

function priorityBadge(priority) {
  const p = String(priority || "normal").toLowerCase();
  if (p === "critical") return `<span class="badge badge-danger">${escapeHtml(p.toUpperCase())}</span>`;
  if (p === "high") return `<span class="badge badge-warning">${escapeHtml(p.toUpperCase())}</span>`;
  return `<span class="badge badge-dim">${escapeHtml(p.toUpperCase())}</span>`;
}

function typeBadge(type) {
  return `<span class="badge badge-info">${escapeHtml(String(type || "chore").toUpperCase())}</span>`;
}

function currentTimeStr() {
  return new Date().toLocaleTimeString("en-US", { hour12: false });
}

// ─── Main render export ──────────────────────────────────────────────────────

export async function render(root, { onSetupChange }) {
  const AGENT_REFRESH_MS = 30_000;

  let disposed = false;
  let notice = null; // { tone: string, text: string }

  // Top-level tab state
  let topTab = "agents"; // "agents" | "board" | "cron" | "memory" | "settings"

  // Agent detail state
  let activeAgentId = null;
  let agentDetailTab = "info"; // "info" | "tasks" | "memory" | "procedures" | "sessions"
  let showAddAgentForm = false;

  // Data caches
  let agentsCache = [];
  let detailCache = {};
  let memoryCache = {};
  let proceduresCache = {};
  let sessionsCache = {};
  let transcriptCache = {};
  let allBoardTasks = [];
  let allCrons = [];
  let globalUserMemory = "";
  let globalProcedures = "";
  let globalKnowledge = [];
  let availableMcpServers = null;
  let setupStatus = null;

  // Pending lifecycle ops
  const pendingLifecycle = {};

  // Cron form state per agent
  const cronUiState = {};

  // Board add-task form visible
  let showAddTaskForm = false;
  // Cron add form visible
  let showAddCronForm = false;
  let cronFilterAgentId = "__all__";

  let refreshIntervalId = null;
  let rerenderInFlight = null;
  let rerenderQueued = false;

  // ── Helpers ────────────────────────────────────────────────────────────────

  function setNotice(tone, text) {
    notice = { tone, text };
  }

  function clearNotice() {
    notice = null;
  }

  function defaultCronFormState() {
    return {
      mode: "create",
      editTaskId: null,
      form: { schedule: "", instruction: "", dm_target: "", enabled: true },
      validation: { checkedSchedule: "", validating: false, valid: null, next_run: null, error: "" },
      validationTimerId: null,
    };
  }

  function ensureCronUi(agentId) {
    if (!cronUiState[agentId]) cronUiState[agentId] = defaultCronFormState();
    return cronUiState[agentId];
  }

  function resetCronForm(agentId) {
    cronUiState[agentId] = defaultCronFormState();
  }

  function lifecyclePending(agentId) {
    return pendingLifecycle[agentId] || null;
  }

  async function queueRerender(force = false) {
    if (disposed) return;
    const ae = document.activeElement;
    if (!force && ae && root.contains(ae) && ["INPUT", "TEXTAREA", "SELECT"].includes(ae.tagName)) {
      rerenderQueued = true;
      return;
    }
    if (rerenderInFlight) {
      rerenderQueued = true;
      return rerenderInFlight;
    }
    rerenderInFlight = (async () => {
      do {
        rerenderQueued = false;
        await rerender();
      } while (rerenderQueued && !disposed);
    })();
    try {
      await rerenderInFlight;
    } finally {
      rerenderInFlight = null;
    }
  }

  async function refreshAgents() {
    agentsCache = await listAgents();
    if (activeAgentId && !agentsCache.some((a) => a.id === activeAgentId)) {
      activeAgentId = agentsCache.length ? agentsCache[0].id : null;
    }
    if (!activeAgentId && agentsCache.length) {
      activeAgentId = agentsCache[0].id;
    }
  }

  async function ensureMcpServers() {
    if (availableMcpServers !== null) return availableMcpServers;
    try {
      const payload = await listMcpServers();
      availableMcpServers = payload.servers || [];
    } catch {
      availableMcpServers = [];
    }
    return availableMcpServers;
  }

  async function ensureGlobalMemory() {
    const [memPayload, procPayload, knowPayload] = await Promise.all([
      getGlobalUserMemory(),
      getGlobalProcedures(),
      listGlobalKnowledge(),
    ]);
    globalUserMemory = memPayload.content || "";
    globalProcedures = procPayload.content || "";
    globalKnowledge = knowPayload.items || [];
  }

  async function refreshAllCrons() {
    allCrons = await listAllCrons();
  }

  async function refreshBoardTasks() {
    allBoardTasks = await listBoardTasks({ limit: 500 });
  }

  // ── Markup helpers ─────────────────────────────────────────────────────────

  function noticeMarkup() {
    if (!notice) return "";
    const cls = notice.tone === "success" ? "notice-ok" : notice.tone === "error" ? "notice-error" : "notice-info";
    const prefix = notice.tone === "success" ? "[OK]" : notice.tone === "error" ? "[ERROR]" : "[INFO]";
    return `<div class="console-notice ${cls}">${escapeHtml(prefix)} ${escapeHtml(notice.text)}</div>`;
  }

  function statusBarMarkup() {
    const time = currentTimeStr();
    const running = agentsCache.filter((a) => isRunningState(a.state)).length;
    return `
      <div class="status-bar">
        <span class="status-bar-title">// G3LOBSTER AGENT CONSOLE //</span>
        <span class="status-bar-stat">AGENTS: <strong>${escapeHtml(String(agentsCache.length))}</strong></span>
        <span class="status-bar-stat">RUNNING: <strong class="${running > 0 ? "text-green" : "text-dim"}">${escapeHtml(String(running))}</strong></span>
        <span class="status-bar-time">${escapeHtml(time)}</span>
      </div>
    `;
  }

  function topTabsMarkup() {
    const tabs = [
      ["agents", "AGENTS"],
      ["board", "BOARD"],
      ["cron", "CRON"],
      ["memory", "MEMORY"],
      ["settings", "SETTINGS"],
    ];
    return `
      <nav class="top-tabs">
        ${tabs.map(([id, label]) => `
          <button class="top-tab-btn ${topTab === id ? "active" : ""}" data-top-tab="${escapeHtml(id)}">${escapeHtml(label)}</button>
        `).join("")}
      </nav>
    `;
  }

  // ── AGENTS TAB ─────────────────────────────────────────────────────────────

  function agentCardMarkup(agent) {
    const pending = lifecyclePending(agent.id);
    const running = isRunningState(agent.state);
    const isActive = agent.id === activeAgentId;
    const cardClass = `agent-card ${isActive ? "agent-card--active" : ""} ${running ? "agent-card--running" : ""}`;

    return `
      <div class="${cardClass}" data-agent-id="${escapeHtml(agent.id)}">
        <div class="agent-card-header">
          <span class="agent-card-emoji">${escapeHtml(agent.emoji || "🤖")}</span>
          <span class="agent-card-name">${escapeHtml(agent.name)}</span>
          ${stateBadge(agent.state, pending)}
        </div>
        <div class="agent-card-meta">
          <span class="agent-meta-item">MODEL: ${escapeHtml(agent.model || "—")}</span>
          <span class="agent-meta-item">ID: ${escapeHtml(String(agent.id || "").slice(0, 8))}</span>
        </div>
        <div class="agent-card-actions">
          <button class="btn-terminal btn-sm" data-action="agent-view" data-agent-id="${escapeHtml(agent.id)}">[VIEW]</button>
          <button class="btn-terminal btn-sm" data-action="heartbeat" data-agent-id="${escapeHtml(agent.id)}">[&#9829; HB]</button>
          ${running
            ? `<button class="btn-danger btn-sm" data-action="stop" data-agent-id="${escapeHtml(agent.id)}" ${pending ? "disabled" : ""}>[STOP]</button>`
            : `<button class="btn-terminal btn-sm" data-action="start" data-agent-id="${escapeHtml(agent.id)}" ${pending ? "disabled" : ""}>[START]</button>`
          }
          <button class="btn-ghost btn-sm" data-action="restart" data-agent-id="${escapeHtml(agent.id)}" ${pending ? "disabled" : ""}>[RESTART]</button>
        </div>
      </div>
    `;
  }

  function addAgentFormMarkup() {
    const mcpList = availableMcpServers || [];
    return `
      <div class="panel-modal">
        <div class="panel-modal-header">
          <span class="panel-modal-title">> ADD AGENT</span>
          <button class="btn-ghost btn-sm" data-action="hide-add-agent">[X CLOSE]</button>
        </div>
        <form id="create-agent-form" class="agent-form">
          <div class="form-grid-2">
            <div class="field">
              <label class="field-label">NAME</label>
              <input class="field-input" name="name" placeholder="IRIS" required />
            </div>
            <div class="field">
              <label class="field-label">EMOJI</label>
              <input class="field-input" name="emoji" value="🤖" />
            </div>
            <div class="field">
              <label class="field-label">MODEL</label>
              <input class="field-input" name="model" value="gemini" />
            </div>
            <div class="field">
              <label class="field-label">SPACE ID</label>
              <input class="field-input" name="space_id" placeholder="spaces/AAAA..." />
            </div>
            <div class="field">
              <label class="field-label">BRIDGE ENABLED</label>
              <select class="field-select" name="bridge_enabled">
                <option value="false">false</option>
                <option value="true">true</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">HEARTBEAT ENABLED</label>
              <select class="field-select" name="heartbeat_enabled">
                <option value="false">false</option>
                <option value="true">true</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">HEARTBEAT INTERVAL (s)</label>
              <input class="field-input" type="number" name="heartbeat_interval_s" value="300" min="30" />
            </div>
            <div class="field">
              <label class="field-label">MCP SERVERS</label>
              ${mcpList.length
                ? `<div class="mcp-checklist">
                    ${mcpList.map((srv) => `<label class="mcp-option"><input type="checkbox" name="mcp_server_item" value="${escapeHtml(srv)}" /> ${escapeHtml(srv)}</label>`).join("")}
                    <label class="mcp-option"><input type="checkbox" name="mcp_server_wildcard" checked /> * (ALL)</label>
                  </div>`
                : `<input class="field-input" name="mcp_servers" value="*" placeholder="* or server1,server2" />`
              }
            </div>
          </div>
          <div class="field">
            <label class="field-label">SOUL.md — PERSONA &amp; TONE</label>
            <textarea class="field-textarea" name="soul" rows="6" placeholder="You are Iris, a helpful agent..."></textarea>
          </div>
          <div class="form-actions">
            <button class="btn-primary" type="submit">[+ CREATE AGENT]</button>
            <button class="btn-ghost" type="button" data-action="hide-add-agent">[CANCEL]</button>
          </div>
        </form>
      </div>
    `;
  }

  function agentDetailSubTabsMarkup() {
    const tabs = [
      ["info", "INFO"],
      ["tasks", "TASKS"],
      ["memory", "MEMORY"],
      ["procedures", "PROCEDURES"],
      ["sessions", "SESSIONS"],
    ];
    return `
      <div class="sub-tabs">
        ${tabs.map(([id, label]) => `
          <button class="sub-tab-btn ${agentDetailTab === id ? "active" : ""}" data-agent-subtab="${escapeHtml(id)}">${escapeHtml(label)}</button>
        `).join("")}
      </div>
    `;
  }

  function agentInfoSubTab(agent) {
    const detail = detailCache[agent.id] || agent;
    const mcpList = availableMcpServers || [];
    return `
      <form class="persona-form agent-form" data-agent-id="${escapeHtml(agent.id)}">
        <div class="form-grid-2">
          <div class="field">
            <label class="field-label">NAME</label>
            <input class="field-input" name="name" value="${escapeHtml(detail.name || "")}" />
          </div>
          <div class="field">
            <label class="field-label">EMOJI</label>
            <input class="field-input" name="emoji" value="${escapeHtml(detail.emoji || "🤖")}" />
          </div>
          <div class="field">
            <label class="field-label">MODEL</label>
            <input class="field-input" name="model" value="${escapeHtml(detail.model || "gemini")}" />
          </div>
          <div class="field">
            <label class="field-label">SPACE ID</label>
            <input class="field-input" name="space_id" value="${escapeHtml(detail.space_id || "")}" placeholder="spaces/AAAA..." />
          </div>
          <div class="field">
            <label class="field-label">BRIDGE ENABLED</label>
            <select class="field-select" name="bridge_enabled">
              <option value="true" ${detail.bridge_enabled ? "selected" : ""}>true</option>
              <option value="false" ${!detail.bridge_enabled ? "selected" : ""}>false</option>
            </select>
          </div>
          <div class="field">
            <label class="field-label">ENABLED</label>
            <select class="field-select" name="enabled">
              <option value="true" ${detail.enabled !== false ? "selected" : ""}>true</option>
              <option value="false" ${detail.enabled === false ? "selected" : ""}>false</option>
            </select>
          </div>
          <div class="field">
            <label class="field-label">HEARTBEAT ENABLED</label>
            <select class="field-select" name="heartbeat_enabled">
              <option value="true" ${detail.heartbeat_enabled === true ? "selected" : ""}>true</option>
              <option value="false" ${detail.heartbeat_enabled !== true ? "selected" : ""}>false</option>
            </select>
          </div>
          <div class="field">
            <label class="field-label">HEARTBEAT INTERVAL (s)</label>
            <input class="field-input" type="number" name="heartbeat_interval_s" min="30" value="${escapeHtml(String(detail.heartbeat_interval_s || 300))}" />
          </div>
          <div class="field">
            <label class="field-label">MCP SERVERS</label>
            ${mcpList.length
              ? `<div class="mcp-checklist">
                  ${mcpList.map((srv) => {
                    const checked = (detail.mcp_servers || ["*"]).includes("*") || (detail.mcp_servers || []).includes(srv) ? "checked" : "";
                    return `<label class="mcp-option"><input type="checkbox" name="mcp_server_item" value="${escapeHtml(srv)}" ${checked} /> ${escapeHtml(srv)}</label>`;
                  }).join("")}
                  <label class="mcp-option"><input type="checkbox" name="mcp_server_wildcard" ${(detail.mcp_servers || ["*"]).includes("*") ? "checked" : ""} /> * (ALL)</label>
                </div>`
              : `<input class="field-input" name="mcp_servers" value="${escapeHtml((detail.mcp_servers || ["*"]).join(", "))}" />`
            }
          </div>
        </div>
        <div class="field">
          <label class="field-label">SOUL.md</label>
          <textarea class="field-textarea" name="soul" rows="8">${escapeHtml(detail.soul || "")}</textarea>
        </div>
        <div class="field">
          <label class="field-label">DM ALLOWLIST (ONE PER LINE)</label>
          <textarea class="field-textarea" name="dm_allowlist" rows="4" placeholder="users/abc123&#10;user@example.com">${escapeHtml((detail.dm_allowlist || []).join("\n"))}</textarea>
        </div>
        <div class="form-actions">
          <button class="btn-primary" type="submit">[SAVE PERSONA]</button>
          <button class="btn-ghost" type="button" data-action="heartbeat" data-agent-id="${escapeHtml(agent.id)}">[&#9829; TRIGGER HEARTBEAT]</button>
          <a class="btn-ghost" href="${exportAgentUrl(agent.id)}" download="${escapeHtml(agent.id)}.g3agent">[EXPORT]</a>
          <button class="btn-danger" type="button" data-action="delete" data-agent-id="${escapeHtml(agent.id)}">[DELETE AGENT]</button>
        </div>
      </form>
    `;
  }

  function agentTasksSubTab(agent) {
    const tasks = allBoardTasks.filter((t) => t.agent_id === agent.id);
    const byStatus = { todo: [], in_progress: [], blocked: [], done: [] };
    for (const t of tasks) {
      const s = t.status || "todo";
      if (byStatus[s]) byStatus[s].push(t);
      else byStatus.todo.push(t);
    }

    function col(title, colTasks, colKey) {
      const colClass = colKey === "done" ? "kanban-col--done" : colKey === "blocked" ? "kanban-col--blocked" : colKey === "in_progress" ? "kanban-col--active" : "";
      const cards = colTasks.map((t) => `
        <div class="kanban-card">
          <div class="kanban-card-title">${escapeHtml(t.title || "")}</div>
          <div class="kanban-card-meta">
            ${typeBadge(t.type)} ${priorityBadge(t.priority)}
          </div>
          <div class="kanban-card-actions">
            ${colKey !== "in_progress" ? `<button class="btn-ghost btn-xs" data-action="task-set-status" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(t.id)}" data-next-status="in_progress">[START]</button>` : ""}
            ${colKey !== "blocked" ? `<button class="btn-ghost btn-xs" data-action="task-set-status" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(t.id)}" data-next-status="blocked">[BLOCK]</button>` : ""}
            ${colKey !== "done" ? `<button class="btn-terminal btn-xs" data-action="task-complete" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(t.id)}">[DONE]</button>` : ""}
            <button class="btn-danger btn-xs" data-action="task-delete" data-agent-id="${escapeHtml(agent.id)}" data-task-id="${escapeHtml(t.id)}">[DEL]</button>
          </div>
        </div>
      `).join("");
      return `
        <div class="kanban-col ${colClass}">
          <div class="kanban-col-header">${escapeHtml(title)} <span class="badge badge-dim">${colTasks.length}</span></div>
          <div class="kanban-col-body">${cards || `<p class="empty-state">// EMPTY //</p>`}</div>
        </div>
      `;
    }

    return `
      <div class="sub-tab-toolbar">
        <button class="btn-primary btn-sm" data-action="toggle-add-task" data-agent-id="${escapeHtml(agent.id)}">[+ ADD TASK]</button>
        <button class="btn-ghost btn-sm" data-action="load-board-tasks" data-agent-id="${escapeHtml(agent.id)}">[REFRESH]</button>
      </div>
      ${showAddTaskForm ? addTaskFormMarkup(agent, agentsCache) : ""}
      <div class="kanban-board">
        ${col("TODO", byStatus.todo, "todo")}
        ${col("IN PROGRESS", byStatus.in_progress, "in_progress")}
        ${col("BLOCKED", byStatus.blocked, "blocked")}
        ${col("DONE", byStatus.done, "done")}
      </div>
    `;
  }

  function agentMemorySubTab(agent) {
    return `
      <div class="field">
        <label class="field-label">// AGENT MEMORY (MEMORY.md)</label>
        <textarea class="field-textarea field-textarea--tall" data-memory-for="${escapeHtml(agent.id)}">${escapeHtml(memoryCache[agent.id] || "")}</textarea>
      </div>
      <div class="form-actions">
        <button class="btn-ghost btn-sm" data-action="load-memory" data-agent-id="${escapeHtml(agent.id)}">[LOAD]</button>
        <button class="btn-primary btn-sm" data-action="save-memory" data-agent-id="${escapeHtml(agent.id)}">[SAVE MEMORY]</button>
      </div>
    `;
  }

  function agentProceduresSubTab(agent) {
    return `
      <div class="field">
        <label class="field-label">// AGENT PROCEDURES (PROCEDURES.md)</label>
        <textarea class="field-textarea field-textarea--tall" data-procedures-for="${escapeHtml(agent.id)}">${escapeHtml(proceduresCache[agent.id] || "")}</textarea>
      </div>
      <div class="form-actions">
        <button class="btn-ghost btn-sm" data-action="load-procedures" data-agent-id="${escapeHtml(agent.id)}">[LOAD]</button>
        <button class="btn-primary btn-sm" data-action="save-procedures" data-agent-id="${escapeHtml(agent.id)}">[SAVE PROCEDURES]</button>
      </div>
    `;
  }

  function agentSessionsSubTab(agent) {
    const sessions = sessionsCache[agent.id] || [];
    const options = sessions.length
      ? sessions.map((sid) => `<option value="${escapeHtml(sid)}">${escapeHtml(sid)}</option>`).join("")
      : `<option value="">NO SESSIONS</option>`;
    const transcript = transcriptCache[agent.id];
    const msgHtml = transcript ? renderTranscript(transcript) : `<p class="empty-state">// SELECT A SESSION TO VIEW TRANSCRIPT //</p>`;

    return `
      <div class="form-grid-2">
        <div class="field">
          <label class="field-label">SESSIONS</label>
          <select class="field-select" data-sessions-for="${escapeHtml(agent.id)}">${options}</select>
        </div>
      </div>
      <div class="form-actions">
        <button class="btn-ghost btn-sm" data-action="load-sessions" data-agent-id="${escapeHtml(agent.id)}">[REFRESH SESSIONS]</button>
        <button class="btn-terminal btn-sm" data-action="load-session" data-agent-id="${escapeHtml(agent.id)}">[OPEN SESSION]</button>
      </div>
      <div class="transcript-panel">${msgHtml}</div>
    `;
  }

  function renderTranscript(cached) {
    if (!cached || typeof cached !== "object") return `<p class="empty-state">// NO TRANSCRIPT //</p>`;
    const entries = Array.isArray(cached.entries) ? cached.entries : [];
    const messages = entries.filter((e) => e && e.type === "message" && e.message);
    if (!messages.length) return `<p class="empty-state">// NO MESSAGES //</p>`;
    return messages.map((e) => {
      const role = String(e.message.role || "unknown");
      const content = escapeHtml(String(e.message.content || ""));
      const ts = e.timestamp ? escapeHtml(e.timestamp.replace("T", " ").slice(0, 19)) : "";
      return `
        <div class="msg msg-${escapeHtml(role)}">
          <div class="msg-header"><span class="msg-role">${escapeHtml(role.toUpperCase())}</span><span class="msg-ts">${ts}</span></div>
          <div class="msg-body"><p>${content}</p></div>
        </div>
      `;
    }).join("");
  }

  function agentDetailMarkup(agent) {
    const pending = lifecyclePending(agent.id);
    const running = isRunningState(agent.state);

    let subContent = "";
    if (agentDetailTab === "info") subContent = agentInfoSubTab(agent);
    else if (agentDetailTab === "tasks") subContent = agentTasksSubTab(agent);
    else if (agentDetailTab === "memory") subContent = agentMemorySubTab(agent);
    else if (agentDetailTab === "procedures") subContent = agentProceduresSubTab(agent);
    else if (agentDetailTab === "sessions") subContent = agentSessionsSubTab(agent);

    return `
      <div class="agent-detail-panel card-terminal">
        <div class="agent-detail-hero">
          <div class="agent-detail-identity">
            <span class="agent-detail-emoji">${escapeHtml(agent.emoji || "🤖")}</span>
            <div>
              <div class="agent-detail-name">${escapeHtml(agent.name)}</div>
              <div class="agent-detail-id">ID: ${escapeHtml(agent.id)}</div>
            </div>
            ${stateBadge(agent.state, pending)}
          </div>
          <div class="agent-detail-controls">
            ${running
              ? `<button class="btn-danger btn-sm" data-action="stop" data-agent-id="${escapeHtml(agent.id)}" ${pending ? "disabled" : ""}>[STOP]</button>`
              : `<button class="btn-terminal btn-sm" data-action="start" data-agent-id="${escapeHtml(agent.id)}" ${pending ? "disabled" : ""}>[START]</button>`
            }
            <button class="btn-ghost btn-sm" data-action="restart" data-agent-id="${escapeHtml(agent.id)}" ${pending ? "disabled" : ""}>[RESTART]</button>
            <button class="btn-terminal btn-sm" data-action="heartbeat" data-agent-id="${escapeHtml(agent.id)}">[&#9829; HEARTBEAT]</button>
            <button class="btn-ghost btn-sm" data-action="close-agent-detail">[&#10005; CLOSE]</button>
          </div>
        </div>
        ${agentDetailSubTabsMarkup()}
        <div class="sub-tab-content">${subContent}</div>
      </div>
    `;
  }

  function agentsTabMarkup() {
    return `
      <div class="tab-section">
        <div class="tab-section-header">
          <span class="section-title">> LOBSTER TANK — ACTIVE AGENTS</span>
          <button class="btn-primary" data-action="toggle-add-agent">[+ ADD AGENT]</button>
        </div>
        ${showAddAgentForm ? addAgentFormMarkup() : ""}
        <div class="agent-grid">
          ${agentsCache.length
            ? agentsCache.map((a) => agentCardMarkup(a)).join("")
            : `<p class="empty-state">// NO AGENTS DEPLOYED — ADD ONE ABOVE //</p>`
          }
        </div>
        ${activeAgentId && agentsCache.some((a) => a.id === activeAgentId)
          ? agentDetailMarkup(agentsCache.find((a) => a.id === activeAgentId))
          : ""
        }
      </div>
    `;
  }

  // ── BOARD TAB ──────────────────────────────────────────────────────────────

  function addTaskFormMarkup(agentCtx, agents) {
    const agentOptions = agents.map((a) =>
      `<option value="${escapeHtml(a.id)}" ${agentCtx && a.id === agentCtx.id ? "selected" : ""}>${escapeHtml(a.emoji || "")} ${escapeHtml(a.name)}</option>`
    ).join("");

    return `
      <div class="panel-modal">
        <div class="panel-modal-header">
          <span class="panel-modal-title">> ADD TASK</span>
          <button class="btn-ghost btn-sm" data-action="toggle-add-task">[X CLOSE]</button>
        </div>
        <form id="add-task-form" class="agent-form">
          <div class="form-grid-2">
            <div class="field" style="grid-column: 1 / -1;">
              <label class="field-label">TITLE</label>
              <input class="field-input" name="title" placeholder="INVESTIGATE INCIDENT REPORT" required />
            </div>
            <div class="field">
              <label class="field-label">TYPE</label>
              <select class="field-select" name="type">
                <option value="chore">CHORE</option>
                <option value="feature">FEATURE</option>
                <option value="bug">BUG</option>
                <option value="research">RESEARCH</option>
                <option value="reminder">REMINDER</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">PRIORITY</label>
              <select class="field-select" name="priority">
                <option value="normal">NORMAL</option>
                <option value="high">HIGH</option>
                <option value="critical">CRITICAL</option>
                <option value="low">LOW</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">ASSIGN TO AGENT</label>
              <select class="field-select" name="agent_id">
                <option value="">— UNASSIGNED —</option>
                ${agentOptions}
              </select>
            </div>
            <div class="field">
              <label class="field-label">STATUS</label>
              <select class="field-select" name="status">
                <option value="todo">TODO</option>
                <option value="in_progress">IN PROGRESS</option>
                <option value="blocked">BLOCKED</option>
              </select>
            </div>
          </div>
          <div class="form-actions">
            <button class="btn-primary" type="submit">[+ CREATE TASK]</button>
            <button class="btn-ghost" type="button" data-action="toggle-add-task">[CANCEL]</button>
          </div>
        </form>
      </div>
    `;
  }

  function boardTabMarkup() {
    const byStatus = { todo: [], in_progress: [], blocked: [], done: [] };
    for (const t of allBoardTasks) {
      const s = t.status || "todo";
      if (byStatus[s]) byStatus[s].push(t);
      else byStatus.todo.push(t);
    }

    function col(title, colTasks, colKey) {
      const colClass = colKey === "done" ? "kanban-col--done" : colKey === "blocked" ? "kanban-col--blocked" : colKey === "in_progress" ? "kanban-col--active" : "";
      const agent = (id) => agentsCache.find((a) => a.id === id);
      const cards = colTasks.map((t) => {
        const ag = agent(t.agent_id);
        const agBadge = ag ? `<span class="badge badge-info">${escapeHtml(ag.emoji || "")} ${escapeHtml(ag.name)}</span>` : "";
        return `
          <div class="kanban-card">
            <div class="kanban-card-title">${escapeHtml(t.title || "")}</div>
            <div class="kanban-card-meta">
              ${typeBadge(t.type)} ${priorityBadge(t.priority)} ${agBadge}
            </div>
            <div class="kanban-card-actions">
              ${colKey !== "in_progress" ? `<button class="btn-ghost btn-xs" data-action="task-set-status" data-task-id="${escapeHtml(t.id)}" data-next-status="in_progress">[START]</button>` : ""}
              ${colKey !== "blocked" ? `<button class="btn-ghost btn-xs" data-action="task-set-status" data-task-id="${escapeHtml(t.id)}" data-next-status="blocked">[BLOCK]</button>` : ""}
              ${colKey !== "done" ? `<button class="btn-terminal btn-xs" data-action="task-complete-board" data-task-id="${escapeHtml(t.id)}">[DONE]</button>` : ""}
              <button class="btn-danger btn-xs" data-action="task-delete-board" data-task-id="${escapeHtml(t.id)}">[DEL]</button>
            </div>
          </div>
        `;
      }).join("");
      return `
        <div class="kanban-col ${colClass}">
          <div class="kanban-col-header">${escapeHtml(title)} <span class="badge badge-dim">${colTasks.length}</span></div>
          <div class="kanban-col-body">${cards || `<p class="empty-state">// EMPTY //</p>`}</div>
        </div>
      `;
    }

    return `
      <div class="tab-section">
        <div class="tab-section-header">
          <span class="section-title">> TASK BOARD — ALL AGENTS</span>
          <div class="header-actions">
            <button class="btn-primary" data-action="toggle-add-task">[+ ADD TASK]</button>
            <button class="btn-ghost btn-sm" data-action="refresh-board">[REFRESH]</button>
          </div>
        </div>
        ${showAddTaskForm ? addTaskFormMarkup(null, agentsCache) : ""}
        <div class="kanban-board">
          ${col("TODO", byStatus.todo, "todo")}
          ${col("IN PROGRESS", byStatus.in_progress, "in_progress")}
          ${col("BLOCKED", byStatus.blocked, "blocked")}
          ${col("DONE", byStatus.done, "done")}
        </div>
      </div>
    `;
  }

  // ── CRON TAB ───────────────────────────────────────────────────────────────

  function addCronFormMarkup() {
    const agentOptions = agentsCache.map((a) =>
      `<option value="${escapeHtml(a.id)}">${escapeHtml(a.emoji || "")} ${escapeHtml(a.name)}</option>`
    ).join("");

    return `
      <div class="panel-modal">
        <div class="panel-modal-header">
          <span class="panel-modal-title">> ADD CRON JOB</span>
          <button class="btn-ghost btn-sm" data-action="toggle-add-cron">[X CLOSE]</button>
        </div>
        <form id="add-cron-form" class="agent-form">
          <div class="form-grid-2">
            <div class="field">
              <label class="field-label">AGENT</label>
              <select class="field-select" name="cron_agent_id" required>
                <option value="">— SELECT AGENT —</option>
                ${agentOptions}
              </select>
            </div>
            <div class="field">
              <label class="field-label">SCHEDULE (CRON EXPR)</label>
              <input class="field-input" name="schedule" placeholder="0 9 * * 1-5" required />
            </div>
            <div class="field">
              <label class="field-label">ENABLED</label>
              <select class="field-select" name="enabled">
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </div>
          </div>
          <div class="field">
            <label class="field-label">INSTRUCTION</label>
            <textarea class="field-textarea" name="instruction" rows="3" placeholder="GENERATE MORNING BRIEFING FOR NICK" required></textarea>
          </div>
          <div class="form-actions">
            <button class="btn-primary" type="submit">[+ CREATE CRON]</button>
            <button class="btn-ghost" type="button" data-action="toggle-add-cron">[CANCEL]</button>
          </div>
        </form>
      </div>
    `;
  }

  function cronTabMarkup() {
    const agentOptions = [
      `<option value="__all__" ${cronFilterAgentId === "__all__" ? "selected" : ""}>ALL AGENTS</option>`,
      ...agentsCache.map((a) =>
        `<option value="${escapeHtml(a.id)}" ${cronFilterAgentId === a.id ? "selected" : ""}>${escapeHtml(a.emoji || "")} ${escapeHtml(a.name)}</option>`
      ),
    ].join("");

    const filteredCrons = cronFilterAgentId === "__all__"
      ? allCrons
      : allCrons.filter((c) => c.agent_id === cronFilterAgentId);

    const agentById = (id) => agentsCache.find((a) => a.id === id);

    const cronRows = filteredCrons.map((c) => {
      const ag = agentById(c.agent_id);
      const agLabel = ag ? `${escapeHtml(ag.emoji || "")} ${escapeHtml(ag.name)}` : escapeHtml(c.agent_id || "?");
      const enabled = c.enabled !== false;
      return `
        <div class="cron-row ${enabled ? "cron-row--enabled" : "cron-row--disabled"}">
          <div class="cron-row-main">
            <code class="cron-schedule">${escapeHtml(c.schedule || "")}</code>
            <span class="badge ${enabled ? "badge-online" : "badge-dim"}">${enabled ? "ENABLED" : "DISABLED"}</span>
            <span class="cron-agent-label">${agLabel}</span>
          </div>
          <div class="cron-row-instruction">"${escapeHtml(String(c.instruction || "").slice(0, 120))}"</div>
          <div class="cron-row-times">
            LAST: ${escapeHtml(c.last_run ? formatRelativeTime(c.last_run) : "NEVER")} |
            NEXT: ${escapeHtml(c.next_run ? formatRelativeTime(c.next_run) : "—")}
          </div>
          <div class="cron-row-actions">
            <button class="btn-terminal btn-xs" data-action="cron-run-now" data-agent-id="${escapeHtml(c.agent_id)}" data-task-id="${escapeHtml(c.id)}">[RUN NOW]</button>
            <button class="btn-ghost btn-xs" data-action="cron-toggle-enabled" data-agent-id="${escapeHtml(c.agent_id)}" data-task-id="${escapeHtml(c.id)}" data-enabled="${enabled ? "1" : "0"}">[${enabled ? "DISABLE" : "ENABLE"}]</button>
            <button class="btn-danger btn-xs" data-action="cron-delete" data-agent-id="${escapeHtml(c.agent_id)}" data-task-id="${escapeHtml(c.id)}">[DELETE]</button>
          </div>
        </div>
      `;
    }).join("");

    return `
      <div class="tab-section">
        <div class="tab-section-header">
          <span class="section-title">> CRON JOBS</span>
          <div class="header-actions">
            <button class="btn-primary" data-action="toggle-add-cron">[+ ADD CRON]</button>
            <button class="btn-ghost btn-sm" data-action="refresh-crons">[REFRESH]</button>
          </div>
        </div>
        <div class="cron-filter-bar">
          <label class="field-label">FILTER BY AGENT:</label>
          <select class="field-select field-select--inline" data-cron-filter>
            ${agentOptions}
          </select>
          <span class="badge badge-dim">${filteredCrons.length} JOBS</span>
        </div>
        ${showAddCronForm ? addCronFormMarkup() : ""}
        <div class="cron-list">
          ${filteredCrons.length
            ? cronRows
            : `<p class="empty-state">// NO CRON JOBS — ADD ONE ABOVE //</p>`
          }
        </div>
      </div>
    `;
  }

  // ── MEMORY TAB ─────────────────────────────────────────────────────────────

  function memoryTabMarkup() {
    return `
      <div class="tab-section">
        <div class="tab-section-header">
          <span class="section-title">> GLOBAL MEMORY</span>
          <div class="header-actions">
            <button class="btn-ghost btn-sm" data-action="reload-global-memory">[RELOAD]</button>
            <button class="btn-primary btn-sm" data-action="save-global-memory">[SAVE ALL]</button>
          </div>
        </div>
        <div class="memory-grid">
          <div class="field">
            <label class="field-label">// USER MEMORY (data/.memory/USER.md)</label>
            <textarea class="field-textarea field-textarea--tall" id="global-user-memory">${escapeHtml(globalUserMemory)}</textarea>
          </div>
          <div class="field">
            <label class="field-label">// GLOBAL PROCEDURES (data/.memory/PROCEDURES.md)</label>
            <textarea class="field-textarea field-textarea--tall" id="global-procedures">${escapeHtml(globalProcedures)}</textarea>
          </div>
        </div>
        <p class="field-hint">KNOWLEDGE FILES: ${escapeHtml(globalKnowledge.join(", ") || "(NONE)")}</p>
      </div>
    `;
  }

  // ── SETTINGS TAB ───────────────────────────────────────────────────────────

  function settingsTabMarkup() {
    const status = setupStatus || {};
    const bridgeCount = (status.agent_bridges || []).filter((b) => b.is_running).length;
    const totalBridges = (status.agent_bridges || []).length;

    return `
      <div class="tab-section">
        <div class="tab-section-header">
          <span class="section-title">> SYSTEM SETTINGS</span>
        </div>
        <div class="settings-grid">
          <div class="card-terminal settings-card">
            <div class="settings-card-title">> SETUP STATUS</div>
            <div class="settings-row"><span class="settings-label">CREDENTIALS:</span> <span class="badge ${status.credentials_ok ? "badge-online" : "badge-danger"}">${status.credentials_ok ? "OK" : "NOT SET"}</span></div>
            <div class="settings-row"><span class="settings-label">AUTH:</span> <span class="badge ${status.auth_ok ? "badge-online" : "badge-danger"}">${status.auth_ok ? "OK" : "NOT AUTHENTICATED"}</span></div>
            <div class="settings-row"><span class="settings-label">SPACE ID:</span> <code>${escapeHtml(status.space_id || "(NOT SET)")}</code></div>
            <div class="settings-row"><span class="settings-label">BRIDGES RUNNING:</span> <span class="badge ${bridgeCount > 0 ? "badge-online" : "badge-dim"}">${bridgeCount}/${totalBridges}</span></div>
          </div>
          <div class="card-terminal settings-card">
            <div class="settings-card-title">> BRIDGE CONTROL</div>
            <div class="settings-row">
              <button class="btn-primary btn-sm" data-action="bridge-start-all">[START ALL BRIDGES]</button>
              <button class="btn-danger btn-sm" data-action="bridge-stop-all">[STOP ALL BRIDGES]</button>
            </div>
            <div class="bridge-table-wrap">
              ${bridgeTableMarkup(agentsCache, status.agent_bridges || [])}
            </div>
          </div>
          <div class="card-terminal settings-card">
            <div class="settings-card-title">> IMPORT / EXPORT</div>
            <div class="settings-row">
              <input type="file" id="import-agent-file" accept=".g3agent,.zip" style="display:none" />
              <button class="btn-ghost btn-sm" data-action="import-agent">[IMPORT AGENT]</button>
            </div>
          </div>
          <div class="card-terminal settings-card">
            <div class="settings-card-title">> DEBUG</div>
            <div class="settings-row">
              <button class="btn-ghost btn-sm" data-action="toggle-debug">[TOGGLE DEBUG MODE]</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function bridgeTableMarkup(agents, bridges) {
    if (!agents.length) return `<p class="empty-state">// NO AGENTS //</p>`;
    const bridgeMap = new Map((bridges || []).map((b) => [b.agent_id, b]));
    const rows = agents.map((a) => {
      const b = bridgeMap.get(a.id) || { agent_id: a.id, space_id: a.space_id || null, bridge_enabled: a.bridge_enabled || false, is_running: a.bridge_running || false };
      const running = b.is_running;
      return `
        <div class="bridge-row">
          <span class="bridge-agent">${escapeHtml(a.emoji || "")} ${escapeHtml(a.name)}</span>
          <code class="bridge-space">${escapeHtml(b.space_id || "(NOT SET)")}</code>
          <span class="badge ${running ? "badge-online" : "badge-dim"}">${running ? "RUNNING" : "STOPPED"}</span>
          <button class="btn-terminal btn-xs" data-action="bridge-start" data-agent-id="${escapeHtml(a.id)}" ${running ? "disabled" : ""}>[START]</button>
          <button class="btn-ghost btn-xs" data-action="bridge-stop" data-agent-id="${escapeHtml(a.id)}" ${!running ? "disabled" : ""}>[STOP]</button>
        </div>
      `;
    }).join("");
    return `<div class="bridge-table">${rows}</div>`;
  }

  // ── FULL RENDER ────────────────────────────────────────────────────────────

  async function rerender() {
    if (disposed) return;

    try {
      [agentsCache, setupStatus] = await Promise.all([listAgents(), getSetupStatus()]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      root.innerHTML = `<div class="console-notice notice-error">[ERROR] FAILED TO LOAD: ${escapeHtml(msg)}</div>`;
      return;
    }

    // Validate active agent
    if (activeAgentId && !agentsCache.some((a) => a.id === activeAgentId)) {
      activeAgentId = null;
    }

    // Load agent detail if viewing info tab
    if (activeAgentId && agentDetailTab === "info" && !detailCache[activeAgentId]) {
      try {
        detailCache[activeAgentId] = await getAgent(activeAgentId);
      } catch {
        detailCache[activeAgentId] = agentsCache.find((a) => a.id === activeAgentId) || {};
      }
    }

    await ensureMcpServers();

    // Tab-specific data loading
    if (topTab === "board") {
      try {
        await refreshBoardTasks();
      } catch {
        allBoardTasks = allBoardTasks.length ? allBoardTasks : [];
      }
    }

    if (topTab === "cron") {
      try {
        await refreshAllCrons();
      } catch {
        allCrons = allCrons.length ? allCrons : [];
      }
    }

    if (topTab === "memory") {
      try {
        await ensureGlobalMemory();
      } catch {
        // Keep existing values
      }
    }

    // If on agents tab and viewing tasks sub-tab, load board tasks for that agent
    if (topTab === "agents" && activeAgentId && agentDetailTab === "tasks") {
      try {
        const tasks = await listBoardTasks({ agent_id: activeAgentId, limit: 200 });
        // Merge into allBoardTasks keyed by id
        const existing = allBoardTasks.filter((t) => t.agent_id !== activeAgentId);
        allBoardTasks = [...existing, ...tasks];
      } catch {
        // Keep existing
      }
    }

    let tabContent = "";
    if (topTab === "agents") tabContent = agentsTabMarkup();
    else if (topTab === "board") tabContent = boardTabMarkup();
    else if (topTab === "cron") tabContent = cronTabMarkup();
    else if (topTab === "memory") tabContent = memoryTabMarkup();
    else if (topTab === "settings") tabContent = settingsTabMarkup();

    root.innerHTML = `
      <div class="console-root crt-screen">
        ${statusBarMarkup()}
        ${topTabsMarkup()}
        ${noticeMarkup()}
        <div class="console-content">
          ${tabContent}
        </div>
      </div>
    `;

    attachEventHandlers();
  }

  // ── EVENT HANDLERS ─────────────────────────────────────────────────────────

  function attachEventHandlers() {
    // Top-level tab buttons
    for (const btn of root.querySelectorAll("[data-top-tab]")) {
      btn.addEventListener("click", () => {
        topTab = btn.dataset.topTab || "agents";
        clearNotice();
        showAddAgentForm = false;
        showAddTaskForm = false;
        showAddCronForm = false;
        queueRerender();
      });
    }

    // Agent sub-tab buttons
    for (const btn of root.querySelectorAll("[data-agent-subtab]")) {
      btn.addEventListener("click", () => {
        agentDetailTab = btn.dataset.agentSubtab || "info";
        queueRerender();
      });
    }

    // Cron filter
    const cronFilterSelect = root.querySelector("[data-cron-filter]");
    if (cronFilterSelect) {
      cronFilterSelect.addEventListener("change", () => {
        cronFilterAgentId = cronFilterSelect.value || "__all__";
        queueRerender();
      });
    }

    // Create agent form
    root.querySelector("#create-agent-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const data = new FormData(form);
      const name = String(data.get("name") || "").trim();
      if (!name) { setNotice("error", "AGENT NAME IS REQUIRED."); queueRerender(true); return; }

      let mcpServersValue;
      const wildcard = form.querySelector("input[name='mcp_server_wildcard']");
      const items = form.querySelectorAll("input[name='mcp_server_item']:checked");
      if (wildcard !== null) {
        mcpServersValue = wildcard.checked ? ["*"] : (Array.from(items).map((cb) => cb.value).filter(Boolean) || ["*"]);
      } else {
        mcpServersValue = parseMcpServers(data.get("mcp_servers"));
      }

      try {
        const created = await createAgent({
          name,
          emoji: String(data.get("emoji") || "🤖").trim() || "🤖",
          model: String(data.get("model") || "gemini").trim() || "gemini",
          mcp_servers: mcpServersValue,
          soul: String(data.get("soul") || ""),
          space_id: String(data.get("space_id") || "").trim() || null,
          bridge_enabled: String(data.get("bridge_enabled") || "false") === "true",
          heartbeat_enabled: String(data.get("heartbeat_enabled") || "false") === "true",
          heartbeat_interval_s: Math.max(30, Number(data.get("heartbeat_interval_s") || 300)),
        });
        activeAgentId = created.id;
        agentDetailTab = "info";
        showAddAgentForm = false;
        setNotice("success", `AGENT "${escapeHtml(name)}" DEPLOYED.`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setNotice("error", `FAILED TO CREATE AGENT: ${msg}`);
      }
      queueRerender(true);
    });

    // Persona save form
    for (const form of root.querySelectorAll("form.persona-form")) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const agentId = form.dataset.agentId;
        if (!agentId) return;
        const data = new FormData(form);

        let mcpServersValue;
        const wildcard = form.querySelector("input[name='mcp_server_wildcard']");
        const items = form.querySelectorAll("input[name='mcp_server_item']:checked");
        if (wildcard !== null) {
          mcpServersValue = wildcard.checked ? ["*"] : (Array.from(items).map((cb) => cb.value).filter(Boolean) || ["*"]);
        } else {
          mcpServersValue = parseMcpServers(data.get("mcp_servers"));
        }

        try {
          detailCache[agentId] = await updateAgent(agentId, {
            name: String(data.get("name") || "").trim(),
            emoji: String(data.get("emoji") || "🤖").trim() || "🤖",
            model: String(data.get("model") || "gemini").trim() || "gemini",
            soul: String(data.get("soul") || ""),
            mcp_servers: mcpServersValue,
            enabled: String(data.get("enabled") || "true") === "true",
            dm_allowlist: parseDmAllowlist(data.get("dm_allowlist")),
            space_id: String(data.get("space_id") || "").trim() || null,
            bridge_enabled: String(data.get("bridge_enabled") || "false") === "true",
            heartbeat_enabled: String(data.get("heartbeat_enabled") || "false") === "true",
            heartbeat_interval_s: Math.max(30, Number(data.get("heartbeat_interval_s") || 300)),
          });
          setNotice("success", `AGENT "${escapeHtml(agentId)}" UPDATED.`);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          setNotice("error", `FAILED TO UPDATE: ${msg}`);
        }
        queueRerender(true);
      });
    }

    // Add task form (board tab)
    root.querySelector("#add-task-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const data = new FormData(form);
      const title = String(data.get("title") || "").trim();
      if (!title) { setNotice("error", "TASK TITLE REQUIRED."); queueRerender(true); return; }

      try {
        await createBoardTask({
          title,
          type: String(data.get("type") || "chore"),
          priority: String(data.get("priority") || "normal"),
          status: String(data.get("status") || "todo"),
          agent_id: String(data.get("agent_id") || "") || null,
          created_by: "human",
          metadata: {},
        });
        showAddTaskForm = false;
        await refreshBoardTasks();
        setNotice("success", "TASK CREATED.");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setNotice("error", `FAILED TO CREATE TASK: ${msg}`);
      }
      queueRerender(true);
    });

    // Add cron form
    root.querySelector("#add-cron-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const data = new FormData(form);
      const agentId = String(data.get("cron_agent_id") || "").trim();
      const schedule = String(data.get("schedule") || "").trim();
      const instruction = String(data.get("instruction") || "").trim();
      const enabled = String(data.get("enabled") || "true") === "true";

      if (!agentId) { setNotice("error", "SELECT AN AGENT."); queueRerender(true); return; }
      if (!schedule || !instruction) { setNotice("error", "SCHEDULE AND INSTRUCTION REQUIRED."); queueRerender(true); return; }

      try {
        await createCronTask(agentId, { schedule, instruction, enabled });
        showAddCronForm = false;
        await refreshAllCrons();
        setNotice("success", "CRON JOB CREATED.");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setNotice("error", `FAILED TO CREATE CRON: ${msg}`);
      }
      queueRerender(true);
    });

    // Data-action buttons
    for (const btn of root.querySelectorAll("button[data-action]")) {
      btn.addEventListener("click", async () => {
        const action = btn.dataset.action;
        const agentId = btn.dataset.agentId || null;
        const taskId = btn.dataset.taskId || null;

        try {
          // ── UI toggles ──
          if (action === "toggle-add-agent") {
            showAddAgentForm = !showAddAgentForm;
            queueRerender();
            return;
          }
          if (action === "hide-add-agent") {
            showAddAgentForm = false;
            queueRerender();
            return;
          }
          if (action === "toggle-add-task") {
            showAddTaskForm = !showAddTaskForm;
            queueRerender();
            return;
          }
          if (action === "toggle-add-cron") {
            showAddCronForm = !showAddCronForm;
            queueRerender();
            return;
          }
          if (action === "agent-view" && agentId) {
            activeAgentId = activeAgentId === agentId ? null : agentId;
            agentDetailTab = "info";
            queueRerender();
            return;
          }
          if (action === "close-agent-detail") {
            activeAgentId = null;
            queueRerender();
            return;
          }

          // ── Lifecycle ──
          if (action === "start" && agentId) {
            pendingLifecycle[agentId] = "start";
            await queueRerender();
            try {
              await startAgent(agentId);
              delete detailCache[agentId];
              setNotice("success", `STARTED AGENT ${agentId}.`);
            } finally { delete pendingLifecycle[agentId]; }
          } else if (action === "stop" && agentId) {
            pendingLifecycle[agentId] = "stop";
            await queueRerender();
            try {
              await stopAgent(agentId);
              delete detailCache[agentId];
              setNotice("info", `STOPPED AGENT ${agentId}.`);
            } finally { delete pendingLifecycle[agentId]; }
          } else if (action === "restart" && agentId) {
            pendingLifecycle[agentId] = "restart";
            await queueRerender();
            try {
              await restartAgent(agentId);
              delete detailCache[agentId];
              setNotice("success", `RESTARTED AGENT ${agentId}.`);
            } finally { delete pendingLifecycle[agentId]; }
          } else if (action === "delete" && agentId) {
            if (!window.confirm(`DELETE AGENT "${agentId}"? THIS CANNOT BE UNDONE.`)) return;
            await deleteAgent(agentId);
            delete detailCache[agentId];
            delete memoryCache[agentId];
            delete proceduresCache[agentId];
            delete sessionsCache[agentId];
            delete transcriptCache[agentId];
            if (cronUiState[agentId]?.validationTimerId) window.clearTimeout(cronUiState[agentId].validationTimerId);
            delete cronUiState[agentId];
            if (activeAgentId === agentId) activeAgentId = null;
            setNotice("info", `AGENT ${agentId} DELETED.`);
          }

          // ── Heartbeat ──
          else if (action === "heartbeat" && agentId) {
            try {
              await fetch(`/agents/${encodeURIComponent(agentId)}/heartbeat`, { method: "POST" });
              setNotice("success", `HEARTBEAT TRIGGERED FOR ${agentId}.`);
            } catch (err) {
              const msg = err instanceof Error ? err.message : String(err);
              setNotice("error", `HEARTBEAT FAILED: ${msg}`);
            }
          }

          // ── Memory ──
          else if (action === "load-memory" && agentId) {
            const payload = await getAgentMemory(agentId);
            memoryCache[agentId] = payload.content || "";
            setNotice("info", `MEMORY LOADED FOR ${agentId}.`);
          } else if (action === "save-memory" && agentId) {
            const area = root.querySelector(`textarea[data-memory-for='${CSS.escape(agentId)}']`);
            const content = area?.value ?? "";
            await updateAgentMemory(agentId, content);
            memoryCache[agentId] = content;
            setNotice("success", `MEMORY SAVED FOR ${agentId}.`);
          }

          // ── Procedures ──
          else if (action === "load-procedures" && agentId) {
            const payload = await getAgentProcedures(agentId);
            proceduresCache[agentId] = payload.content || "";
            setNotice("info", `PROCEDURES LOADED FOR ${agentId}.`);
          } else if (action === "save-procedures" && agentId) {
            const area = root.querySelector(`textarea[data-procedures-for='${CSS.escape(agentId)}']`);
            const content = area?.value ?? "";
            await updateAgentProcedures(agentId, content);
            proceduresCache[agentId] = content;
            setNotice("success", `PROCEDURES SAVED FOR ${agentId}.`);
          }

          // ── Sessions ──
          else if (action === "load-sessions" && agentId) {
            const payload = await listAgentSessions(agentId);
            sessionsCache[agentId] = payload.sessions || [];
            setNotice("info", `SESSIONS LOADED FOR ${agentId}.`);
          } else if (action === "load-session" && agentId) {
            const sel = root.querySelector(`select[data-sessions-for='${CSS.escape(agentId)}']`);
            const sessionId = sel?.value;
            if (!sessionId) { setNotice("error", "SELECT A SESSION FIRST."); }
            else {
              const payload = await getAgentSession(agentId, sessionId);
              transcriptCache[agentId] = payload;
              setNotice("info", `SESSION ${sessionId} LOADED.`);
            }
          }

          // ── Global memory ──
          else if (action === "reload-global-memory") {
            await ensureGlobalMemory();
            setNotice("info", "GLOBAL MEMORY RELOADED.");
          } else if (action === "save-global-memory") {
            const userVal = root.querySelector("#global-user-memory")?.value ?? "";
            const procVal = root.querySelector("#global-procedures")?.value ?? "";
            await Promise.all([updateGlobalUserMemory(userVal), updateGlobalProcedures(procVal)]);
            globalUserMemory = userVal;
            globalProcedures = procVal;
            setNotice("success", "GLOBAL MEMORY + PROCEDURES SAVED.");
          }

          // ── Bridge ──
          else if (action === "bridge-start-all") {
            await startBridge();
            setNotice("success", "ALL BRIDGES STARTED.");
            if (onSetupChange) await onSetupChange();
          } else if (action === "bridge-stop-all") {
            await stopBridge();
            setNotice("info", "ALL BRIDGES STOPPED.");
            if (onSetupChange) await onSetupChange();
          } else if (action === "bridge-start" && agentId) {
            await startBridge(agentId);
            setNotice("success", `BRIDGE STARTED FOR ${agentId}.`);
            if (onSetupChange) await onSetupChange();
          } else if (action === "bridge-stop" && agentId) {
            await stopBridge(agentId);
            setNotice("info", `BRIDGE STOPPED FOR ${agentId}.`);
            if (onSetupChange) await onSetupChange();
          }

          // ── Board tasks (agents sub-tab) ──
          else if (action === "toggle-add-task") {
            showAddTaskForm = !showAddTaskForm;
          } else if (action === "load-board-tasks" && agentId) {
            const tasks = await listBoardTasks({ agent_id: agentId, limit: 200 });
            const existing = allBoardTasks.filter((t) => t.agent_id !== agentId);
            allBoardTasks = [...existing, ...tasks];
            setNotice("info", `TASKS REFRESHED FOR ${agentId}.`);
          } else if (action === "task-set-status" && taskId) {
            const nextStatus = btn.dataset.nextStatus || "todo";
            await updateBoardTask(taskId, { status: nextStatus });
            await refreshBoardTasks();
            setNotice("success", `TASK MOVED TO ${nextStatus.toUpperCase()}.`);
          } else if (action === "task-complete" && taskId) {
            await completeBoardTask(taskId, null);
            await refreshBoardTasks();
            setNotice("success", "TASK MARKED COMPLETE.");
          } else if (action === "task-delete" && taskId) {
            await deleteBoardTask(taskId);
            await refreshBoardTasks();
            setNotice("info", "TASK DELETED.");
          }

          // ── Board tasks (board tab) ──
          else if (action === "refresh-board") {
            await refreshBoardTasks();
            setNotice("info", "BOARD REFRESHED.");
          } else if (action === "task-complete-board" && taskId) {
            await completeBoardTask(taskId, null);
            await refreshBoardTasks();
            setNotice("success", "TASK COMPLETE.");
          } else if (action === "task-delete-board" && taskId) {
            await deleteBoardTask(taskId);
            await refreshBoardTasks();
            setNotice("info", "TASK DELETED.");
          }

          // ── Cron ──
          else if (action === "refresh-crons") {
            await refreshAllCrons();
            setNotice("info", "CRON JOBS REFRESHED.");
          } else if (action === "cron-run-now" && agentId && taskId) {
            const result = await runCronTask(agentId, taskId);
            await refreshAllCrons();
            setNotice(result.status === "completed" ? "success" : "error", `CRON RUN ${(result.status || "?").toUpperCase()} (${formatDuration(result.duration_s)}).`);
          } else if (action === "cron-toggle-enabled" && agentId && taskId) {
            const currentlyEnabled = btn.dataset.enabled === "1";
            await updateCronTask(agentId, taskId, { enabled: !currentlyEnabled });
            await refreshAllCrons();
            setNotice("success", `CRON ${currentlyEnabled ? "DISABLED" : "ENABLED"}.`);
          } else if (action === "cron-delete" && agentId && taskId) {
            if (!window.confirm("DELETE THIS CRON JOB?")) return;
            await deleteCronTask(agentId, taskId);
            await refreshAllCrons();
            setNotice("info", "CRON JOB DELETED.");
          }

          // ── Settings ──
          else if (action === "toggle-debug") {
            try {
              const { toggleDebugMode } = await import("./api.js");
              await toggleDebugMode();
              setNotice("success", "DEBUG MODE TOGGLED.");
            } catch (err) {
              const msg = err instanceof Error ? err.message : String(err);
              setNotice("error", `DEBUG TOGGLE FAILED: ${msg}`);
            }
          } else if (action === "import-agent") {
            const fileInput = root.querySelector("#import-agent-file");
            if (!fileInput) return;
            fileInput.value = "";
            fileInput.onchange = async () => {
              const file = fileInput.files?.[0];
              if (!file) return;
              try {
                const result = await importAgent(file, false);
                activeAgentId = result.agent_id;
                setNotice("success", `IMPORTED AGENT "${result.agent_id}".`);
              } catch (err) {
                if (err.status === 409) {
                  setNotice("error", `AGENT ALREADY EXISTS. (${err.message})`);
                } else {
                  const msg = err instanceof Error ? err.message : String(err);
                  setNotice("error", `IMPORT FAILED: ${msg}`);
                }
              }
              queueRerender();
            };
            fileInput.click();
            return;
          }

        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          setNotice("error", `[${(action || "action").toUpperCase()}] FAILED: ${msg}`);
        }

        queueRerender();
      });
    }

    // focusout: flush queued rerender when user leaves input
    root.addEventListener("focusout", () => {
      setTimeout(() => {
        if (disposed) return;
        const ae = document.activeElement;
        if (!ae || !root.contains(ae) || !["INPUT", "TEXTAREA", "SELECT"].includes(ae.tagName)) {
          if (rerenderQueued) queueRerender(true);
        }
      }, 10);
    });
  }

  // ── Bootstrap ──────────────────────────────────────────────────────────────

  try {
    await ensureGlobalMemory();
  } catch {
    // Non-fatal on first load
  }

  await queueRerender();

  refreshIntervalId = window.setInterval(() => {
    if (!disposed) queueRerender();
  }, AGENT_REFRESH_MS);

  return {
    destroy() {
      disposed = true;
      if (refreshIntervalId !== null) {
        window.clearInterval(refreshIntervalId);
        refreshIntervalId = null;
      }
      for (const state of Object.values(cronUiState)) {
        if (state?.validationTimerId) window.clearTimeout(state.validationTimerId);
      }
    },
  };
}
