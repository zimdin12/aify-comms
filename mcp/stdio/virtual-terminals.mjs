// Virtual terminals: the per-agent registry, the sink that streams their output, and the control surface
// the dashboard drives them through.
//
// A "virtual" terminal is one with no pty behind it — a pi session's console, streamed line by line rather
// than by attaching to a process. `server.js` owned the whole family: the registry Map, the input manager,
// the ensure/dispatch/sink path, and the control handler.
//
// WHY THIS IS EXTRACTABLE AT ALL, when `docs/JS_SERVER_REMAINDER_PACKET.md` measured only ~105 clean lines
// left in server.js. That census was PER FUNCTION, which counts a call between two functions that move
// together as a blocker — the same criterion that wrongly shelved `hermes-managed-host.js` and declared
// `app.js` irreducible, and was withdrawn in both cases. Measured as a GROUP this is seven declarations and
// 144 lines whose entire external surface is seven names server.js already imports from elsewhere.
//
// It does NOT touch the packet's open question. The four scheduler loops and `runDispatchLoop` are
// untouched, and the A-vs-C decision about them is still the operator's.
//
// THE TWO MAPS MOVE, and server.js imports them back: `VIRTUAL_TERMINALS_BY_AGENT` and
// `VIRTUAL_TERMINAL_INPUT` are read by four sites that stay behind (the boot reset, the terminal-dead
// sweep, the input append path, and the teardown). A Map's identity is stable across an ES module import,
// so those readers keep mutating the same object — one owner, several readers, which is the arrangement
// this series has converged on.
//
// Bodies are byte-identical to those in server.js; the only substitution is the added `export `.


import { httpCall } from "./aify-service-endpoint.mjs";
import { REMOTE_AGENT_STATE } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { __markControllerStart } from "./controller-activity.mjs";
import { acquirePiSession, getPiSession } from "./pi-session-pool.mjs";
import { createVirtualTerminalInputManager } from "./virtual-terminal-input.js";

// agentId → { terminalId, runtime } for the bridge's synthesized RPC
// terminal. Cached so subsequent dispatches reuse the same virtual
// terminal_session row. Covers both managed pi (persistent omp --mode rpc
// child) and managed hermes (per-dispatch `hermes chat -q -Q` with a
// synthesized request/response feed).
export const VIRTUAL_TERMINALS_BY_AGENT = new Map();
// Dashboard input buffering for synthesized pi RPC terminals. See
// virtual-terminal-input.js for the buffer-and-dispatch semantics.
export const VIRTUAL_TERMINAL_INPUT = createVirtualTerminalInputManager({
  dispatch: (agentId, line) => dispatchVirtualTerminalLine(agentId, line),
  onError: (error, ctx) => {
    console.error(`[aify] virtual-terminal dispatch failed for "${ctx.agentId}" (line=${JSON.stringify(ctx.line?.slice(0, 80) || "")}): ${error?.message || error}`);
  },
});
export async function ensureVirtualTerminal(agentId, agentInfo, runtime) {
  const key = String(agentId || "").trim();
  const rt = String(runtime || "").trim();
  if (!key || !rt) return null;
  const cached = VIRTUAL_TERMINALS_BY_AGENT.get(key);
  if (cached?.terminalId && cached.runtime === rt) return cached;
  const sessionHandle = String(agentInfo?.sessionHandle || agentInfo?.runtimeState?.sessionId || "").trim();
  const workspace = String(agentInfo?.cwd || "").trim();
  const res = await httpCall("POST", `/agents/${encodeURIComponent(key)}/virtual-terminal/ensure`, {
    bridgeId: BRIDGE_INSTANCE_ID,
    sessionHandle,
    workspace,
    runtime: rt,
    requestedBy: "bridge-rpc",
  });
  const terminalId = String(res?.terminal?.id || "").trim();
  if (!terminalId) throw new Error("virtual-terminal/ensure returned no terminal id");
  const entry = { terminalId, runtime: rt };
  VIRTUAL_TERMINALS_BY_AGENT.set(key, entry);
  return entry;
}
export async function dispatchVirtualTerminalLine(agentId, lineBody) {
  // Drive the persistent PiSession from operator-typed terminal input. This
  // is intentionally lighter than launchRuntimeRun: there's no dispatch_run
  // row, no agent_status/turn_busy management, no runtime-state PATCH. The
  // synthesized terminal stream is the only operator-visible artifact.
  const state = REMOTE_AGENT_STATE.get(String(agentId || "").trim());
  if (!state) throw new Error(`No bridge state for agent "${agentId}"`);
  const agentInfo = state.info || {};
  const sessionHandle = String(agentInfo?.sessionHandle || agentInfo?.runtimeState?.sessionId || "").trim();
  const session = await acquirePiSession({
    agentId,
    agentInfo,
    sessionId: sessionHandle,
    cwd: agentInfo?.cwd || process.cwd(),
    onPoolEvent: () => {},
  });
  const entry = await ensureVirtualTerminal(agentId, agentInfo, "pi");
  if (entry?.terminalId) session.attachTerminalSink(createVirtualTerminalSink(entry.terminalId));
  const syntheticRun = {
    from: "dashboard",
    subject: "Operator console input",
    body: String(lineBody || ""),
    type: "request",
    executionMode: "managed",
    requireReply: false,
  };
  const turnHandle = session.runTurn(syntheticRun, {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  });
  // Status gap A (2026-06-11, cross-harness audit): an operator-typed pi console turn ran
  // with NO turn tracking (deliberately no dispatch_run row — but that also skipped the
  // turn-busy heartbeat), so the agent read `online` for the whole turn. Registering the
  // turn promise arms the existing 30s turn-busy re-pulse for its duration.
  __markControllerStart(turnHandle.promise);
  return turnHandle.promise;
}
export function createVirtualTerminalSink(terminalId) {
  const id = String(terminalId || "").trim();
  if (!id) return null;
  return async (output, status = "") => {
    if (!output && !status) return;
    // Retry transient POST failures up to 3 times so text_delta frames
    // during a long claude/pi turn aren't silently lost when the
    // service is briefly unreachable (e.g., container rebuild blip).
    // Operator-reported (2026-05-22): pi terminal output stopped at
    // "▶ turn started" with only one character of the assistant's
    // reply visible — the subsequent text_delta POSTs fell on the
    // floor during a service-restart window. 404 always means the
    // terminal row is gone — don't retry, invalidate the cache.
    let lastErr = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await httpCall("POST", `/terminals/${encodeURIComponent(id)}/output`, {
          bridgeId: BRIDGE_INSTANCE_ID,
          output: String(output || ""),
          status: String(status || ""),
        });
        return;
      } catch (error) {
        lastErr = error;
        const msg = error?.message || String(error);
        if (/^HTTP 404/.test(msg)) {
          for (const [key, value] of VIRTUAL_TERMINALS_BY_AGENT.entries()) {
            if (value?.terminalId === id) VIRTUAL_TERMINALS_BY_AGENT.delete(key);
          }
          return;
        }
        if (attempt < 2) {
          await new Promise((r) => setTimeout(r, 250 * Math.pow(2, attempt)));
        }
      }
    }
    // After 3 retries: still best-effort, but log so debug ledgers
    // show dropped frames rather than silent loss.
    console.error(
      `[aify] virtual terminal sink dropped frame for ${id} after 3 retries:`,
      lastErr?.message || lastErr,
    );
  };
}
export async function updateTerminalControl(controlId, body) {
  return httpCall("PATCH", `/terminals/controls/${encodeURIComponent(controlId)}`, body);
}
export async function handleVirtualTerminalControl(agentId, terminalId, control) {
  const action = String(control.action || "").trim();
  if (action === "input") {
    const rawBody = String(control.body || "");
    await VIRTUAL_TERMINAL_INPUT.append(agentId, terminalId, rawBody);
    await updateTerminalControl(control.id, { status: "completed", terminalStatus: "running" });
    return;
  }
  if (action === "resize") {
    // The synthesized terminal has no PTY dimensions; ack so the dashboard
    // doesn't keep retrying. Future: surface cols/rows in the dashboard hint.
    await updateTerminalControl(control.id, { status: "completed", terminalStatus: "running" });
    return;
  }
  if (action === "stop") {
    const session = getPiSession(agentId);
    if (session) await session.stop("virtual-terminal stop control");
    VIRTUAL_TERMINALS_BY_AGENT.delete(agentId);
    VIRTUAL_TERMINAL_INPUT.remove(terminalId);
    await updateTerminalControl(control.id, { status: "completed", terminalStatus: "stopped" });
    return;
  }
  if (action === "start") {
    // Virtual terminals are created via /agents/{id}/virtual-terminal/ensure,
    // not via the start control. Treat a stray start as a no-op ack so the
    // dashboard's reconcile path doesn't infinite-retry.
    await updateTerminalControl(control.id, { status: "completed", terminalStatus: "running" });
    return;
  }
  throw new Error(`Unsupported virtual-terminal control action: ${action}`);
}

// Which agent owns a virtual terminal, moved out of server.js in v0.5.4. It belongs here because the
// map it searches is this module's own, and the runtime allowlist travels with it: the pair is what
// distinguishes an RPC-backed virtual terminal from a real PTY, and a lookup that ignored the runtime
// would hand a PTY terminal's input to an agent that never had one.
// Bridge-side runtimes that own a synthesized virtual rpc
// terminal_session. Must stay aligned with the service-side
// VIRTUAL_RPC_COMMANDS_BY_RUNTIME in api_v2.py — when a new runtime
// is added there, add it here too so the bridge's terminal-control
// router routes synth-terminal controls (input/resize/stop) through
// handleVirtualTerminalControl instead of the legacy node-pty path
// (which marks the row stopped because no real PTY exists).
export const VIRTUAL_RPC_RUNTIMES = new Set(["pi", "hermes", "codex", "opencode"]);

export function findAgentIdForVirtualTerminal(terminalId) {
  const id = String(terminalId || "").trim();
  if (!id) return "";
  for (const [agentId, entry] of VIRTUAL_TERMINALS_BY_AGENT.entries()) {
    if (entry?.terminalId === id && VIRTUAL_RPC_RUNTIMES.has(entry?.runtime)) return agentId;
  }
  return "";
}
