// HermesResidentController — extracted from createHermesResidentChannelController
// in runtimes.js as part of Plan 3 Task 10. Resident-hermes wake via tui_gateway
// /api/ws.
//
// The bridge connects a SECOND WebSocket to the local hermes dashboard gateway
// (the first WS belongs to the operator's `hermes chat --tui` Ink TUI). It must
// bind that bridge transport onto the active visible TUI session before
// prompt.submit/session.steer. Calling session.resume/session.create here forks
// hidden in-memory sessions that do not render in the operator console.
//
// File budget per 500-line rule: <=400 lines.

import WebSocket from "ws";
import { BaseController } from "./base-controller.js";
import { getRuntimeConfig } from "../runtimes-helpers.js";

function cleanNoticeLine(value = "") {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function previewNoticeBody(value = "", limit = 700) {
  const normalized = String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit).trimEnd()}\n...`;
}

function wrapNoticeText(value = "", width = 72) {
  const lines = [];
  const paragraphs = String(value || "").split("\n");
  for (const paragraph of paragraphs) {
    const words = paragraph.trim().split(/\s+/).filter(Boolean);
    if (!words.length) {
      lines.push("");
      continue;
    }
    let line = "";
    for (const word of words) {
      if (!line) {
        line = word;
        continue;
      }
      if ((line.length + 1 + word.length) <= width) {
        line = `${line} ${word}`;
      } else {
        lines.push(line);
        line = word;
      }
    }
    if (line) lines.push(line);
  }
  return lines;
}

function boxNoticeLine(value = "", width = 72) {
  const text = String(value || "");
  const clipped = text.length > width ? text.slice(0, Math.max(0, width - 3)).trimEnd() + "..." : text;
  return `| ${clipped.padEnd(width, " ")} |`;
}

export function buildHermesVisibleWakeNotice({ from = "", to = "", subject = "", body = "" } = {}) {
  const sender = cleanNoticeLine(from) || "dashboard";
  const target = cleanNoticeLine(to);
  const title = cleanNoticeLine(subject);
  const preview = previewNoticeBody(body);
  const width = 72;
  const border = `+${"-".repeat(width + 2)}+`;
  const lines = [
    border,
    boxNoticeLine("aify-comms message", width),
    boxNoticeLine(target ? `${sender} -> ${target}` : `from ${sender}`, width),
  ];
  if (title) lines.push(boxNoticeLine(`Subject: ${title}`, width));
  if (preview) {
    lines.push(border);
    for (const line of wrapNoticeText(preview, width)) {
      lines.push(boxNoticeLine(line, width));
    }
  }
  lines.push(border);
  return lines.join("\n");
}

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
    let sessionKey = residentSessionId;

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
      try {
        if (socket && socket.readyState !== socket.CLOSED) {
          socket.close();
          const closeKiller = setTimeout(() => {
            try { if (socket.readyState !== socket.CLOSED) socket.terminate(); } catch {}
          }, 1000);
          if (typeof closeKiller.unref === "function") closeKiller.unref();
        }
      } catch {}
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
          callbacks.onEvent?.("hermes", `turn completed (${String(ev.status || "complete")})`);
          pushTerminalFrame(`\r\n\x1b[36m\x1b[1m■ turn ended\x1b[0m\r\n`);
          clearTimeout(overallTimer);
          const finalStatus = String(ev.status || "").trim().toLowerCase();
          if (finalStatus === "error") {
            settle("reject", new Error(finalText.trim() || "Hermes turn failed"));
            return;
          }
          settle("resolve", {
            status: "completed",
            summary: finalText.trim() || "(no output)",
            runtimeState: {
              ...(sessionKey ? { sessionId: sessionKey } : {}),
              ...(resolvedSessionId ? { gatewaySessionId: resolvedSessionId } : {}),
            },
            externalRefs: {
              ...(sessionKey ? { sessionId: sessionKey } : {}),
              ...(resolvedSessionId ? { gatewaySessionId: resolvedSessionId } : {}),
            },
          });
        } else if (ev.kind === "tool_started") {
          callbacks.onEvent?.("hermes", `tool started: ${ev.label}`);
          pushTerminalFrame(`\r\n\x1b[33m→ ${ev.label}\x1b[0m\r\n`);
        } else if (ev.kind === "tool_completed") {
          callbacks.onEvent?.("hermes", `tool completed: ${ev.label}`);
          pushTerminalFrame(`\x1b[32m✓ ${ev.label}\x1b[0m\r\n`);
        } else if (ev.kind === "start") {
          callbacks.onEvent?.("hermes", "turn started");
        } else if (ev.kind === "error") {
          pushTerminalFrame(`\r\n\x1b[31m\x1b[1m✗ error\x1b[0m ${ev.text}\r\n`);
          clearTimeout(overallTimer);
          settle("reject", new Error(ev.text || "Hermes gateway error"));
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
          const cleanup = () => {
            socket.off("open", onOpen);
            socket.off("error", onError);
            socket.off("close", onClose);
          };
          const onOpen = () => { cleanup(); res(); };
          const onError = (err) => { cleanup(); rej(err); };
          const onClose = (code, reason) => {
            cleanup();
            rej(new Error(`Hermes gateway WS closed before open (code=${code} reason=${String(reason || "")})`));
          };
          socket.once("open", onOpen);
          socket.once("error", onError);
          socket.once("close", onClose);
        });
      } catch (err) {
        clearTimeout(overallTimer);
        if (settled) return;
        settle("reject", new Error(`Hermes gateway WS open failed: ${err?.message || err}`));
        return;
      }
      // Plan 4 ready: tui_gateway WS connection established. Session resolve
      // and prompt.submit are dispatch-specific and follow.
      this.markReady();

      try {
        // Hermes has a durable session_key / short active-sid split.
        // session.list and session.most_recent return durable keys; the TUI
        // renders events only for its active sid. The aify extension installed
        // into tui_gateway resolves the durable key to the already-visible sid
        // and tees this bridge transport into that session. If the extension is
        // unavailable, fail visibly instead of resuming/creating a hidden sid.
        sessionKey = resolvedSessionId; // may be the registered handle
        if (!sessionKey) {
          const liveList = await sendRpc(proto.buildSessionListFrame({})).catch(() => null);
          sessionKey = proto.pickFreshestSessionFromList(liveList);
        }
        if (!sessionKey) {
          const mostRecent = await sendRpc(proto.buildSessionMostRecentFrame({})).catch(() => null);
          sessionKey = String(mostRecent?.session_id || mostRecent?.sessionId || "").trim();
        }
        if (!sessionKey) {
          throw new Error("Hermes gateway has no resolvable session — operator should open hermes chat first");
        }

        const bindVisibleSession = async (key) => {
          const bound = await sendRpc(proto.buildAifySessionBindTransportFrame({ sessionKey: key }));
          return {
            visibleSid: String(bound?.session_id || bound?.sessionId || "").trim(),
            sessionKey: String(bound?.session_key || bound?.sessionKey || "").trim() || key,
          };
        };
        let visibleSid = "";
        try {
          const bound = await bindVisibleSession(sessionKey);
          visibleSid = bound.visibleSid;
          if (bound.sessionKey && bound.sessionKey !== sessionKey) {
            callbacks.onEvent?.("hermes", `visible session key corrected: ${sessionKey} -> ${bound.sessionKey}`);
            sessionKey = bound.sessionKey;
          }
        } catch (err) {
          const originalSessionKey = sessionKey;
          const candidates = [];
          const addCandidate = (value) => {
            const candidate = String(value || "").trim();
            if (candidate && candidate !== originalSessionKey && !candidates.includes(candidate)) candidates.push(candidate);
          };
          const liveList = await sendRpc(proto.buildSessionListFrame({})).catch(() => null);
          addCandidate(proto.pickFreshestSessionFromList(liveList));
          const mostRecent = await sendRpc(proto.buildSessionMostRecentFrame({})).catch(() => null);
          addCandidate(mostRecent?.session_id || mostRecent?.sessionId || mostRecent?.id);
          for (const candidate of candidates) {
            try {
              callbacks.onEvent?.("hermes", `visible session bind retry: ${originalSessionKey} -> ${candidate}`);
              const rebound = await bindVisibleSession(candidate);
              visibleSid = rebound.visibleSid;
              sessionKey = rebound.sessionKey || candidate;
              if (sessionKey !== originalSessionKey) {
                callbacks.onEvent?.("hermes", `visible session key corrected: ${originalSessionKey} -> ${sessionKey}`);
              }
              break;
            } catch (retryErr) {
              callbacks.onEvent?.("hermes", `visible session bind retry failed for ${candidate}: ${retryErr?.message || JSON.stringify(retryErr)}`);
            }
          }
          if (!visibleSid) {
            throw new Error(`Hermes visible-session binding failed for ${originalSessionKey}: ${err?.message || JSON.stringify(err)}. Re-run install.sh --client hermes and restart hermes-aify; refusing to create a hidden session.`);
          }
        }
        if (!visibleSid) {
          throw new Error(`Hermes visible-session binding returned no active sid for ${sessionKey}. Re-run install.sh --client hermes and restart hermes-aify; refusing to create a hidden session.`);
        }
        resolvedSessionId = visibleSid;
        callbacks.onEvent?.("hermes", `visible session bound: ${sessionKey} -> ${visibleSid}`);
        try { callbacks.onRefs?.({ sessionId: resolvedSessionId, sessionKey }); } catch {}

        const body = String(run?.body || "").trim();
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        const deliveryNotes = [
          "AIFY-COMMS DELIVERY INSTRUCTIONS:",
          "- This prompt was delivered by the aify-comms bridge.",
          "- Your final assistant response is captured and posted back to the sender automatically.",
          "- Do not call comms_send, local HTTP, curl, browser, or terminal tools just to acknowledge or reply.",
          "- If the request can be answered directly, answer directly in final text.",
          "",
        ].join("\n");
        const messageText = buildHermesVisibleWakeNotice({ from, to: agentId, subject, body });
        const wireText = `${deliveryNotes}${messageText}`;

        try {
          await sendRpc(proto.buildAifySessionRenderNoticeFrame({
            sessionId: resolvedSessionId,
            notice: buildHermesVisibleWakeNotice({ from, to: agentId, subject, body }),
            status: "aify-comms message received",
          })).catch((err) => {
            callbacks.onEvent?.("hermes", `visible notice skipped: ${err?.message || JSON.stringify(err)}`);
          });
        } catch {}

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
          // after visible binding, fail visibly. Falling back to
          // session.resume/session.create would fork a hidden session and
          // break harness-console delivery.
          if (proto.isSessionNotFoundError(err)) {
            clearTimeout(overallTimer);
            settle("reject", new Error(`Hermes visible session ${resolvedSessionId} disappeared before prompt.submit; restart hermes-aify and re-register. Refusing hidden session fallback.`));
            return;
          } else if (proto.isSessionBusyError(err)) {
            callbacks.onEvent?.("hermes", `prompt.submit busy; session.steer on ${resolvedSessionId}`);
            await sendRpc(proto.buildSessionSteerFrame({ sessionId: resolvedSessionId, text: wireText }));
            callbacks.onEvent?.("hermes", `session.steer queued on ${resolvedSessionId}`);
            clearTimeout(overallTimer);
            settle("resolve", {
              status: "completed",
              summary: `Steered into running turn: ${body.slice(0, 80)}${body.length > 80 ? "..." : ""}`,
              runtimeState: {
                ...(sessionKey ? { sessionId: sessionKey } : {}),
                ...(resolvedSessionId ? { gatewaySessionId: resolvedSessionId } : {}),
              },
              externalRefs: {
                ...(sessionKey ? { sessionId: sessionKey } : {}),
                ...(resolvedSessionId ? { gatewaySessionId: resolvedSessionId } : {}),
              },
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
