// The terminal-control claim pass, extracted from server.js in v0.5.4.
//
// The LOOP stays in server.js — timer, busy flag, shutdown gate and catch/finally are untouched. Only
// the pass moved, byte-identical, dedented by two.
//
// This is the console path: it claims one terminal control at a time and starts, stops, writes to or
// reaps a terminal. Two things in it are dangerous and both are guarded here rather than downstream —
// the workspace check, which decides where a terminal may be launched, and the orphan-pid reap, which
// decides what this bridge is allowed to kill.

import { httpCall } from "./aify-service-endpoint.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { attachNotice } from "./terminal-attach-notice.js";
// `noteControlClaimFailure` is called in the catch below and was never imported: the sibling
// name was, and the failure path is the one no test runs. It would have thrown
// ReferenceError from inside an error handler — turning a recoverable claim failure into
// an unhandled one, and leaving the failure tracker blind to it.
import { noteControlClaimFailure, noteControlClaimSuccess } from "./claim-failure-tracker.mjs";
import { workspaceWithinRoots } from "./environment-identity.mjs";
import { reconcileLabels } from "./label-reconciler.mjs";
import { TERMINAL_CONTROL_POLL_MS } from "./poll-intervals.mjs";
import { terminalLoopEligible } from "./terminals-are-possible.mjs";
import { extractRuntimeSessionHandleFromArgv } from "./runtimes.js";
import { defaultGetCmdline as hermesGetCmdline } from "./hermes-daemon.js";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { readManagedViaWrapperRuntimes } from "./managed-wrapper-cache.mjs";
import { stopControlTriadAgentId } from "./reap-managed-survivors.js";
import { DEFAULT_CWD } from "./registration-inputs.mjs";
import { normalizeRuntime } from "./runtimes.js";
import { normalizeSessionMode } from "./session-mode.mjs";
import { runSingleAgentManagedTeardown } from "./single-agent-teardown.mjs";
import { stopRequestReason } from "./reap-managed-survivors.js";
import { orphanPidReapAllowed, orphanPidToKill, terminalControlFailurePatch } from "./terminal-control.js";
import { terminalChildEnv } from "./terminal-env.js";
import { TERMINAL_MANAGER, reportDeadOwnedTerminals } from "./terminal-manager.mjs";
import { findAgentIdForVirtualTerminal, handleVirtualTerminalControl, updateTerminalControl } from "./virtual-terminals.mjs";
import { IS_REMOTE } from "./aify-service-endpoint.mjs";
import { shouldSkipLoop } from "./loop-gate.mjs";
import { bridgeTerminalSupported } from "./terminal-runtime.js";

export async function runTerminalControlPass({
  CLAIM_OPTS,
  CLAIM_WAIT_MS,
  effectiveEnvironmentPayload,
  extractTerminalSessionHandle,
}) {
  // Reconcile any console PTY this bridge owns whose local pid has died but
  // whose server row is still `attached` (WS4 Task 4.2). Cheap + best-effort.
  await reportDeadOwnedTerminals();
  // AND WHAT aify-env IS DISPLAYING FOR THEM. Beside the reconcile above for the same reason: a
  // display that must be right on EVERY path cannot be written once at spawn, because identity
  // does not always exist by then. See label-reconciler.mjs. Never throws, and pushes only
  // differences, so a correct fleet costs one listing per tick and no writes.
  await reconcileLabels({
    client: TERMINAL_MANAGER.envDelegation?.isEnabled?.() ? TERMINAL_MANAGER.envDelegation.client : null,
    terminals: TERMINAL_MANAGER.terminals?.values?.() ?? [],
  });
  // AND RE-OPEN ANY STREAM WE LOST. `_settleDelegatedExit` holds a terminal open rather than
  // calling a live process dead, which is the right way round -- a stale row heals, an orphaned
  // process does not -- and it leaves the terminal DEAF until something re-subscribes. This is that
  // something. Tried on the tick rather than from a retry timer, so it needs no backoff and leaks no
  // timer.
  //
  // WHAT A RESTARTED aify-env NOW MEANS, corrected 2026-08-29. This used to say the environment
  // "comes back seconds later and re-owns the same pids", and that was the bug: handles were only
  // unique within one instance, so a returning environment answered YES to "is p1 still listed" about
  // a process belonging to somebody else, and this loop re-attached to it. A handle is instance-
  // qualified now, so a returning environment does NOT match an old one -- `processStillListed`
  // answers false, the terminal finalises, and that is correct: aify-env reaps its record's leftovers
  // at boot, so the process this terminal was watching really is gone. The recovery this loop still
  // performs is the one it was written for: an environment that never went away and a stream that
  // merely broke.
  try {
    await TERMINAL_MANAGER.reattachLostStreams();
  } catch {
    // Recovery must never be able to stop the loop that delivers work.
  }
  const environment = effectiveEnvironmentPayload();
  const claim = await httpCall("POST", "/terminals/controls/claim", {
    environmentId: environment.id,
    bridgeId: BRIDGE_INSTANCE_ID,
    waitMs: CLAIM_WAIT_MS,
  }, CLAIM_OPTS);
  noteControlClaimSuccess("terminal controls");
  const controls = claim?.controls || [];
  for (const control of controls) {
    try {
      const terminalId = String(control.terminalId || "").trim();
      if (!terminalId) throw new Error("Terminal control missing terminal id");
      const virtualAgentId = findAgentIdForVirtualTerminal(terminalId);
      if (virtualAgentId) {
        await handleVirtualTerminalControl(virtualAgentId, terminalId, control);
        continue;
      }
      if (control.action === "start") {
        const terminalRes = await httpCall("GET", `/terminals/${encodeURIComponent(terminalId)}`);
        const terminal = terminalRes?.terminal || {};
        const workspace = terminal.workspace || DEFAULT_CWD;
        if (!workspaceWithinRoots(workspace, environment.cwdRoots)) {
          throw new Error(`Terminal workspace "${workspace}" is outside this bridge's advertised roots`);
        }
        const command = terminal.command || control.body || "";
        const runtime = normalizeRuntime(terminal.runtime || "");
        // PREFER ARGV where the row carries it (v0.6 Phase 8). Reading the handle structurally means
        // finding a flag and taking the next element -- no regex, no shell unquoting, and nothing a
        // space in a path can defeat. The string reader stays for every row created before the column
        // existed and for operator-supplied commands, which have no argv by design.
        //
        // This is not a new parse. It DELETES one for the rows that carry argv, and the parse it
        // replaces has already shipped a defect: codex's and opencode's resume forms went unrecognised,
        // so the heal path could not fire and workers got a blank CODEX_THREAD_ID.
        const fromArgv = extractRuntimeSessionHandleFromArgv(runtime, terminal.argv);
        const sessionHandle = fromArgv || extractTerminalSessionHandle(runtime, command);
        let agentInfo = {};
        if (terminal.agentId) {
          try {
            const agentResp = await httpCall("GET", `/agents/${encodeURIComponent(terminal.agentId)}`);
            agentInfo = agentResp?.agent || {};
          } catch {
            agentInfo = {};
          }
        }
        let managedViaWrapper = runtime === "claude-code";
        try {
          const _wrapperRuntimes = await readManagedViaWrapperRuntimes();
          managedViaWrapper = managedViaWrapper || Boolean(_wrapperRuntimes && _wrapperRuntimes.has?.(runtime));
        } catch { /* best effort */ }
        const wrapperEnv = terminalChildEnv({ runtime, sessionHandle, terminal, workspace, terminalId, agentInfo, managedViaWrapper });
        if (managedViaWrapper && terminal.agentId) wrapperEnv.AIFY_AGENT_ID = String(terminal.agentId);
        const started = await TERMINAL_MANAGER.start({
          id: terminalId,
          command,
          // THE ROW'S ARGV, which delegation cannot spawn without. The loop already reads
          // `terminal.argv` above to find the session handle structurally, then dropped it here --
          // so `startDelegated` saw an empty argv and threw "the row carries no argv" on every
          // spawn. Phase 8 was proven against a real aify-env by a test that passes argv itself; the
          // production caller never did, which is the difference between a seam that works and one
          // that works when something else supplies the input.
          //
          // Harmless while delegation is off: the local PTY path spawns from `command` and ignores
          // it. It is the flip that needs it.
          argv: Array.isArray(terminal.argv) ? terminal.argv : [],
          cwd: workspace,
          env: wrapperEnv,
          cols: control.cols || 100,
          rows: control.rows || 28,
          runtime,
          sessionHandle,
          agentId: terminal.agentId || "",
          // FIX 6 (2026-06-03): tag the PTY's session mode so an env-bridge
          // stopAll never reaps an operator-launched resident console.
          sessionMode: normalizeSessionMode(agentInfo.sessionMode || agentInfo.session_mode),
        });
        await updateTerminalControl(control.id, {
          status: "completed",
          terminalStatus: "attached",
          output: attachNotice(started),
          // Report the PTY root pid so the server persists it
          // (terminal_sessions.process_id). Lets Dashboard Stop/Restart
          // kill-by-pid if THIS bridge later dies and orphans the PTY.
          processId: started.pid != null ? String(started.pid) : "",
        });
      } else if (control.action === "input") {
        // Raw passthrough: callers own newline semantics. Prompt answers are
        // handled separately by TerminalProcessManager's cursor-verified rules.
        const rawBody = String(control.body || "");
        TERMINAL_MANAGER.input(terminalId, rawBody);
        await updateTerminalControl(control.id, { status: "completed", terminalStatus: "attached" });
      } else if (control.action === "resize") {
        TERMINAL_MANAGER.resize(terminalId, control.cols || 0, control.rows || 0);
        await updateTerminalControl(control.id, { status: "completed", terminalStatus: "attached" });
      } else if (control.action === "stop") {
        const stopResult = await TERMINAL_MANAGER.stop(terminalId, "terminal stop control");
        // Kill-by-pid fallback (2026-06-02): the in-memory stop path is a
        // no-op when THIS bridge never owned the PTY (Map miss) — the owning
        // bridge restarted/died and orphaned a still-live console. The stop
        // control carries the persisted PTY root pid (server-scoped to this
        // bridge's environment, so machine-local). Reap the orphan by pid so
        // Stop/Restart isn't silently dropped. Owned-in-memory path unchanged.
        const orphanPid = orphanPidToKill(stopResult, control);
        if (orphanPid) {
          // Identity guard (2026-07-10 bughunt HIGH): this pid is the PRIOR
          // spawn's persisted PTY root and the fallback fires only on the
          // owning-bridge-gone path — the window where Windows may have RECYCLED
          // it onto a live sibling agent's worker. Refuse only when the cmdline
          // positively names a DIFFERENT agent; fail-open otherwise so a real
          // orphan Stop is never dropped. terminateProcessTree's pidIsSelfProtected
          // still blocks the bridge/shell/init separately.
          if (orphanPidReapAllowed(orphanPid, control, { getCmdline: hermesGetCmdline })) {
            TERMINAL_MANAGER.killByPid(orphanPid);
          } else {
            console.error(
              `[aify] orphan Stop: refused kill-by-pid ${orphanPid} for terminal ${terminalId} — ` +
              `its command line identifies a different agent (recycled pid?); leaking rather than cross-killing`,
            );
          }
        }
        // fix/hermes-leak P2: a STOP/REMOVE of a MANAGED HERMES agent must tear
        // down the WHOLE triad (detached gateway host + delivery loop + daemon),
        // not just the PTY above — otherwise Stop/Remove leaves the gateway/loop/
        // daemon orphaned (the big latent leak). AGENT-SCOPED: stopControlTriadAgentId
        // returns the agent id ONLY for a managed-hermes stop (sessionMode=managed
        // or the REMOVE body sentinel); a resident hermes / claude / another runtime
        // returns null and is never touched.
        const triadAgentId = stopControlTriadAgentId(control);
        if (triadAgentId && IS_ENVIRONMENT_BRIDGE) {
          // THE REASON COMES FROM THE CONTROL, not from a guess about who is usually pressing
          // buttons. This said "dashboard stop/remove" unconditionally; measured on the live
          // database, 0 of 13 stop controls came from the dashboard and all 13 were the agent
          // stopping itself. The log was the operator's only attribution and it named the wrong
          // actor every time.
          await runSingleAgentManagedTeardown(triadAgentId, stopRequestReason(control));
        }
        await updateTerminalControl(control.id, { status: "completed", terminalStatus: "stopped" });
      } else {
        throw new Error(`Unsupported terminal control action: ${control.action}`);
      }
    } catch (error) {
      await updateTerminalControl(
        control.id,
        terminalControlFailurePatch(control.action, error),
      ).catch(() => {});
    }
  }
}

// THE LOOP SHELL LIVES HERE NOW, with the busy flag it owns. Its gate, its try/catch/finally and
// its body are byte-identical to what left server.js; the only change is that `shutdownStarted`
// arrives as a parameter, because the flag it reads is set by the shutdown chain server.js owns
// and must be read AFRESH on every tick — a value captured at import would be permanently false.
//
// The TIMER stays in server.js: `ensure*Loop` arms it and `cleanupOnExit` clears it, so it has two
// readers and one of them is the shutdown chain.
let terminalControlBusy = false;
export async function runTerminalControlLoop({
  CLAIM_OPTS,
  CLAIM_WAIT_MS,
  effectiveEnvironmentPayload,
  extractTerminalSessionHandle,
  shutdownStarted,
}) {
  // TERMINALS FROM ANYWHERE, not just from here. This read `bridgeTerminalSupported()` -- did
  // node-pty load in THIS process -- which since v0.6 Phase 8 is not what decides whether this
  // bridge can open a terminal. See terminals-are-possible.mjs for what it costs on a host where
  // the native module does not build.
  const eligible = terminalLoopEligible({
    isRemote: IS_REMOTE, isEnvironmentBridge: IS_ENVIRONMENT_BRIDGE,
    localTerminal: bridgeTerminalSupported(),
    delegationEnabled: TERMINAL_MANAGER.envDelegation?.isEnabled?.() === true,
  });
  if (shouldSkipLoop({ eligible, alreadyActive: terminalControlBusy, shuttingDown: shutdownStarted })) return;
  terminalControlBusy = true;
  try {
    await runTerminalControlPass({
      CLAIM_OPTS,
      CLAIM_WAIT_MS,
      effectiveEnvironmentPayload,
      extractTerminalSessionHandle,
    });
  } catch (error) {
    if (error?.status !== 404) {
      noteControlClaimFailure("terminal controls", error);
    }
  } finally {
    terminalControlBusy = false;
  }
}

//: The timer that keeps the loop running, owned here since 2026-08-29.
//:
//: v0.5.4 moved the loop's body out of server.js and left the timer behind, with a note saying so.
//: That was right at the time -- the module had no opinion about whether the loop should run. It
//: has one now (`terminalLoopEligible`), and the split meant server.js asked that question and this
//: file asked it again, in two hand-written copies. A loop that starts and then skips every pass
//: looks exactly like one that never started, and neither says why.
let terminalControlTimer = null;

/**
 * Start the terminal control loop if this process should be running one.
 *
 * Idempotent: an already-running loop is left alone, which is what `alreadyActive` is for.
 */
export function ensureTerminalControlLoop(deps) {
  const eligible = terminalLoopEligible({
    isRemote: IS_REMOTE,
    isEnvironmentBridge: IS_ENVIRONMENT_BRIDGE,
    localTerminal: bridgeTerminalSupported(),
    delegationEnabled: TERMINAL_MANAGER.envDelegation?.isEnabled?.() === true,
  });
  if (shouldSkipLoop({
    eligible,
    alreadyActive: Boolean(terminalControlTimer),
    shuttingDown: deps.shutdownStarted,
  })) return;
  // ONE CALL, USED TWICE. It was written out twice -- once now, once per tick -- and two copies of
  // one argument list is how they start disagreeing about what the loop is passed.
  const tick = () => runTerminalControlLoop(deps)
    .catch((error) => console.error("[aify] terminal control loop error:", error));
  tick();
  terminalControlTimer = setInterval(tick, TERMINAL_CONTROL_POLL_MS);
}

/** Stop it. Safe to call when it was never started -- shutdown runs on paths that never had one. */
export function stopTerminalControlLoop() {
  if (!terminalControlTimer) return;
  clearInterval(terminalControlTimer);
  terminalControlTimer = null;
}
