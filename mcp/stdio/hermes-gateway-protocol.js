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

export function buildSessionInterruptFrame({ id, sessionId }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.interrupt",
    params: { session_id: String(sessionId || "") },
  };
}

// Translates an inbound gateway event into a normalized shape the
// controller can consume. Returns null for events the bridge doesn't
// care about (e.g. gateway.ready handshake, UI metadata).
export function translateGatewayEvent(message) {
  const method = String(message?.method || "");
  const params = message?.params || {};
  if (method === "agent.message.delta") {
    return { kind: "delta", text: String(params.delta || "") };
  }
  if (method === "agent.message.end") {
    return { kind: "final", text: String(params.text || "") };
  }
  if (method === "tool.started") {
    return { kind: "tool_started", label: String(params.tool || params.name || "tool") };
  }
  if (method === "tool.completed") {
    return { kind: "tool_completed", label: String(params.tool || params.name || "tool") };
  }
  if (method === "error") {
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
