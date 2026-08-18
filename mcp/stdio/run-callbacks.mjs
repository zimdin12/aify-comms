// The callbacks a launched runtime fires back at the bridge. Extracted from `dispatch-loop.mjs`
// (v0.6 Phase 1) with the bodies byte-identical, dedented by four.
//
// WHY THIS MOVED. A V8-coverage census over `mcp/stdio` reports 122 named functions that no test has
// ever called, and ten of them were in `dispatch-loop.mjs` — these callbacks. They were unreachable by
// CONSTRUCTION, not by neglect: they are created inside `runDispatchPass` and handed to
// `launchRuntimeRun`, so reaching them meant launching a real runtime. `runDispatchPass` already had a
// seam and three tests; the tests could call the pass and still never fire a single callback.
//
// WHAT THEY DO, which is why leaving them untested was the wrong trade. `onTurnStart`/`onTurnEnd` are
// what set and clear `turn_busy` — the signal the whole status engine derives `working` from.
// `onSessionHandleChange` is what discards a poisoned resume handle, and getting it wrong strands an
// agent that can never resume. `onEvent` carries runtime output to the console. Every one of them is
// best-effort by design (a callback that throws must not kill the delivery loop), which means a broken
// one fails SILENTLY — the worst possible combination with "no test has ever called it".
//
// A FACTORY, not a class: the callbacks close over one run's identity and state, and the call site
// builds them once per launch. Taking those five as parameters is the whole seam — everything else they
// need is a module import, which this file now owns directly.

import { httpCall } from "./aify-service-endpoint.mjs";
import { reportTurnBusy } from "./agent-heartbeat.mjs";
import { reregisterAgentFromState } from "./auto-registration.mjs";
import { normalizeRuntime, runtimeStateWithoutSessionHandle } from "./runtimes.js";
import { createVirtualTerminalSink, ensureVirtualTerminal } from "./virtual-terminals.mjs";

/**
 * Build the callback bundle for one launched run.
 *
 * @param {object}  args
 * @param {string}  args.agentId       the agent this run belongs to
 * @param {object}  args.state         the bridge's mutable state for that agent (mutated in place)
 * @param {object}  args.run           the dispatch run being launched
 * @param {string}  args.runtime       the normalized runtime name
 * @param {object}  args.runtimeState  the runtime state passed to the launch
 * @returns {object} the `callbacks` object `launchRuntimeRun` expects
 */
export function buildRunCallbacks({ agentId, state, run, runtime, runtimeState }) {
  return {
    // Plan 4 Task 13 (2026-05-25): controllers fire this when their
    // initial handshake completes (WS app-server initialize, gateway
    // connect, pi agent_ready, etc.). Maps to PATCH /agents/{id}/ready
    // so operators can see "ready" as a distinct state from "online".
    onReady: () => {
      httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/ready`, {
        ready: true,
        requestedBy: "controller-handshake",
      }).catch(() => { /* best-effort */ });
    },
    onEvent: async (eventType, text) => {
      try {
        await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
          appendEvent: text,
          eventType,
        });
      } catch {
        // best effort
      }
    },
    onRuntimeState: async (nextState) => {
      try {
        state.info.runtimeState = { ...(state.info.runtimeState || {}), ...nextState };
        await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
          runtimeState: state.info.runtimeState,
        });
      } catch {
        // best effort
      }
    },
    onRefs: async (refs) => {
      try {
        const body = {};
        if (refs.threadId) body.externalThreadId = refs.threadId;
        if (refs.turnId) body.externalTurnId = refs.turnId;
        if (Object.keys(body).length > 0) {
          await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, body);
        }
      } catch {
        // best effort
      }
    },
    // TERTIARY pure-event (2026-06-19): wire codex's native app-server turn events to the
    // turn-state poster — turn/started → working, turn/completed → cleared — so managed
    // codex status is event-EXACT instead of leaning on the 5s rollout-tail poll. Both are
    // idempotent (reportTurnBusy is ownership-guarded) and additive to the existing
    // dispatch-boundary + rollout-detector signals, so they only sharpen, never conflict.
    onTurnStart: async () => {
      try { await reportTurnBusy(agentId, state, { busy: true, runId: run.id, runtime: "codex" }); } catch { /* best-effort */ }
    },
    onTurnEnd: async () => {
      try { await reportTurnBusy(agentId, state, { busy: false, runId: run.id, runtime: "codex" }); } catch { /* best-effort */ }
    },
    // Fired when the runtime controller had to discard an unloadable
    // thread/session and start a fresh one. Non-empty handles are
    // persisted through re-registration; explicit clears use the
    // lightweight session-handle endpoint so a poisoned handle is gone
    // even if the fresh run fails before discovering its replacement.
    onSessionHandleChange: async (newHandle, meta = {}) => {
      const nextHandle = String(newHandle || "").trim();
      const metaLabel = meta?.reason ? ` (reason: ${meta.reason}, previous: ${meta.previous || ""})` : "";
      try {
        if (!nextHandle && meta?.reason) {
          state.info.sessionHandle = "";
          state.info.runtimeState = runtimeStateWithoutSessionHandle(
            state.info.runtime || "",
            state.info.runtimeState || {},
          );
          await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/session-handle`, {
            sessionHandle: "",
            requestedBy: "pi-rpc-heal",
          });
          await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
            runtimeState: state.info.runtimeState,
          });
          console.error(`[aify] cleared stale sessionHandle for "${agentId}"${metaLabel}`);
          return;
        }
        if (!nextHandle) return;
        state.info.sessionHandle = nextHandle;
        await reregisterAgentFromState(agentId, state);
        console.error(`[aify] healed sessionHandle for "${agentId}" → ${nextHandle}${metaLabel}`);
      } catch (error) {
        console.error(`[aify] failed to persist healed sessionHandle for "${agentId}": ${error?.message || error}`);
      }
    },
    // Synthesized terminal_session row backing the bridge's native
    // RPC controller. Pi (Phase 2): persistent omp --mode rpc child
    // streams its event feed through this sink. Hermes: per-dispatch
    // `hermes chat -q -Q` controller pushes request/response frames.
    // PiController (managed mode only post-Plan-2 flip) wires this
    // sink via session.attachTerminalSink. Other runtimes return null
    // and stay on their existing visibility surface.
    terminalSinkProvider: async ({ agentId: provId, agentInfo }) => {
      const rt = normalizeRuntime(agentInfo?.runtime || "");
      // Phases 2 + 7 + 5/6: pi (persistent), hermes (per-dispatch
      // with synth feed), codex (per-dispatch with synth feed),
      // opencode (per-dispatch with synth feed). Codex/opencode
      // still use per-dispatch controllers; the synth terminal
      // gives operators visible Console activity even before the
      // full Phase 5/6 persistent-worker pool refactor.
      if (rt !== "pi" && rt !== "hermes" && rt !== "codex" && rt !== "opencode") return null;
      try {
        const entry = await ensureVirtualTerminal(provId, agentInfo, rt);
        if (!entry?.terminalId) return null;
        return createVirtualTerminalSink(entry.terminalId);
      } catch (error) {
        console.error(`[aify] virtual-terminal/ensure failed for "${provId}" (runtime=${rt}): ${error?.message || error}`);
        return null;
      }
    },
  };
}
