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

export function listSpaceBots() {
  return request("/setup/space-bots", { method: "GET" });
}

export function startBridge() {
  return request("/setup/start", { method: "POST" });
}

export function stopBridge() {
  return request("/setup/stop", { method: "POST" });
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

export function linkAgentBot(agentId, botUserId) {
  return request(`/agents/${encodeURIComponent(agentId)}/link-bot`, {
    method: "POST",
    body: JSON.stringify({ bot_user_id: botUserId }),
  });
}

export function testAgent(agentId, text = "ping") {
  return request(`/agents/${encodeURIComponent(agentId)}/test`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}
