// The dispatch claim pass, extracted from server.js in v0.5.4 — the largest body in the bridge.
//
// The LOOP stays in server.js: its timer, its busy flag, its shutdown gate and its catch/finally are
// untouched. Only the pass moved, byte-identical, dedented by two — including the `for` over this
// bridge's agents, which is what keeps every `continue` and `break` inside its own loop.
//
// THIS IS THE CLAIM PATH. It asks the service for work on behalf of each agent this bridge hosts, and
// then launches it. Every incident this repo records about a restart that produced no worker, or work
// stranded behind a dead one, ran through here. It was also, until now, reachable only by starting a
// bridge — server.js is imported by no test.
//
// Six dependencies stay in server.js and are injected: the claim tuning, this machine's identity, and
// the two functions that end a resident host. Everything else it needs is a module already.

import { reportAgentHeartbeat, reportTurnBusy } from "./agent-heartbeat.mjs";
import { httpCall, logTransientOrError } from "./aify-service-endpoint.mjs";
import { reregisterAgentFromState } from "./auto-registration.mjs";
import { ACTIVE_RUNS, CONSECUTIVE_FAILURES, REMOTE_AGENT_STATE, forgetRemoteAgent } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { __markControllerStart } from "./controller-activity.mjs";
import { supportedExecutionModes, wrapperChildExecutionModes } from "./dispatch-execution.js";
import { reconcileLocalActiveRun } from "./local-active-run.mjs";
import { readManagedViaWrapperRuntimes } from "./managed-wrapper-cache.mjs";
import { ensureRequiredReplyHandoff } from "./required-reply-handoff.mjs";
import { residentRuntimeBindingLost } from "./resident-binding-health.mjs";
import { processRunControls } from "./run-controls.mjs";
import { canLaunchRuntime, launchRuntimeRun, normalizeRuntime, runtimeStateWithoutSessionHandle } from "./runtimes.js";
import { normalizeSessionMode } from "./session-mode.mjs";
import { createVirtualTerminalSink, ensureVirtualTerminal } from "./virtual-terminals.mjs";

export async function runDispatchPass({
  AUTO_REREGISTER_AFTER_FAILURES,
  CLAIM_OPTS,
  CLAIM_WAIT_MS,
  MACHINE_ID,
  reportResidentRuntimeLost,
  terminateResidentHost,
}) {
  // Long-poll the dispatch claim ONLY when this bridge hosts a single agent (every
  // resident claude/codex/hermes bridge — the common case). This loop iterates its
  // agents SEQUENTIALLY, so a long idle wait per agent would serialize and delay the
  // others; a multi-agent env-bridge therefore keeps the legacy short-poll (waitMs=0).
  const soloAgentBridge = REMOTE_AGENT_STATE.size <= 1;
  for (const [agentId, state] of REMOTE_AGENT_STATE.entries()) {
    if (!state?.info) continue;

    const active = ACTIVE_RUNS.get(agentId);
    if (active) {
      const dropped = await reconcileLocalActiveRun(agentId, state, active);
      if (!dropped) {
        // Heartbeat while an active run is genuinely owned by this process.
        reportAgentHeartbeat(agentId, state, active).catch(() => {});
        await processRunControls(agentId, active).catch((error) => {
          logTransientOrError("[aify] control processing error", error);
        });
        continue;
      }
    }

    try {
      const agentRes = await httpCall("GET", `/agents/${encodeURIComponent(agentId)}`);
      const liveAgent = agentRes.agent || null;
      if (liveAgent) {
        if (
          normalizeSessionMode(liveAgent.sessionMode) === "resident" &&
          (liveAgent.launchMode || "") === "none" &&
          String(liveAgent.statusRaw || liveAgent.status || "").toLowerCase().startsWith("stopped")
        ) {
          terminateResidentHost(`Stop requested for resident agent "${agentId}"`);
          continue;
        }
        state.info = {
          ...state.info,
          ...liveAgent,
          runtimeState: liveAgent.runtimeState || state.info.runtimeState || {},
        };
        if (
          normalizeSessionMode(liveAgent.sessionMode) === "managed" &&
          liveAgent.runtimeState?.pendingResidentTakeover &&
          String(liveAgent.runtimeState.pendingResidentTakeover.bridgeId || "") === BRIDGE_INSTANCE_ID
        ) {
          // A CLI registered for this agent while a managed turn was active.
          // Keep heartbeating, but do not claim work until the backend
          // promotes ownership after that active turn reaches a terminal
          // state.
          continue;
        }
      }
    } catch (error) {
      // If the server forgot about this agent (404), auto-re-register from
      // cached state instead of silently polling a dead agentId forever.
      // This is the common "re-registration fixes it" symptom.
      if (error?.status === 404) {
        console.error(`[aify] agent "${agentId}" missing from server; auto-re-registering`);
        await reregisterAgentFromState(agentId, state);
        CONSECUTIVE_FAILURES.set(agentId, 0);
        continue;
      }
      if (error?.status === 410) {
        forgetRemoteAgent(agentId, "server marked it intentionally removed");
        continue;
      }
      // Other errors: log only, keep going.
    }

    if (await residentRuntimeBindingLost(agentId, state.info)) {
      await reportResidentRuntimeLost(agentId, state.info, "resident Codex app-server is unreachable");
      continue;
    }

    // Heartbeat after validating resident runtime reachability. This avoids
    // orphaned MCP child processes keeping a closed resident CLI "active".
    reportAgentHeartbeat(agentId, state).catch(() => {});

    const managedViaWrapperRuntimes = await readManagedViaWrapperRuntimes().catch(() => null);
    let executionModes = supportedExecutionModes(state.info, { managedViaWrapperRuntimes });
    // When this bridge IS the wrapper child for a managed agent (env
    // AIFY_MANAGED_VIA_WRAPPER=1 set by server.js when it spawned the
    // wrapper PTY), claim channel + resident regardless of the agent's
    // recorded session_mode. The wrapper IS the backing — its in-process
    // bridge owns delivery via the runtime's local backing (gateway / app-
    // server / RPC). Mirror of how claude-channel.js polls for channel +
    // resident from inside claude-aify. Operator-stated 2026-05-25:
    // "managed workers are just pseudo terminals running resident sessions
    // in them".
    //
    // EXCEPTION (managed-hermes visible-TUI, 2026-05-31): hermes' wrapper
    // child is the thin `hermes --tui` (a WS client); channel/resident
    // delivery is owned by the per-agent `hermes-managed-host.js run` loop
    // (bridgeKind="channel-sidecar"). If this hermes wrapper child also
    // claimed channel runs it would RACE that loop and route the run through
    // the leftover ChannelDelegatedController (auto-mirrored summary instead
    // of the real agent reply). wrapperChildExecutionModes excludes hermes.
    if (String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim() === "1" && String(state.info?.agentId || agentId || "") === (process.env.AIFY_AGENT_ID || "")) {
      executionModes = wrapperChildExecutionModes(executionModes, {
        runtime: normalizeRuntime(state.info?.runtime || ""),
        isWrapperChild: true,
      });
    }
    if (!executionModes.length) continue;

    // Claim all available dispatches and merge into one turn. The server
    // queues messages one by one as they arrive; the bridge batches them
    // for delivery so the agent sees everything at once. Symmetric with
    // the Claude channel bridge's batch notification.
    const batchedRuns = [];
    for (let i = 0; i < 20; i++) {
      let claim;
      try {
        claim = await httpCall("POST", "/dispatch/claim", {
          agentId,
          machineId: state.info.machineId || MACHINE_ID,
          bridgeId: BRIDGE_INSTANCE_ID,
          executionModes,
          // Long-poll only the FIRST claim of the batch (wait for work to arrive), and
          // only on a single-agent bridge (see soloAgentBridge). The remaining iterations
          // drain already-queued runs and must return at once.
          waitMs: (i === 0 && soloAgentBridge ? CLAIM_WAIT_MS : 0),
        }, (i === 0 && soloAgentBridge ? CLAIM_OPTS : {}));
        CONSECUTIVE_FAILURES.set(agentId, 0);
      } catch (error) {
        if (error?.status === 404) {
          console.error(`[aify] dispatch/claim 404 for "${agentId}"; auto-re-registering`);
          await reregisterAgentFromState(agentId, state);
          CONSECUTIVE_FAILURES.set(agentId, 0);
        } else if (error?.status === 410) {
          forgetRemoteAgent(agentId, "server marked it intentionally removed");
          break;
        } else {
          const count = (CONSECUTIVE_FAILURES.get(agentId) || 0) + 1;
          CONSECUTIVE_FAILURES.set(agentId, count);
          if (count >= AUTO_REREGISTER_AFTER_FAILURES) {
            console.error(`[aify] ${count} consecutive dispatch/claim failures for "${agentId}" (last: ${error?.message || error}); attempting auto-re-register`);
            await reregisterAgentFromState(agentId, state);
            CONSECUTIVE_FAILURES.set(agentId, 0);
          }
        }
        break;
      }
      if (!claim?.run) break;
      batchedRuns.push(claim.run);
    }
    if (!batchedRuns.length) continue;

    const run = batchedRuns[0];
    if (batchedRuns.length > 1) {
      const extras = batchedRuns.slice(1).map((r, i) =>
        `--- Message ${i + 2} of ${batchedRuns.length} ---\nFrom: ${r.from}\nSubject: ${r.subject}\n${r.body || ""}`
      ).join("\n\n");
      run.body = `${run.body || ""}\n\n${extras}`;
      run.subject = `${batchedRuns.length} messages (latest: ${run.subject})`;
    }
    const runtime = normalizeRuntime(state.info.runtime || "generic");
    if (run.requestedRuntime && normalizeRuntime(run.requestedRuntime) !== runtime) {
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
        status: run.mode === "require_start" ? "failed" : "cancelled",
        error: `Requested runtime "${run.requestedRuntime}" does not match registered runtime "${runtime}"`,
        agentStatus: "idle",
        appendEvent: `Skipped: requested runtime "${run.requestedRuntime}" does not match "${runtime}"`,
        eventType: "skipped",
      });
      continue;
    }
    if (!canLaunchRuntime(runtime)) {
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
        status: run.mode === "require_start" ? "failed" : "cancelled",
        error: `Runtime "${runtime}" does not support active dispatch`,
        agentStatus: "idle",
        appendEvent: `Skipped: runtime "${runtime}" does not support active dispatch`,
        eventType: "skipped",
      });
      continue;
    }
    const runtimeState = state.info.runtimeState || {};
    let turnBusyStarted = false;
    await reportTurnBusy(agentId, state, {
      busy: true,
      runId: run.id,
      runtime,
    }).then(() => {
      turnBusyStarted = true;
    }).catch(() => {});
    try {
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
        status: "running",
        runtime,
        agentStatus: "working",
        appendEvent: `Starting ${runtime} run for "${run.subject}"`,
        eventType: "runtime",
      });
    } catch (error) {
      if (turnBusyStarted) {
        await reportTurnBusy(agentId, state, {
          busy: false,
          runId: run.id,
          runtime,
        }).catch(() => {});
      }
      throw error;
    }

    // Pass managedViaWrapper into the controller so native RPC adapters
    // (CodexController / HermesController) can short-circuit to a delegated
    // marker when the wrapper's child bridge owns delivery. Defensive: if
    // the main bridge dispatch loop's executionMode gate (Task A4) somehow
    // misses a wrapper-backed managed run, the controller still no-ops
    // rather than competing with the wrapper.
    //
    // BUT: when THIS bridge IS the wrapper child (AIFY_MANAGED_VIA_WRAPPER=1),
    // it IS the wrapper — it should NOT short-circuit. The wrapper child
    // needs to actually deliver via the runtime's local backing (gateway /
    // app-server). Only the main bridge should short-circuit.
    const _runRuntime = normalizeRuntime(state.info?.runtime || "");
    const _isWrapperChild = String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim() === "1";
    const _isManagedViaWrapper = !_isWrapperChild && Boolean(_runRuntime && managedViaWrapperRuntimes && managedViaWrapperRuntimes.has?.(_runRuntime));
    const controller = launchRuntimeRun({
      agentId,
      agentInfo: state.info,
      run,
      runtimeState,
      managedViaWrapper: _isManagedViaWrapper,
      callbacks: {
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
      },
    });

    ACTIVE_RUNS.set(agentId, { runId: run.id, runtime, controller });
    // Plan 4 Task 13: track this controller's work promise so the
    // turn-busy heartbeat fires while it's unresolved.
    __markControllerStart(controller.promise);
    let turnBusyCleared = false;
    const clearTurnBusy = async () => {
      if (turnBusyCleared) return;
      turnBusyCleared = true;
      await reportTurnBusy(agentId, state, {
        busy: false,
        runId: run.id,
        runtime,
      }).catch(() => {});
    };

    // Audit 2026-06-28: when >1 run is claimed into a batch, only run[0] is executed — the
    // extras' bodies are merged into run[0]'s prompt (above) but the extra dispatch_runs were
    // left at `claimed`. That stranded them: false-busy "activeRun" for ~5min, then a spurious
    // [FAILED] handoff mirror to their senders (for content that WAS delivered), plus unclosed
    // reply contracts. Finalize each extra as `completed` (its text reached the agent in the
    // merged turn; the response lives in run[0]). Mirrors claude-channel.js, which already
    // marks every run in its batch delivered. Best-effort; the server reconciler is the backstop.
    let batchExtrasFinalized = false;
    const finalizeBatchedExtras = async () => {
      if (batchExtrasFinalized || batchedRuns.length <= 1) return;
      batchExtrasFinalized = true;
      for (const extra of batchedRuns.slice(1)) {
        try {
          await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(extra.id)}`, {
            status: "completed",
            agentStatus: "idle",
            summary: `Delivered in a merged batch turn with run ${run.id} (response is on that run).`,
            appendEvent: `Batch-merged into run ${run.id}; delivered in the same turn.`,
            eventType: "completed",
          });
        } catch { /* best-effort; server reconciler backstops */ }
      }
    };

    controller.promise
      .then(async (result) => {
        const summary = result.summary || "";
        const terminalStatus = result.status === "cancelled" ? "cancelled" : "completed";
        await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
          status: terminalStatus,
          summary,
          agentStatus: "idle",
          appendEvent:
            result.status === "cancelled"
              ? "Run cancelled."
              : "Run completed successfully.",
          eventType: terminalStatus,
        });
        await clearTurnBusy();
        await finalizeBatchedExtras();
        await ensureRequiredReplyHandoff(agentId, run, terminalStatus, summary);
        if (result.runtimeState) {
          state.info.runtimeState = { ...(state.info.runtimeState || {}), ...result.runtimeState };
          await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
            runtimeState: state.info.runtimeState,
          });
        }
      })
      .catch(async (error) => {
        const message = error?.message || String(error);
        // Retry the failure-PATCH up to 3 times with exponential
        // backoff. Without this, a transient connection blip during
        // the FAILURE path leaves the dispatch_run stuck `running`
        // — operator-reported "hermes stuck working" symptom. The
        // server's stale-run reconciler eventually catches it, but
        // its window is 5+ minutes (5 for managed). Retrying here
        // closes the gap for the common case.
        let lastErr = null;
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
              status: "failed",
              error: message,
              agentStatus: "idle",
              appendEvent: message,
              eventType: "failed",
            });
            await clearTurnBusy();
            await finalizeBatchedExtras();
            await ensureRequiredReplyHandoff(agentId, run, "failed", message);
            return;
          } catch (inner) {
            lastErr = inner;
            if (attempt < 2) {
              await new Promise((r) => setTimeout(r, 500 * Math.pow(2, attempt)));
            }
          }
        }
        console.error(
          `[aify] failed to report dispatch failure for ${run.id} after 3 retries; server reconciler will catch it within active_managed_run_stale_minutes:`,
          lastErr?.message || lastErr,
        );
      })
      .finally(async () => {
        await clearTurnBusy();
        ACTIVE_RUNS.delete(agentId);
      });
  }
}
