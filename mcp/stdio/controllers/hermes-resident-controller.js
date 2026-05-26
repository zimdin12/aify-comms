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
      // Plan 4 ready: tui_gateway WS connection established. Session resolve
      // and prompt.submit are dispatch-specific and follow.
      this.markReady();

      try {
        // Plan 6 follow-up (2026-05-26): hermes tui_gateway has a sid /
        // session_key duality. `prompt.submit` looks up in-memory
        // `_sessions[sid]` (short uuid bound to ONE transport). External
        // WS clients (us) can't see the operator's TUI sid. `session.list`
        // and `session.most_recent` return persisted session_keys, NOT
        // active sids — so prompt.submit on those fails with 4001.
        //
        // Recipe (see tui_gateway/server.py:2386 session.resume):
        //   1. Resolve a session_key (gateway live list → registered handle
        //      → session.most_recent).
        //   2. Call session.resume(session_id=<key>) → gateway loads the
        //      persisted session into a NEW in-memory sid bound to OUR ws.
        //   3. prompt.submit(session_id=<new sid>) is now legal.
        //
        // The operator's TUI keeps its own sid; both write to the same DB
        // session_key. For aify-comms purposes (one-shot dispatch +
        // streamed reply) this is sufficient — the bridge captures the
        // reply, completes the run, and posts the response to comms chat.
        let sessionKey = resolvedSessionId; // may be the registered handle
        const liveList = await sendRpc(proto.buildSessionListFrame({})).catch(() => null);
        const freshFromList = proto.pickFreshestSessionFromList(liveList);
        if (freshFromList) sessionKey = freshFromList;
        if (!sessionKey) {
          const mostRecent = await sendRpc(proto.buildSessionMostRecentFrame({})).catch(() => null);
          sessionKey = String(mostRecent?.session_id || mostRecent?.sessionId || "").trim();
        }
        if (!sessionKey) {
          throw new Error("Hermes gateway has no resolvable session — operator should open hermes chat first");
        }

        // Resume the persisted session_key into a fresh in-memory sid bound
        // to THIS ws. session.resume returns { session_id: <new sid>,
        // resumed: <session_key>, ... } per tui_gateway/server.py:2418.
        //
        // Fallback: session.create when resume fails (operator's session_key
        // is stale OR never existed). Always succeeds, creating a fresh
        // session in hermes' DB. We then submit to its sid.
        let newSid = "";
        let resumeError = null;
        try {
          const resumed = await sendRpc(proto.buildSessionResumeFrame({ sessionKey }));
          newSid = String(resumed?.session_id || resumed?.sessionId || "").trim();
        } catch (err) {
          resumeError = err;
        }
        if (!newSid) {
          // session.resume failed (stale key, GC'd, etc) — create a fresh
          // session so prompt.submit ALWAYS has a valid sid to target.
          const cwd = String(agentInfo?.cwd || "").trim();
          try {
            const created = await sendRpc(proto.buildSessionCreateFrame({ cwd }));
            newSid = String(created?.session_id || created?.sessionId || "").trim();
            const reason = resumeError ? (resumeError?.message || JSON.stringify(resumeError)) : "no session_id in resume response";
            callbacks.onEvent?.("hermes", `session.resume failed (${reason.slice(0,80)}); fell back to session.create -> sid ${newSid}`);
          } catch (createErr) {
            throw new Error(`Hermes session.resume(${sessionKey}) AND session.create both failed; resume: ${resumeError?.message || resumeError || 'no sid'}; create: ${createErr?.message || createErr}`);
          }
        } else {
          if (resolvedSessionId && resolvedSessionId !== newSid) {
            callbacks.onEvent?.("hermes", `session id corrected: '${resolvedSessionId}' -> '${newSid}' (session.resume on ${sessionKey})`);
          } else {
            callbacks.onEvent?.("hermes", `session.resume on ${sessionKey} -> sid ${newSid}`);
          }
        }
        if (!newSid) {
          throw new Error("Hermes: no sid obtained from session.resume or session.create");
        }
        resolvedSessionId = newSid;
        try { callbacks.onRefs?.({ sessionId: resolvedSessionId, sessionKey }); } catch {}

        const body = String(run?.body || "").trim();
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        const wireText = subject ? `[aify-comms wake from ${from}]\nSubject: ${subject}\n\n${body}` : `[aify-comms wake from ${from}]\n\n${body}`;

        const submitOnce = async (sid) => {
          callbacks.onEvent?.("hermes", `prompt.submit on session ${sid}`);
          await sendRpc(proto.buildPromptSubmitFrame({ sessionId: sid, text: wireText }));
        };

        try {
          await submitOnce(resolvedSessionId);
          // No immediate settle — the agent.message.end event in the socket
          // 'message' handler closes the promise after streaming completes.
        } catch (err) {
          // Plan 6 follow-up: if prompt.submit still fails with 4001 even
          // after our session.resume above (e.g. the freshly-allocated sid
          // got reaped between resume and submit, or the gateway returned
          // a sid bound to a different transport), do a single re-resume
          // and retry.
          if (proto.isSessionNotFoundError(err)) {
            try {
              const reresumeList = await sendRpc(proto.buildSessionListFrame({})).catch(() => null);
              const reresumeKey = proto.pickFreshestSessionFromList(reresumeList) || sessionKey;
              const retried = await sendRpc(proto.buildSessionResumeFrame({ sessionKey: reresumeKey }));
              const retriedSid = String(retried?.session_id || retried?.sessionId || "").trim();
              if (!retriedSid) throw new Error("session.resume retry returned no sid");
              callbacks.onEvent?.("hermes", `prompt.submit session_not_found; re-resumed ${reresumeKey} -> sid ${retriedSid} and retrying`);
              resolvedSessionId = retriedSid;
              try { callbacks.onRefs?.({ sessionId: resolvedSessionId, sessionKey: reresumeKey }); } catch {}
              await submitOnce(resolvedSessionId);
              // Retry succeeded — fall through to wait for streaming end.
            } catch (retryErr) {
              clearTimeout(overallTimer);
              settle("reject", new Error(`Hermes prompt.submit failed after re-resume: ${retryErr?.message || JSON.stringify(retryErr)}`));
              return;
            }
          } else if (proto.isSessionBusyError(err)) {
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
