// Pure functions for translating between aify-comms dispatch shapes and
// hermes tui_gateway JSON-RPC 2.0 frames over WebSocket. No side effects;
// no I/O. The resident-channel controller in runtimes.js owns the WS
// connection and uses these helpers to build outbound frames + translate
// inbound events into the bridge's existing onEvent / synth-terminal frame
// shape.
//
// Mirrors the protocol module pattern from hermes-acp-protocol.js — keeps
// the controller thin and the wire format tested in isolation.

export function buildPromptSubmitFrame({ id, sessionId, text }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "prompt.submit",
    params: { session_id: String(sessionId || ""), text: String(text || "") },
  };
}

export function buildSessionSteerFrame({ id, sessionId, text }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.steer",
    params: { session_id: String(sessionId || ""), text: String(text || "") },
  };
}

export function buildSessionMostRecentFrame({ id }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.most_recent",
    params: {},
  };
}

export function buildSessionListFrame({ id }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.list",
    params: {},
  };
}

// Plan 6 follow-up (2026-05-26): session.resume creates a NEW in-memory
// `sid` bound to this WS connection for the given persisted `session_key`.
// Without this dance prompt.submit returns 4001 "session not found" — the
// gateway looks up by short in-memory sid, not by persisted session_key,
// and external WS clients (like our aify-comms bridge) have no access to
// the operator's TUI sid. Returns { session_id: <fresh sid>, resumed:
// <session_key>, ... }; the bridge must use the new sid for all
// subsequent prompt.submit / session.steer / session.interrupt calls on
// this connection.
export function buildSessionResumeFrame({ id, sessionKey, cols = 80 }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.resume",
    params: { session_id: String(sessionKey || ""), cols: Number(cols) || 80 },
  };
}

// NOTE (2026-05-30 hermes-apiserver-delivery): the aify.session.bind_transport
// and aify.session.render_notice frame builders were removed with the retired
// tui_gateway WS-bind path. They were used ONLY by the deleted
// HermesResidentController. Managed/resident hermes now delivers via the
// hermes-channel.js api_server sidecar. The frames below remain because
// hermes-managed-gateway-session.js (AIFY_HERMES_MANAGED_USE_GATEWAY=1) still
// uses prompt.submit / session.steer / session.most_recent over the gateway WS.

// Plan 6 follow-up #2 (2026-05-26): when session.resume(session_key) fails
// because the persisted key has been GC'd (or never existed), session.create
// allocates a BRAND NEW session in hermes' DB + in-memory _sessions. Always
// available regardless of the bridge's stored handle truth. Returns
// { session_id: <fresh sid>, ... }. We then submit to that sid.
export function buildSessionCreateFrame({ id, cwd = "", cols = 80, title = "" }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.create",
    params: {
      cwd: String(cwd || ""),
      cols: Number(cols) || 80,
      title: String(title || ""),
    },
  };
}

export function buildSessionInterruptFrame({ id, sessionId }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.interrupt",
    params: { session_id: String(sessionId || "") },
  };
}

// Plan "managed-hermes visible-TUI" (2026-05-31): list the gateway's currently
// active sessions so a thin WS client (the per-agent managed-host) can discover
// the visible TUI's EPHEMERAL runtime sid. The TUI resumes a STABLE title/key
// (`aify-<agentId>`), but its in-memory runtime `id` is forged fresh
// (`uuid4().hex[:8]`) on every attach, so it must be re-discovered after each
// (re)attach and NEVER cached. `current_session_id` is an optional hint the
// gateway may use to bias the listing toward the caller's own connection.
export function buildSessionActiveListFrame({ id, currentSessionId = "" } = {}) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.active_list",
    params: { current_session_id: String(currentSessionId || "") },
  };
}

// Pick the row for the visible TUI from a session.active_list response. The
// stable key is `aify-<agentId>`; the live runtime `id` we actually submit
// against is ephemeral. Match precedence:
//   1. exact `session_key === key`
//   2. exact `id === key`
//   3. title contains/matches `key` (the TUI sets its title to the resume key)
//   4. freshest by last_active / started_at / created_at (best-effort)
// Returns the matched row's runtime id (string) or null when nothing matches.
export function pickSessionForKey(activeListResponse, key) {
  const wanted = String(key || "").trim();
  const rows = Array.isArray(activeListResponse)
    ? activeListResponse
    : Array.isArray(activeListResponse?.result?.sessions)
    ? activeListResponse.result.sessions
    : Array.isArray(activeListResponse?.sessions)
    ? activeListResponse.sessions
    : Array.isArray(activeListResponse?.result)
    ? activeListResponse.result
    : [];
  if (!rows.length) return null;

  const rowId = (r) => String(r?.id || r?.session_id || r?.sessionId || "").trim();

  if (wanted) {
    // 1. exact session_key match
    for (const r of rows) {
      if (String(r?.session_key || r?.sessionKey || "").trim() === wanted) {
        const id = rowId(r);
        if (id) return id;
      }
    }
    // 2. exact runtime id match
    for (const r of rows) {
      if (rowId(r) === wanted) return wanted;
    }
    // 3. title match (the TUI titles itself with the resume key)
    for (const r of rows) {
      const title = String(r?.title || r?.name || "").trim();
      if (title && (title === wanted || title.includes(wanted))) {
        const id = rowId(r);
        if (id) return id;
      }
    }
  }

  // 4. freshest by timestamp; fall back to the first row with an id.
  const stamp = (s) =>
    Number(
      Date.parse(
        s?.last_active || s?.lastActive || s?.started_at || s?.startedAt || s?.created_at || s?.createdAt || 0,
      ),
    ) || 0;
  let best = null;
  let bestStamp = -1;
  for (const r of rows) {
    const id = rowId(r);
    if (!id) continue;
    const t = stamp(r);
    if (best === null || t > bestStamp) {
      best = id;
      bestStamp = t;
    }
  }
  return best;
}

// Translates an inbound gateway event into a normalized shape the
// controller can consume. Returns null for events the bridge doesn't
// care about (e.g. gateway.ready handshake, UI metadata).
export function translateGatewayEvent(message) {
  const method = String(message?.method || "");
  const rawParams = message?.params || {};
  const eventType = method === "event" ? String(rawParams.type || "") : method;
  const params = method === "event" && rawParams.payload && typeof rawParams.payload === "object"
    ? rawParams.payload
    : rawParams;

  if (eventType === "message.start") {
    return { kind: "start" };
  }
  if (eventType === "agent.message.delta" || eventType === "message.delta") {
    return { kind: "delta", text: String(params.delta || params.text || "") };
  }
  if (eventType === "agent.message.end" || eventType === "message.complete") {
    return {
      kind: "final",
      text: String(params.text || ""),
      status: String(params.status || ""),
      warning: String(params.warning || ""),
    };
  }
  if (eventType === "tool.started" || eventType === "tool.start" || eventType === "tool.progress") {
    return { kind: "tool_started", label: String(params.tool || params.name || "tool") };
  }
  if (eventType === "tool.completed" || eventType === "tool.complete") {
    return { kind: "tool_completed", label: String(params.tool || params.name || "tool") };
  }
  if (eventType === "error") {
    return { kind: "error", text: String(params.message || "") };
  }
  return null;
}

// Identifies the JSON-RPC error code hermes returns when prompt.submit is
// called against a session that's already running a turn. The controller
// uses this to decide whether to fall back to session.steer (mid-run
// insertion that lands on the next tool result without interrupting).
// Source: tui_gateway/server.py:3148.
export function isSessionBusyError(error) {
  if (!error) return false;
  const code = Number(error.code);
  const message = String(error.message || "");
  return code === 4009 || /session busy/i.test(message);
}

// Plan 6 follow-up (2026-05-26): the gateway returns this when
// prompt.submit / session.steer / session.interrupt is called against a
// session_id that's no longer loaded in memory (operator killed the chat
// TUI, hermes process restarted, etc.). Distinct from isSessionBusyError —
// here the right recovery is to refresh the session list and retry against
// whatever is currently active, NOT to steer into a "running" turn.
export function isSessionNotFoundError(error) {
  if (!error) return false;
  const code = Number(error.code);
  const message = String(error.message || "");
  // tui_gateway/server.py uses 4010 for not-found per current convention;
  // accept the textual signature as a safety net for older gateway builds.
  return code === 4010 || /session not found|no such session|unknown session/i.test(message);
}

// Plan 6 follow-up (2026-05-26): pick the freshest session id from
// session.list response. Tries `createdAt` / `created_at` / `started_at`
// in order; falls back to the first entry's id when no timestamp field is
// present. Returns null when the response shape isn't recognizable.
export function pickFreshestSessionFromList(response) {
  if (!response) return null;
  const sessions = Array.isArray(response)
    ? response
    : Array.isArray(response.sessions)
    ? response.sessions
    : Array.isArray(response.items)
    ? response.items
    : [];
  if (!sessions.length) return null;
  const stamp = (s) =>
    Number(Date.parse(s?.createdAt || s?.created_at || s?.startedAt || s?.started_at || 0)) || 0;
  let best = null;
  let bestStamp = -1;
  for (const s of sessions) {
    const id = String(s?.id || s?.session_id || s?.sessionId || "").trim();
    if (!id) continue;
    const t = stamp(s);
    if (t > bestStamp) {
      bestStamp = t;
      best = id;
    }
  }
  return best;
}
