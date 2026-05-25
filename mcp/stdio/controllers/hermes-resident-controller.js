// HermesResidentController — extracted from createHermesResidentChannelController
// in runtimes.js as part of Plan 3 Task 10. Resident-hermes wake via tui_gateway
// /api/ws.
//
// The bridge connects a SECOND WebSocket to the local hermes dashboard gateway
// (the first WS belongs to the operator's `hermes chat --tui` Ink TUI) and
// injects prompt.submit (idle session) or session.steer (mid-run, when
// prompt.submit returns 4009). TeeTransport in tui_gateway/transport.py mirrors
// dispatcher events to all attached clients — operator sees the bridge-injected
// turn + model reply in their live terminal TUI.
//
// File budget per 500-line rule: <=400 lines.

import WebSocket from "ws";
import { BaseController } from "./base-controller.js";
import { getRuntimeConfig } from "../runtimes-helpers.js";

export class HermesResidentController extends BaseController {
  constructor(opts) {
    super(opts);
    this._started = false;
    this._capabilities = { interrupt: true, steer: true };
    this._socket = null;
    this._settled = false;
    this._promise = null;
  }

  start() {
    if (this._started) return this._legacyShape();
    this._started = true;

    const { agentId, agentInfo, run, callbacks } = this.opts;
    const cfg = getRuntimeConfig(agentInfo);
    const gatewayUrl = String(cfg.gatewayUrl || "").trim();
    const timeoutMs = Number(cfg.timeoutMs || 12 * 60 * 60 * 1000);
    const residentSessionId = String(agentInfo.sessionHandle || "").trim();

    let nextRpcId = 100;
    const pending = new Map();
    let socket = null;
    let settled = false;
    let resolvePromise = null;
    let rejectPromise = null;
    let finalText = "";
    let resolvedSessionId = residentSessionId;

    let terminalSink = null;
    let sinkChain = Promise.resolve();
    const pushTerminalFrame = (text, status = "") => {
      try {
        if (!terminalSink || (!text && !status)) return;
        const frame = { text: String(text || ""), status: String(status || "") };
        sinkChain = sinkChain.then(async () => {
          try { await terminalSink(frame.text, frame.status); } catch {}
        });
      } catch {}
    };

    const settle = (kind, valueOrError) => {
      if (settled) return;
      settled = true;
      this._settled = true;
      try { if (socket && socket.readyState === socket.OPEN) socket.close(); } catch {}
      if (kind === "resolve") resolvePromise?.(valueOrError);
      else rejectPromise?.(valueOrError);
    };

    this._promise = new Promise(async (resolve, reject) => {
      resolvePromise = resolve;
      rejectPromise = reject;
      const overallTimer = setTimeout(() => {
        settle("reject", new Error(`Hermes resident channel timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      if (overallTimer && typeof overallTimer.unref === "function") overallTimer.unref();

      // Resolve synth-terminal sink so the operator sees a Console echo even
      // if the gateway events come through TeeTransport to the Ink TUI alone.
      if (typeof callbacks?.terminalSinkProvider === "function") {
        try {
          const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
          if (typeof sink === "function") terminalSink = sink;
        } catch {}
      }

      // Echo the dispatch body into Console BEFORE the WS turn fires so
      // the operator sees which agent is waking them.
      try {
        const body = String(run?.body || "").trim();
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        const header = subject ? `\r\n\x1b[92m>\x1b[0m [${from}] ${subject}\r\n` : `\r\n\x1b[92m>\x1b[0m [${from}]\r\n`;
        const prefixed = body.split(/\r?\n/).map((l) => `\x1b[92m>\x1b[0m ${l}`).join("\r\n");
        pushTerminalFrame(`${header}${prefixed}\r\n`, "running");
        pushTerminalFrame("\x1b[2m[hermes] connecting...\x1b[0m\r\n", "running");
      } catch {}

      let proto;
      try {
        proto = await import("../hermes-gateway-protocol.js");
      } catch (err) {
        clearTimeout(overallTimer);
        settle("reject", err);
        return;
      }

      try {
        socket = new WebSocket(gatewayUrl);
        this._socket = socket;
      } catch (err) {
        clearTimeout(overallTimer);
        settle("reject", new Error(`Hermes gateway WS open failed: ${err?.message || err}`));
        return;
      }

      const sendRpc = (frame) => new Promise((rResolve, rReject) => {
        if (!socket || socket.readyState !== socket.OPEN) {
          rReject(new Error("Hermes gateway WS not open"));
          return;
        }
        const id = frame.id ?? (nextRpcId++);
        frame.id = id;
        const t = setTimeout(() => {
          pending.delete(id);
          rReject(new Error(`hermes RPC ${frame.method} timed out after 60s`));
        }, 60000);
        pending.set(id, {
          resolve: (v) => { clearTimeout(t); rResolve(v); },
          reject: (e) => { clearTimeout(t); rReject(e); },
        });
        socket.send(JSON.stringify(frame));
      });

      socket.on("message", (raw) => {
        let msg;
        try { msg = JSON.parse(String(raw)); } catch { return; }
        if (msg.id !== undefined && pending.has(msg.id)) {
          const pend = pending.get(msg.id);
          pending.delete(msg.id);
          if (msg.error) pend.reject(msg.error);
          else pend.resolve(msg.result);
          return;
        }
        const ev = proto.translateGatewayEvent(msg);
        if (!ev) return;
        if (ev.kind === "delta") {
          finalText += ev.text;
          pushTerminalFrame(String(ev.text));
        } else if (ev.kind === "final") {
          finalText = ev.text || finalText;
          pushTerminalFrame(`\r\n\x1b[36m\x1b[1m■ turn ended\x1b[0m\r\n`);
          clearTimeout(overallTimer);
          settle("resolve", {
            status: "completed",
            summary: finalText.trim() || "(no output)",
            runtimeState: resolvedSessionId ? { sessionId: resolvedSessionId } : {},
            externalRefs: resolvedSessionId ? { sessionId: resolvedSessionId } : {},
          });
        } else if (ev.kind === "tool_started") {
          pushTerminalFrame(`\r\n\x1b[33m→ ${ev.label}\x1b[0m\r\n`);
        } else if (ev.kind === "tool_completed") {
          pushTerminalFrame(`\x1b[32m✓ ${ev.label}\x1b[0m\r\n`);
        } else if (ev.kind === "error") {
          pushTerminalFrame(`\r\n\x1b[31m\x1b[1m✗ error\x1b[0m ${ev.text}\r\n`);
        }
      });
      socket.on("close", (code, reason) => {
        if (settled) return;
        clearTimeout(overallTimer);
        settle("reject", new Error(`Hermes gateway WS closed before turn completed (code=${code} reason=${String(reason || "")})`));
      });
      socket.on("error", (err) => {
        if (settled) return;
        clearTimeout(overallTimer);
        settle("reject", err);
      });

      try {
        await new Promise((res, rej) => {
          socket.once("open", res);
          socket.once("error", rej);
        });
      } catch (err) {
        clearTimeout(overallTimer);
        settle("reject", new Error(`Hermes gateway WS open failed: ${err?.message || err}`));
        return;
      }

      try {
        // Resolve session id: prefer the registered sessionHandle; otherwise
        // ask the gateway for its most recent session (which is the chat the
        // operator is currently in).
        if (!resolvedSessionId) {
          const mostRecent = await sendRpc(proto.buildSessionMostRecentFrame({})).catch(() => null);
          resolvedSessionId = String(mostRecent?.session_id || "").trim();
        }
        if (!resolvedSessionId) {
          throw new Error("Hermes gateway has no resolvable session — operator should open hermes chat first");
        }
        try { callbacks.onRefs?.({ sessionId: resolvedSessionId }); } catch {}

        const body = String(run?.body || "").trim();
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        const wireText = subject ? `[aify-comms wake from ${from}]\nSubject: ${subject}\n\n${body}` : `[aify-comms wake from ${from}]\n\n${body}`;

        try {
          callbacks.onEvent?.("hermes", `prompt.submit on session ${resolvedSessionId}`);
          await sendRpc(proto.buildPromptSubmitFrame({ sessionId: resolvedSessionId, text: wireText }));
          // No immediate settle — the agent.message.end event in the socket
          // 'message' handler closes the promise after streaming completes.
        } catch (err) {
          if (proto.isSessionBusyError(err)) {
            callbacks.onEvent?.("hermes", `prompt.submit busy; session.steer on ${resolvedSessionId}`);
            await sendRpc(proto.buildSessionSteerFrame({ sessionId: resolvedSessionId, text: wireText }));
            callbacks.onEvent?.("hermes", `session.steer queued on ${resolvedSessionId}`);
            clearTimeout(overallTimer);
            settle("resolve", {
              status: "completed",
              summary: `Steered into running turn: ${body.slice(0, 80)}${body.length > 80 ? "..." : ""}`,
              runtimeState: { sessionId: resolvedSessionId },
              externalRefs: { sessionId: resolvedSessionId },
            });
          } else {
            clearTimeout(overallTimer);
            settle("reject", new Error(`Hermes prompt.submit failed: ${err?.message || JSON.stringify(err)}`));
          }
        }
      } catch (err) {
        clearTimeout(overallTimer);
        settle("reject", err);
      }
    });

    return this._legacyShape();
  }

  _legacyShape() {
    return {
      capabilities: this._capabilities,
      interrupt: async () => this.interrupt(),
      steer: async () => this.steer(),
      promise: this._promise,
    };
  }

  async injectMessage(_opts) {
    throw new Error("hermes resident does not support direct message injection; send a follow-up dispatch");
  }

  async interrupt(_opts) {
    try {
      if (this._socket && this._socket.readyState === this._socket.OPEN) {
        this._socket.close();
      }
    } catch {}
  }

  async steer(_opts) {
    throw new Error("Direct steer not implemented for resident-hermes channel; send another comms_send and it will route via session.steer if the turn is still running.");
  }
}
