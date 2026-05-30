#!/usr/bin/env node
// Test double for the hermes-agent `api_server` platform (HTTP/SSE, port 8642).
// Models the exact request/response/SSE shapes recorded in
// docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.
//
// Endpoints:
//   GET  /health                              → unauthenticated {"status":"ok",...}
//   POST /api/sessions                        → 201 (or 409 for a pre-seeded id)
//   POST /api/sessions/{id}/chat/stream       → SSE: assistant.delta x2 →
//                                               assistant.completed → run.completed → done
//                                               (Bearer auth required, else 401)
//   POST /v1/runs                             → 202 {"run_id","status":"started"}
//   GET  /v1/runs/{id}/events                 → SSE (data:-only frames) →
//                                               message.delta x2 → run.completed
//
// Use start() for an in-process server returning {baseUrl, key, close}.

import http from "node:http";

const DEFAULT_KEY = "test-api-server-key";

function bearer(req) {
  const auth = String(req.headers["authorization"] || "");
  if (!auth.startsWith("Bearer ")) return "";
  return auth.slice(7).trim();
}

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(body);
}

function send401(res) {
  sendJson(res, 401, {
    error: {
      message: "Invalid API key",
      type: "invalid_request_error",
      code: "invalid_api_key",
    },
  });
}

function readBody(req) {
  return new Promise((resolve) => {
    let buf = "";
    req.on("data", (chunk) => { buf += chunk; });
    req.on("end", () => {
      if (!buf) return resolve({});
      try { resolve(JSON.parse(buf)); } catch { resolve({}); }
    });
  });
}

function openSse(res) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
  });
}

// Named-event framing (session-chat): `event: <name>\ndata: <json>\n\n`.
function namedFrame(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

// data:-only framing (/v1/runs events): `data: <json>\n\n`, type inside JSON.
function dataFrame(res, data) {
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

export function start({ key = DEFAULT_KEY, seedSessionIds = ["already-exists"] } = {}) {
  const sessions = new Set(seedSessionIds);

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const pathname = url.pathname;

    // --- health (unauthenticated) ---
    if (req.method === "GET" && (pathname === "/health" || pathname === "/v1/health")) {
      return sendJson(res, 200, { status: "ok", platform: "hermes-agent" });
    }

    // --- create / pin session ---
    if (req.method === "POST" && pathname === "/api/sessions") {
      if (bearer(req) !== key) return send401(res);
      const body = await readBody(req);
      const id = String(body.id || body.session_id || "").trim();
      if (!id) return sendJson(res, 400, { error: { code: "invalid_session_id" } });
      if (sessions.has(id)) {
        return sendJson(res, 409, { error: { code: "session_exists", message: `session ${id} exists` } });
      }
      sessions.add(id);
      return sendJson(res, 201, { object: "hermes.session", session: { id } });
    }

    // --- session chat stream (named-event SSE) ---
    const chatMatch = pathname.match(/^\/api\/sessions\/([^/]+)\/chat\/stream$/);
    if (req.method === "POST" && chatMatch) {
      if (bearer(req) !== key) return send401(res);
      const sessionId = decodeURIComponent(chatMatch[1]);
      if (!sessions.has(sessionId)) {
        return sendJson(res, 404, { error: { code: "session_not_found" } });
      }
      const body = await readBody(req);
      const text = String(body.message || body.input || "");
      const sessionKeyHeader = req.headers["x-hermes-session-key"];
      const runId = "run_fake01";
      const messageId = "msg_fake01";
      openSse(res);
      const base = { session_id: sessionId, run_id: runId, seq: 0, ts: 0 };
      namedFrame(res, "run.started", { ...base, user_message: { role: "user", content: text } });
      namedFrame(res, "message.started", { ...base, message: { id: messageId, role: "assistant" } });
      res.write(": keepalive\n\n");
      const full = `echo:${text}`;
      const deltas = [full.slice(0, 5), full.slice(5)];
      namedFrame(res, "assistant.delta", { ...base, message_id: messageId, delta: deltas[0] });
      namedFrame(res, "assistant.delta", { ...base, message_id: messageId, delta: deltas[1] });
      namedFrame(res, "assistant.completed", {
        ...base,
        message_id: messageId,
        content: full,
        completed: true,
        partial: false,
        interrupted: false,
        session_key: sessionKeyHeader || undefined,
      });
      namedFrame(res, "run.completed", {
        ...base,
        message_id: messageId,
        completed: true,
        messages: [],
        usage: { input_tokens: 1, output_tokens: 2, total_tokens: 3 },
      });
      namedFrame(res, "done", {});
      return res.end();
    }

    // --- create run ---
    if (req.method === "POST" && pathname === "/v1/runs") {
      if (bearer(req) !== key) return send401(res);
      const body = await readBody(req);
      const input = body.input;
      if (!input || (Array.isArray(input) && input.length === 0)) {
        return sendJson(res, 400, { error: { code: "missing_input" } });
      }
      return sendJson(res, 202, { run_id: "run_fakeRun01", status: "started" });
    }

    // --- run events (data:-only SSE) ---
    const eventsMatch = pathname.match(/^\/v1\/runs\/([^/]+)\/events$/);
    if (req.method === "GET" && eventsMatch) {
      if (bearer(req) !== key) return send401(res);
      const runId = decodeURIComponent(eventsMatch[1]);
      openSse(res);
      const stamp = (extra) => ({ run_id: runId, timestamp: 0, ...extra });
      dataFrame(res, stamp({ event: "message.delta", delta: "hi " }));
      res.write(": keepalive\n\n");
      dataFrame(res, stamp({ event: "message.delta", delta: "there" }));
      dataFrame(res, stamp({
        event: "run.completed",
        output: "hi there",
        usage: { input_tokens: 1, output_tokens: 2, total_tokens: 3 },
      }));
      res.write(": stream closed\n\n");
      return res.end();
    }

    // --- stop run ---
    const stopMatch = pathname.match(/^\/v1\/runs\/([^/]+)\/stop$/);
    if (req.method === "POST" && stopMatch) {
      if (bearer(req) !== key) return send401(res);
      const runId = decodeURIComponent(stopMatch[1]);
      return sendJson(res, 200, { object: "hermes.run", run_id: runId, status: "stopping" });
    }

    return sendJson(res, 404, { error: { code: "not_found", path: pathname } });
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        baseUrl: `http://127.0.0.1:${port}`,
        key,
        close: () => new Promise((r) => server.close(() => r())),
      });
    });
  });
}

// Allow standalone run for manual probing.
if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1].replace(/\\/g, "/")}`).href) {
  start().then(({ baseUrl }) => process.stdout.write(`fake-hermes-apiserver listening on ${baseUrl}\n`));
}
