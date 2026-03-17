async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
      }
    } catch (_err) {
      // fallback detail
    }
    throw new Error(detail);
  }

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

export function getSetupStatus() {
  return request("/setup/status", { method: "GET" });
}

export function uploadCredentials(credentials) {
  return request("/setup/credentials", {
    method: "POST",
    body: JSON.stringify({ credentials }),
  });
}

export function testAuth(force = false) {
  const url = force ? "/setup/test-auth?force=true" : "/setup/test-auth";
  return request(url, { method: "GET" });
}

export function completeAuth(code) {
  return request("/setup/complete-auth", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function configureSpace(payload) {
  return request("/setup/space", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function startBridge(agentId = null) {
  const suffix = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return request(`/setup/start${suffix}`, { method: "POST" });
}

export function stopBridge(agentId = null) {
  const suffix = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return request(`/setup/stop${suffix}`, { method: "POST" });
}

export function listAgents() {
  return request("/agents", { method: "GET" });
}

export function createAgent(payload) {
  return request("/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getAgent(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}`, { method: "GET" });
}

export function updateAgent(agentId, payload) {
  return request(`/agents/${encodeURIComponent(agentId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteAgent(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}`, { method: "DELETE" });
}

export function startAgent(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}/start`, { method: "POST" });
}

export function stopAgent(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}/stop`, { method: "POST" });
}

export function restartAgent(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}/restart`, { method: "POST" });
}

export function getAgentMemory(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}/memory`, { method: "GET" });
}

export function updateAgentMemory(agentId, content) {
  return request(`/agents/${encodeURIComponent(agentId)}/memory`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export function getAgentProcedures(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}/procedures`, { method: "GET" });
}

export function updateAgentProcedures(agentId, content) {
  return request(`/agents/${encodeURIComponent(agentId)}/procedures`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export function listAgentSessions(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}/sessions`, { method: "GET" });
}

export function getAgentSession(agentId, sessionId) {
  return request(
    `/agents/${encodeURIComponent(agentId)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "GET" },
  );
}

export function getGlobalUserMemory() {
  return request("/agents/_global/user-memory", { method: "GET" });
}

export function updateGlobalUserMemory(content) {
  return request("/agents/_global/user-memory", {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export function getGlobalProcedures() {
  return request("/agents/_global/procedures", { method: "GET" });
}

export function updateGlobalProcedures(content) {
  return request("/agents/_global/procedures", {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export function listGlobalKnowledge() {
  return request("/agents/_global/knowledge", { method: "GET" });
}

export function testAgent(agentId, text = "ping") {
  return request(`/agents/${encodeURIComponent(agentId)}/test`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function listCronTasks(agentId) {
  return request(`/agents/${encodeURIComponent(agentId)}/crons`, { method: "GET" });
}

export function listAllCrons() {
  return request("/agents/_cron/all", { method: "GET" });
}

export function validateCronSchedule(schedule) {
  return request("/agents/_cron/validate", {
    method: "POST",
    body: JSON.stringify({ schedule }),
  });
}

export function createCronTask(agentId, payloadOrSchedule, instruction) {
  const payload = typeof payloadOrSchedule === "object" && payloadOrSchedule !== null
    ? payloadOrSchedule
    : { schedule: payloadOrSchedule, instruction };
  return request(`/agents/${encodeURIComponent(agentId)}/crons`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCronTask(agentId, taskId, payload) {
  return request(`/agents/${encodeURIComponent(agentId)}/crons/${encodeURIComponent(taskId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteCronTask(agentId, taskId) {
  return request(`/agents/${encodeURIComponent(agentId)}/crons/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}

function toQueryString(params = {}) {
  const search = new URLSearchParams();
  for (const [key, rawValue] of Object.entries(params || {})) {
    if (rawValue === null || rawValue === undefined || rawValue === "") {
      continue;
    }
    search.set(key, String(rawValue));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export function listBoardTasks(filters = {}) {
  return request(`/tasks${toQueryString(filters)}`, { method: "GET" });
}

export function createBoardTask(payload) {
  return request("/tasks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateBoardTask(taskId, payload) {
  return request(`/tasks/${encodeURIComponent(taskId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function completeBoardTask(taskId, result = null) {
  return request(`/tasks/${encodeURIComponent(taskId)}/complete`, {
    method: "POST",
    body: JSON.stringify({ result }),
  });
}

export function deleteBoardTask(taskId) {
  return request(`/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}

export function runCronTask(agentId, taskId) {
  return request(`/agents/${encodeURIComponent(agentId)}/crons/${encodeURIComponent(taskId)}/run`, {
    method: "POST",
  });
}

export function getCronTaskHistory(agentId, taskId) {
  return request(`/agents/${encodeURIComponent(agentId)}/crons/${encodeURIComponent(taskId)}/history`, {
    method: "GET",
  });
}


export function getMetricsSummary() {
  return request("/agents/metrics/summary", { method: "GET" });
}

export function listMcpServers() {
  return request("/agents/_mcp/servers", { method: "GET" });
}

export function toggleDebugMode() {
  return request("/setup/debug-mode", { method: "POST" });
}

export function exportAgentUrl(agentId) {
  return `/agents/${encodeURIComponent(agentId)}/export`;
}

export async function importAgent(file, overwrite = false) {
  const form = new FormData();
  form.append("archive", file);
  const qs = overwrite ? "?overwrite=true" : "";
  const response = await fetch(`/agents/import${qs}`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
      }
    } catch (_err) {
      // fallback detail
    }
    const err = new Error(detail);
    err.status = response.status;
    throw err;
  }
  return response.json();
}
