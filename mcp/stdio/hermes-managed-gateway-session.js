// Persistent `hermes dashboard --tui` child per managed hermes agent.
// Mirror of CodexSession (codex app-server) and HermesSession (hermes acp)
// patterns, but uses hermes's multi-client tui_gateway WS instead of
// single-client ACP. Symmetric with the resident-hermes channel path —
// the only difference is who spawns the backing: the wrapper spawns it
// for resident, this class spawns it for managed.
//
// Why: ACP is single-client. The dashboard Console can't attach to the
// running ACP session because the bridge owns the only connection slot.
// The tui_gateway dispatcher fans events via TeeTransport to N clients,
// so bridge + dashboard Console can both subscribe to the same session.
//
// Gated by AIFY_HERMES_MANAGED_USE_GATEWAY=1 (default off until validated).

import { spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import WebSocket from "ws";
import {
  buildPromptSubmitFrame,
  buildSessionSteerFrame,
  buildSessionMostRecentFrame,
  translateGatewayEvent,
  isSessionBusyError,
} from "./hermes-gateway-protocol.js";
import { terminateProcessTree } from "./runtimes.js";

const hermesGatewayPool = new Map();

const DEFAULT_IDLE_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const DEFAULT_STARTUP_TIMEOUT_MS = 60_000;
const DEFAULT_TURN_TIMEOUT_MS = 12 * 60 * 60 * 1000;

function pickPort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

function fetchToken(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => {
        const match = body.match(/__HERMES_SESSION_TOKEN__="([^"]+)"/);
        if (match) resolve(match[1]);
        else reject(new Error(`token not found in ${url}/`));
      });
    });
    req.setTimeout(5000, () => { req.destroy(new Error("token fetch timeout")); });
    req.on("error", reject);
  });
}

function waitForReady(url, deadlineMs, isDead) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + deadlineMs;
    const tryOnce = () => {
      // Fail FAST when the child already died (bughunt 2026-07-03): without this a
      // spawn-then-die gateway child made us poll a never-binding port for the full
      // deadline (60s) instead of aborting the moment the process exited.
      if (typeof isDead === "function" && isDead()) {
        reject(new Error(`gateway process exited before ${url} bound`));
        return;
      }
      const req = http.get(url, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (typeof isDead === "function" && isDead()) reject(new Error(`gateway process exited before ${url} bound`));
        else if (Date.now() > deadline) reject(new Error(`dashboard at ${url} did not bind within ${deadlineMs}ms`));
        else setTimeout(tryOnce, 250);
      });
    };
    tryOnce();
  });
}

function createDeferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  promise.catch(() => {});
  return { promise, resolve, reject };
}

export class HermesManagedGatewaySession {
  constructor({ agentId, agentInfo, onPoolEvent = null } = {}) {
    this.agentId = String(agentId || "").trim();
    this.agentInfo = agentInfo || {};
    this._state = "idle"; // idle | starting | ready | stopped | failed
    this._proc = null;
    this._port = 0;
    this._token = "";
    this._gatewayUrl = "";
    this._socket = null;
    this._sessionId = "";
    this._idleTimer = null;
    this._activeTurn = null;
    this._turnQueue = Promise.resolve();
    this._terminalSink = null;
    this._terminalFlushChain = Promise.resolve();
    this._pendingRpc = new Map();
    this._nextRpcId = 100;
    this._startupDeferred = null;
    this._onPoolEvent = typeof onPoolEvent === "function" ? onPoolEvent : null;
  }

  _emit(kind, payload) {
    if (!this._onPoolEvent) return;
    try { this._onPoolEvent(kind, payload); } catch {}
  }

  attachTerminalSink(sink) {
    this._terminalSink = typeof sink === "function" ? sink : null;
  }

  detachTerminalSink() { this._terminalSink = null; }

  _pushTerminalFrame(text, status = "") {
    if (!this._terminalSink) return;
    const body = String(text || "");
    const stat = String(status || "");
    if (!body && !stat) return;
    this._terminalFlushChain = this._terminalFlushChain.then(async () => {
      try { await this._terminalSink(body, stat); } catch {}
    });
  }

  async ensureStarted() {
    if (this._state === "ready") return;
    if (this._state === "starting") {
      if (this._startupDeferred) return this._startupDeferred.promise;
    }
    if (this._state === "stopped" || this._state === "failed") {
      throw new Error(`HermesManagedGatewaySession ${this.agentId} is ${this._state}`);
    }
    this._state = "starting";
    this._startupDeferred = createDeferred();
    const deferred = this._startupDeferred;

    try {
      this._port = await pickPort();
      const hermesCommand = String(process.env.AIFY_HERMES_COMMAND || "hermes").trim() || "hermes";
      const args = [
        // NOTE: `--tui` is NOT passed — hermes 0.15.1's `dashboard` subcommand rejects
        // it. The embedded-chat/`/api/ws` feature it used to enable is now turned on
        // via the HERMES_DASHBOARD_TUI=1 env below (mirrors ensureGatewayHost).
        "dashboard",
        "--port", String(this._port),
        "--host", "127.0.0.1",
        "--no-open",
        "--skip-build",
      ];
      this._emit("spawn", { command: hermesCommand, port: this._port });
      this._proc = spawn(hermesCommand, args, {
        stdio: ["ignore", "pipe", "pipe"],
        // Managed agents run unattended — the gateway host that runs the dispatch
        // turn must carry YOLO (hermes freezes it from HERMES_YOLO_MODE at import,
        // tools/approval.py) so it never blocks on tool-approval prompts.
        // HERMES_DASHBOARD_TUI=1 enables the embedded-chat feature that gates the
        // `/api/ws` WebSocket (else it closes 4403 → "gateway websocket connection
        // failed"). Mirrors the active hermes-managed-host.js ensureGatewayHost spawn.
        env: { ...process.env, HERMES_YOLO_MODE: "1", HERMES_DASHBOARD_TUI: "1" },
        // HARD no-popup requirement (operator): hide the window even though this
        // opt-in gateway-session path (AIFY_HERMES_MANAGED_USE_GATEWAY=1, off by
        // default and slated for removal) is not the live managed path.
        windowsHide: true,
      });
      this._proc.on("exit", (code, signal) => {
        if (this._state !== "failed") this._state = "stopped";
        this._emit("exit", { code, signal });
        hermesGatewayPool.delete(this.agentId);
      });
      this._proc.on("error", (err) => {
        this._state = "failed";
        this._emit("spawn-error", { message: err?.message || String(err) });
        deferred.reject(err);
      });

      const dashboardUrl = `http://127.0.0.1:${this._port}`;
      // Pass a liveness probe so a spawn-then-die child aborts the wait immediately
      // instead of polling for the full startup timeout (bughunt 2026-07-03).
      await waitForReady(dashboardUrl + "/", DEFAULT_STARTUP_TIMEOUT_MS, () => this._state === "stopped" || this._state === "failed");
      this._token = await fetchToken(dashboardUrl + "/");
      this._gatewayUrl = `ws://127.0.0.1:${this._port}/api/ws?token=${this._token}`;
      await this._openSocket();

      this._state = "ready";
      this._armIdleTimer();
      deferred.resolve();
      this._startupDeferred = null;
      this._emit("ready", { gatewayUrl: this._gatewayUrl });
    } catch (err) {
      this._state = "failed";
      if (this._proc) { try { terminateProcessTree(this._proc, "SIGTERM"); } catch {} }
      hermesGatewayPool.delete(this.agentId);
      deferred.reject(err);
      this._startupDeferred = null;
      throw err;
    }
  }

  _openSocket() {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(this._gatewayUrl);
      this._socket = socket;
      socket.on("open", () => resolve());
      socket.on("error", (err) => {
        if (this._state !== "ready") reject(err);
        else this._emit("ws-error", { message: err?.message || String(err) });
      });
      socket.on("close", () => {
        this._socket = null;
        // Reject any pending RPCs so callers don't hang.
        for (const [, pending] of this._pendingRpc) pending.reject(new Error("hermes gateway WS closed"));
        this._pendingRpc.clear();
        // Force-settle the ACTIVE TURN too (bughunt 2026-07-03): the turn loop polls
        // `while(!turn.settled)`, which is set only by agent.message.end events over this
        // socket. A dropped socket mid-turn left it spinning until the 12h turn timeout,
        // blocking the delivery queue. Settle it with an error like codex-session._onExit.
        if (this._activeTurn && !this._activeTurn.settled) {
          this._activeTurn.finalError = "hermes gateway WS closed mid-turn";
          this._activeTurn.settled = true;
        }
      });
      socket.on("message", (raw) => this._onSocketMessage(raw));
    });
  }

  _onSocketMessage(raw) {
    let msg;
    try { msg = JSON.parse(String(raw)); } catch { return; }
    if (msg.id !== undefined && this._pendingRpc.has(msg.id)) {
      const pend = this._pendingRpc.get(msg.id);
      this._pendingRpc.delete(msg.id);
      if (msg.error) pend.reject(msg.error);
      else pend.resolve(msg.result);
      return;
    }
    const ev = translateGatewayEvent(msg);
    if (!ev) return;
    const turn = this._activeTurn;
    if (!turn) {
      // Late event after a turn settled — drop.
      return;
    }
    if (ev.kind === "delta") {
      turn.finalText += ev.text;
      this._pushTerminalFrame(ev.text);
    } else if (ev.kind === "final") {
      turn.finalText = ev.text || turn.finalText;
      if (String(ev.status || "").trim().toLowerCase() === "error") {
        turn.finalError = turn.finalText || "Hermes turn failed";
      }
      if (ev.warning) {
        turn.finalError = turn.finalError || ev.warning;
      }
      this._pushTerminalFrame("\r\n\x1b[36m\x1b[1m■ turn ended\x1b[0m\r\n");
      turn.settled = true;
    } else if (ev.kind === "tool_started") {
      this._pushTerminalFrame(`\r\n\x1b[33m→ ${ev.label}\x1b[0m\r\n`);
    } else if (ev.kind === "tool_completed") {
      this._pushTerminalFrame(`\x1b[32m✓ ${ev.label}\x1b[0m\r\n`);
    } else if (ev.kind === "error") {
      turn.finalError = ev.text;
      this._pushTerminalFrame(`\r\n\x1b[31m\x1b[1m✗ error\x1b[0m ${ev.text}\r\n`);
      turn.settled = true;
    }
  }

  _sendRpc(frame) {
    return new Promise((resolve, reject) => {
      if (!this._socket || this._socket.readyState !== WebSocket.OPEN) {
        reject(new Error("hermes gateway WS not open"));
        return;
      }
      const id = frame.id ?? (this._nextRpcId++);
      frame.id = id;
      const timer = setTimeout(() => {
        this._pendingRpc.delete(id);
        reject(new Error(`hermes RPC ${frame.method} timed out`));
      }, 60_000);
      this._pendingRpc.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this._socket.send(JSON.stringify(frame));
    });
  }

  async _resolveSessionId() {
    if (this._sessionId) return this._sessionId;
    const result = await this._sendRpc(buildSessionMostRecentFrame({})).catch(() => null);
    this._sessionId = String(result?.session_id || "").trim();
    if (!this._sessionId) {
      // No existing session — create one. Note: session.create is the proper
      // primitive; relying on most_recent is best-effort for ambient state.
      const created = await this._sendRpc({
        jsonrpc: "2.0",
        method: "session.create",
        params: { cwd: this.agentInfo?.cwd || process.cwd() },
      });
      this._sessionId = String(created?.session_id || created?.id || "").trim();
    }
    return this._sessionId;
  }

  async runTurn({ promptText, run, callbacks = {}, runtimeState = {} }) {
    await this.ensureStarted();
    const deferred = createDeferred();
    this._turnQueue = this._turnQueue
      .catch(() => {})
      .then(() => this._runTurnInner({ promptText, run, callbacks, runtimeState }))
      .then(deferred.resolve, deferred.reject);
    return deferred.promise;
  }

  async _runTurnInner({ promptText, run, callbacks }) {
    if (this._state !== "ready") throw new Error(`HermesManagedGatewaySession ${this.agentId} not ready (${this._state})`);
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }

    const sessionId = await this._resolveSessionId();
    if (!sessionId) throw new Error("could not resolve hermes session id");
    try { callbacks.onRefs?.({ sessionId }); } catch {}

    const turn = {
      finalText: "",
      finalError: "",
      settled: false,
    };
    this._activeTurn = turn;

    try {
      // Echo prompt body into synth terminal
      const body = String(run?.body || "").trim();
      if (body) {
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        const header = subject ? `\r\n\x1b[92m>\x1b[0m [${from}] ${subject}\r\n` : `\r\n\x1b[92m>\x1b[0m [${from}]\r\n`;
        this._pushTerminalFrame(`${header}${body.split(/\r?\n/).map((l) => `\x1b[92m>\x1b[0m ${l}`).join("\r\n")}\r\n`, "running");
      }
      this._pushTerminalFrame("\x1b[2m[hermes] working...\x1b[0m\r\n", "running");

      const text = String(promptText || body || "");
      try {
        await this._sendRpc(buildPromptSubmitFrame({ sessionId, text }));
        callbacks.onEvent?.("hermes", `prompt.submit accepted on session ${sessionId}`);
      } catch (err) {
        if (isSessionBusyError(err)) {
          callbacks.onEvent?.("hermes", `prompt.submit busy; falling back to session.steer on ${sessionId}`);
          await this._sendRpc(buildSessionSteerFrame({ sessionId, text }));
          callbacks.onEvent?.("hermes", `session.steer queued on ${sessionId}`);
          return {
            status: "completed",
            summary: `Steered into running turn: ${text.slice(0, 80)}${text.length > 80 ? "..." : ""}`,
            runtimeState: { sessionId },
            externalRefs: { sessionId },
          };
        }
        throw new Error(`hermes prompt.submit failed: ${err?.message || JSON.stringify(err)}`);
      }

      // Wait for agent.message.end (sets turn.settled).
      const startedAt = Date.now();
      const timeoutMs = Number(this.agentInfo?.runtimeConfig?.timeoutMs || DEFAULT_TURN_TIMEOUT_MS);
      while (!turn.settled) {
        if (Date.now() - startedAt > timeoutMs) {
          throw new Error(`hermes turn timed out after ${timeoutMs}ms`);
        }
        await new Promise((r) => setTimeout(r, 100));
      }

      if (turn.finalError) {
        throw new Error(turn.finalError);
      }
      return {
        status: "completed",
        summary: turn.finalText.trim() || "(no output)",
        runtimeState: { sessionId },
        externalRefs: { sessionId },
      };
    } finally {
      this._activeTurn = null;
      if (this._state === "ready") this._armIdleTimer();
    }
  }

  _armIdleTimer() {
    if (this._idleTimer) clearTimeout(this._idleTimer);
    const ms = Number(process.env.AIFY_HERMES_IDLE_TIMEOUT_MS) || DEFAULT_IDLE_TIMEOUT_MS;
    this._idleTimer = setTimeout(() => {
      this._emit("idle-reap", { agentId: this.agentId });
      this.stop().catch(() => {});
    }, ms);
    if (typeof this._idleTimer.unref === "function") this._idleTimer.unref();
  }

  async cancelActiveTurn() {
    if (!this._activeTurn || !this._sessionId) return;
    try {
      await this._sendRpc({
        jsonrpc: "2.0",
        method: "session.interrupt",
        params: { session_id: this._sessionId },
      });
    } catch {}
    this._activeTurn.settled = true;
  }

  async stop(reason = "stop") {
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
    this._state = "stopped";
    try { this._socket?.close(); } catch {}
    if (this._proc) { try { terminateProcessTree(this._proc, "SIGTERM"); } catch {} }
    hermesGatewayPool.delete(this.agentId);
  }
}

export function getOrCreateHermesGatewaySession({ agentId, agentInfo, onPoolEvent }) {
  const key = String(agentId || "").trim();
  if (!key) throw new Error("agentId required for HermesManagedGatewaySession pool");
  const existing = hermesGatewayPool.get(key);
  if (existing) {
    if (existing._state === "stopped" || existing._state === "failed") {
      hermesGatewayPool.delete(key);
    } else {
      return existing;
    }
  }
  const sess = new HermesManagedGatewaySession({ agentId: key, agentInfo, onPoolEvent });
  hermesGatewayPool.set(key, sess);
  return sess;
}

export async function shutdownAllHermesGatewaySessions(reason = "shutdown") {
  const sessions = [...hermesGatewayPool.values()];
  hermesGatewayPool.clear();
  await Promise.all(sessions.map((s) => s.stop(reason).catch(() => {})));
}
export function __injectHermesGatewaySessionForTests(agentId, session) { hermesGatewayPool.set(agentId, session); }
export function __hermesGatewayPoolSize() { return hermesGatewayPool.size; }

export function _resetHermesGatewayPoolForTests() {
  for (const [, sess] of hermesGatewayPool) {
    try { sess.stop(); } catch {}
  }
  hermesGatewayPool.clear();
}

export function managedHermesUsesGateway() {
  return String(process.env.AIFY_HERMES_MANAGED_USE_GATEWAY || "").trim() === "1";
}
