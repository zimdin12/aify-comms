#!/usr/bin/env node
// HTTP/SSE client for the hermes-agent `api_server` platform (default
// http://127.0.0.1:8642). Plain Node 18+ `fetch` + manual SSE line parsing —
// no extra deps. Contract:
// docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.
//
// Two SSE framings are handled by parseSseStream:
//   - named-event  (session-chat):   event: <name>\ndata: <json>\n\n
//   - data:-only   (/v1/runs events): data: <json>\n\n  (type in JSON "event")
// Keepalive comment lines (": keepalive", ": stream closed") are ignored.

const DEFAULT_BASE_URL = "http://127.0.0.1:8642";

function trimTrailingSlash(url) {
  return String(url || "").replace(/\/+$/, "");
}

function bearerHeaders(key, extra = {}) {
  const headers = { ...extra };
  if (key) headers["Authorization"] = `Bearer ${key}`;
  return headers;
}

function authError(status, bodyText) {
  const err = new Error(
    `hermes api_server auth failed (HTTP ${status}): ${bodyText || "Invalid API key"}`,
  );
  err.status = status;
  return err;
}

// Parse a fetch Response body (ReadableStream) of SSE frames. Invokes
// onFrame({ event, data }) for each complete `\n\n`-delimited frame.
// `event` is the named `event:` line if present, else null (data:-only).
// `data` is the parsed JSON from the (possibly multi-line) `data:` lines.
// SSE comment lines (starting with ":") are skipped.
async function parseSseStream(response, onFrame) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushFrame = (rawFrame) => {
    let eventName = null;
    const dataLines = [];
    for (const line of rawFrame.split("\n")) {
      if (!line || line.startsWith(":")) continue; // blank or comment
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).replace(/^ /, ""));
      }
    }
    if (dataLines.length === 0 && eventName === null) return;
    let data = {};
    const joined = dataLines.join("\n");
    if (joined) {
      try { data = JSON.parse(joined); } catch { data = { raw: joined }; }
    }
    onFrame({ event: eventName, data });
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const rawFrame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      flushFrame(rawFrame);
    }
    if (done) {
      if (buffer.trim()) flushFrame(buffer);
      break;
    }
  }
}

export function createHermesApiServerClient() {
  // GET /health (unauthenticated). Returns {ok, status?, version?}.
  async function health({ baseUrl = DEFAULT_BASE_URL } = {}) {
    const url = `${trimTrailingSlash(baseUrl)}/health`;
    try {
      const res = await fetch(url, { method: "GET" });
      if (!res.ok) return { ok: false, status: res.status };
      const body = await res.json().catch(() => ({}));
      return { ok: body.status === "ok", status: body.status, version: body.version };
    } catch (error) {
      return { ok: false, reason: error?.message || String(error) };
    }
  }

  // POST /api/sessions { id } — pin a stable session. 201 = created,
  // 409 = already exists (treated as success/idempotent). 401/other → throw.
  async function ensureSession({ baseUrl = DEFAULT_BASE_URL, key, id }) {
    if (!id) throw new Error("ensureSession requires an explicit session id");
    const url = `${trimTrailingSlash(baseUrl)}/api/sessions`;
    const res = await fetch(url, {
      method: "POST",
      headers: bearerHeaders(key, { "Content-Type": "application/json" }),
      body: JSON.stringify({ id }),
    });
    if (res.status === 201 || res.status === 409) return { id, created: res.status === 201 };
    const text = await res.text().catch(() => "");
    if (res.status === 401) throw authError(res.status, text);
    throw new Error(`ensureSession(${id}) failed (HTTP ${res.status}): ${text}`);
  }

  // POST /api/sessions/{id}/chat/stream — named-event SSE. Calls onDelta for
  // each assistant.delta; resolves with assistant.completed.content (falling
  // back to concatenated deltas) when the terminal `done` event arrives.
  async function chatStream({
    baseUrl = DEFAULT_BASE_URL,
    key,
    sessionId,
    sessionKey,
    text,
    onDelta,
  }) {
    if (!sessionId) throw new Error("chatStream requires a sessionId");
    const url = `${trimTrailingSlash(baseUrl)}/api/sessions/${encodeURIComponent(sessionId)}/chat/stream`;
    const headers = bearerHeaders(key, {
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
      "X-Hermes-Session-Id": sessionId,
    });
    if (sessionKey) headers["X-Hermes-Session-Key"] = sessionKey;
    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ message: text }),
    });
    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      if (res.status === 401) throw authError(res.status, errText);
      throw new Error(`chatStream failed (HTTP ${res.status}): ${errText}`);
    }

    let completedContent = null;
    let assembled = "";
    let streamError = null;
    let done = false;

    await parseSseStream(res, ({ event, data }) => {
      switch (event) {
        case "assistant.delta": {
          const chunk = String(data?.delta ?? "");
          if (chunk) {
            assembled += chunk;
            if (typeof onDelta === "function") onDelta(chunk);
          }
          break;
        }
        case "assistant.completed":
          if (typeof data?.content === "string") completedContent = data.content;
          break;
        case "error":
          streamError = new Error(String(data?.message || "hermes stream error"));
          break;
        case "done":
          done = true;
          break;
        default:
          break;
      }
    });

    if (streamError) throw streamError;
    if (!done) throw new Error("chatStream ended without terminal `done` event");
    return completedContent != null ? completedContent : assembled;
  }

  // POST /v1/runs — start a run. Returns { runId, status }. 202 on success.
  async function createRun({ baseUrl = DEFAULT_BASE_URL, key, input, instructions, sessionId, sessionKey, model }) {
    const url = `${trimTrailingSlash(baseUrl)}/v1/runs`;
    const headers = bearerHeaders(key, { "Content-Type": "application/json" });
    if (sessionKey) headers["X-Hermes-Session-Key"] = sessionKey;
    const body = { input };
    if (instructions) body.instructions = instructions;
    if (sessionId) body.session_id = sessionId;
    if (model) body.model = model;
    const res = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
    const text = await res.text().catch(() => "");
    if (res.status !== 202 && res.status !== 200) {
      if (res.status === 401) throw authError(res.status, text);
      throw new Error(`createRun failed (HTTP ${res.status}): ${text}`);
    }
    let parsed = {};
    try { parsed = JSON.parse(text); } catch { /* keep empty */ }
    return { runId: parsed.run_id, status: parsed.status };
  }

  // GET /v1/runs/{id}/events — data:-only SSE. Calls onDelta for each
  // message.delta; resolves with { status, output, usage, error } on the
  // terminal run.completed / run.failed / run.cancelled event.
  async function runEvents({ baseUrl = DEFAULT_BASE_URL, key, runId, onDelta }) {
    if (!runId) throw new Error("runEvents requires a runId");
    const url = `${trimTrailingSlash(baseUrl)}/v1/runs/${encodeURIComponent(runId)}/events`;
    const res = await fetch(url, {
      method: "GET",
      headers: bearerHeaders(key, { "Accept": "text/event-stream" }),
    });
    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      if (res.status === 401) throw authError(res.status, errText);
      throw new Error(`runEvents failed (HTTP ${res.status}): ${errText}`);
    }

    let assembled = "";
    let result = null;

    await parseSseStream(res, ({ data }) => {
      // data:-only framing — the event type lives in data.event.
      const type = String(data?.event || "");
      switch (type) {
        case "message.delta": {
          const chunk = String(data?.delta ?? "");
          if (chunk) {
            assembled += chunk;
            if (typeof onDelta === "function") onDelta(chunk);
          }
          break;
        }
        case "run.completed":
          result = { status: "completed", output: data?.output ?? assembled, usage: data?.usage };
          break;
        case "run.failed":
          result = { status: "failed", error: data?.error };
          break;
        case "run.cancelled":
          result = { status: "cancelled" };
          break;
        default:
          break;
      }
    });

    if (!result) throw new Error("runEvents ended without a terminal run event");
    return result;
  }

  // POST /v1/runs/{id}/stop — request cancellation. Returns the status object.
  async function stopRun({ baseUrl = DEFAULT_BASE_URL, key, runId }) {
    if (!runId) throw new Error("stopRun requires a runId");
    const url = `${trimTrailingSlash(baseUrl)}/v1/runs/${encodeURIComponent(runId)}/stop`;
    const res = await fetch(url, { method: "POST", headers: bearerHeaders(key) });
    const text = await res.text().catch(() => "");
    if (!res.ok) {
      if (res.status === 401) throw authError(res.status, text);
      throw new Error(`stopRun failed (HTTP ${res.status}): ${text}`);
    }
    try { return JSON.parse(text); } catch { return { status: "stopping" }; }
  }

  return { health, ensureSession, chatStream, createRun, runEvents, stopRun };
}

export { parseSseStream, DEFAULT_BASE_URL };
