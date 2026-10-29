// ChatBridge example for Node.js >= 18: no dependencies, no automatic replay.
// Supply CHATBRIDGE_URL and optional CHATBRIDGE_API_KEY in your environment.
export class OutcomeUnknown extends Error {
  constructor(operation, clientRequestId) {
    super(operation + " may have reached ChatBridge; reconcile before retrying");
    this.clientRequestId = clientRequestId;
  }
}

export class ChatBridgeClient {
  constructor({baseUrl = "http://127.0.0.1:8788", apiKey = "", timeoutMs = 30000} = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
    this.timeoutMs = timeoutMs;
  }

  async request(method, path, body, requestKey = "") {
    const headers = {Accept: "application/json"};
    if (this.apiKey) headers.Authorization = "Bearer " + this.apiKey;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const timer = AbortSignal.timeout(this.timeoutMs);
    let response;
    try {
      response = await fetch(this.baseUrl + path, {
        method, headers, signal: timer,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
        throw new OutcomeUnknown(method + " " + path, requestKey);
      }
      throw error;
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(payload?.error?.message || "ChatBridge HTTP " + response.status);
      err.status = response.status;
      err.code = payload?.error?.code || "http_error";
      err.retryAfter = Number(response.headers.get("Retry-After") || 0);
      throw err;
    }
    return payload;
  }

  createProject(name, {memoryMode = "default", clientRequestId} = {}) {
    if (!clientRequestId) throw new Error("Stable clientRequestId is required");
    if (!["default", "project_only"].includes(memoryMode)) throw new Error("Invalid memory mode");
    return this.request("POST", "/v1/projects", {
      name, memory_mode: memoryMode, client_request_id: clientRequestId
    }, clientRequestId);
  }

  verifyProject(projectId, name, memoryMode = "default") {
    return this.request("POST", "/v1/projects/verify", {
      project_id: projectId, name, memory_mode: memoryMode
    }, "verify-" + projectId);
  }

  createSession({mode, projectId = null, title = ""}) {
    if (!["temporary", "persistent", "continued"].includes(mode)) {
      throw new Error("Explicit session mode required");
    }
    if (mode === "temporary" && projectId) throw new Error("Temporary chat cannot belong to project");
    return this.request("POST", "/v1/sessions", {
      mode, title, ...(projectId ? {project_id: projectId} : {})
    }, "create-session-" + mode);
  }

  send(sessionId, content, {clientRequestId, reasoningEffort = "high"} = {}) {
    if (!clientRequestId) throw new Error("Stable clientRequestId is required");
    if (!["low", "medium", "high"].includes(reasoningEffort)) throw new Error("Invalid reasoning effort");
    return this.request("POST", "/v1/sessions/" + sessionId + "/messages", {
      content, client_request_id: clientRequestId, reasoning_effort: reasoningEffort
    }, clientRequestId);
  }

  status(requestId) {
    return this.request("GET", "/v1/requests/" + requestId);
  }

  reconcile(requestId) {
    // Does NOT repeat a model submission; use only for an existing request_id.
    return this.request("POST", "/v1/requests/" + requestId + "/reconcile",
                        {}, "reconcile-" + requestId);
  }

  rotate(sessionId) {
    // Caller must ensure all external Watcher guards have been released.
    return this.request("POST", "/v1/sessions/" + sessionId + "/rotate",
                        {}, "rotate-" + sessionId);
  }

  async wait(requestId, {intervalMs = 2000, maxMs = 180000} = {}) {
    const end = Date.now() + maxMs;
    while (true) {
      const state = await this.status(requestId);
      if (["completed", "failed", "unknown", "interrupted"].includes(state.status)) return state;
      if (Date.now() >= end) return state; // Never convert streaming into completed.
      await new Promise(resolve => setTimeout(resolve, Math.min(intervalMs, end - Date.now())));
    }
  }
}
