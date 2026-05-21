// Phase 3: dashboard-input buffering for synthesized pi RPC terminals.
//
// Operator keystrokes arrive through terminal_controls (action="input"), one
// byte at a time. We accumulate per-terminal until a CR/LF lands, then hand
// each complete line to a dispatch function that drives the agent's
// PiSession. This module is intentionally transport-free so server.js can
// own HTTP I/O and the unit tests can drive the buffer with stubs.

const MAX_VIRTUAL_TERMINAL_INPUT_BUFFER_CHARS = 16 * 1024;

export function createVirtualTerminalInputManager({ dispatch, maxBufferChars = MAX_VIRTUAL_TERMINAL_INPUT_BUFFER_CHARS, onError = null } = {}) {
  if (typeof dispatch !== "function") {
    throw new Error("createVirtualTerminalInputManager requires a dispatch function");
  }
  const buffers = new Map();

  async function append(agentId, terminalId, chunk) {
    if (!terminalId) return;
    let entry = buffers.get(terminalId);
    if (!entry) {
      entry = { agentId, buffer: "", dispatching: false };
      buffers.set(terminalId, entry);
    }
    entry.agentId = agentId || entry.agentId;
    entry.buffer += String(chunk || "");
    if (entry.buffer.length > maxBufferChars) {
      entry.buffer = entry.buffer.slice(-maxBufferChars);
    }
    await drain(terminalId);
  }

  async function drain(terminalId) {
    const entry = buffers.get(terminalId);
    if (!entry || entry.dispatching) return;
    const submissions = [];
    while (true) {
      const match = /\r\n|\r|\n/.exec(entry.buffer);
      if (!match) break;
      const line = entry.buffer.slice(0, match.index);
      entry.buffer = entry.buffer.slice(match.index + match[0].length);
      if (line.length > 0) submissions.push(line);
    }
    if (submissions.length === 0) return;
    entry.dispatching = true;
    try {
      for (const line of submissions) {
        try {
          await dispatch(entry.agentId, line);
        } catch (error) {
          if (typeof onError === "function") {
            try { onError(error, { agentId: entry.agentId, terminalId, line }); } catch { /* swallow */ }
          }
        }
      }
    } finally {
      entry.dispatching = false;
    }
    const after = buffers.get(terminalId);
    if (after && /\r|\n/.test(after.buffer)) await drain(terminalId);
  }

  function remove(terminalId) {
    buffers.delete(terminalId);
  }

  function clear() {
    buffers.clear();
  }

  function snapshot() {
    const out = {};
    for (const [id, entry] of buffers.entries()) {
      out[id] = { agentId: entry.agentId, buffer: entry.buffer, dispatching: entry.dispatching };
    }
    return out;
  }

  return { append, remove, clear, snapshot };
}
