// Abstract base for per-runtime controllers extracted from runtimes.js as
// part of Plan 3. Each controller owns one runtime's delivery + lifecycle:
// start/interrupt/steer/injectMessage, plus an optional terminalSink for
// synth-terminal stream consumers.
//
// Per the 500-line file rule, each subclass lives in its own file under
// mcp/stdio/controllers/ and targets <=400 lines.

export class BaseController {
  constructor(opts) {
    this.opts = opts || {};
  }

  // Lifecycle - begin work, returns a promise that resolves on turn-completed
  async start(_ctx) {
    throw new Error("BaseController.start is abstract - subclass must override");
  }

  // Delivery - inject a message into the live session (resident) or
  // forward it to the wrapper PTY (managed). Returns when message accepted.
  async injectMessage(_opts) {
    throw new Error("BaseController.injectMessage is abstract - subclass must override");
  }

  // Cancel the active turn. Returns immediately; final state arrives via
  // turn-completed callback.
  async interrupt(_opts) {
    throw new Error("BaseController.interrupt is abstract - subclass must override");
  }

  // Mid-turn append. Some runtimes (codex turn/steer, hermes session.steer)
  // support this; others don't (subclass throws or returns rejected promise).
  async steer(_opts) {
    throw new Error("BaseController.steer is abstract - subclass must override");
  }

  // Optional synth-terminal frame source. Subclasses with a terminal stream
  // (pi-session, codex remote, hermes gateway) return an EventEmitter-like
  // object; subclasses without one return null.
  get terminalSink() {
    return null;
  }

  // Plan 4 Task 13 (2026-05-25): subclasses call this.markReady() when their
  // initial handshake completes (WS app-server initialized, gateway connect,
  // wrapper PTY spawn, omp agent_ready, etc.). The bridge wires
  // setReadyListener to POST PATCH /api/v1/agents/{id}/ready so operators
  // can distinguish "online" (heartbeat alive) from "ready" (handshake done,
  // can accept dispatch). Best-effort — listener errors are swallowed so a
  // failing ready-PATCH never breaks the start() path.
  setReadyListener(fn) {
    this._readyListener = (typeof fn === "function") ? fn : null;
  }

  markReady() {
    if (this._readyListener) {
      try { this._readyListener(); } catch { /* best-effort */ }
    }
  }
}
