// Tearing down ONE named managed agent -- the triad a console PTY stop leaves behind.
//
// Extracted from server.js in v0.5.4. Deliberately separate from the SWEEPS
// (`runManagedTeardownForBridge`, `runBootSurvivorSweep`), which stay in server.js pending a scope
// decision: those DECIDE which agents die, this one is TOLD which one. That is the whole difference in
// blast radius, and it is why this one could move on its own.
//
// It reaps the DETACHED triad (gateway host / delivery loop / daemon). The console PTY itself is killed
// by TERMINAL_MANAGER.stop on the stop control, which is why `consolePtyPids` is empty here.
//
// Best-effort by construction: the marker cleanup and the whole body are wrapped, because a teardown
// that threw would abort the stop control that called it and leave the agent half-stopped.

import os from "os";

import { clearGatewayMarkers as hermesClearGatewayMarkers } from "./hermes-endpoint.js";
import { cwdRootsForEnvironment } from "./environment-identity.mjs";
import { defaultKillByPort, stopDaemon } from "./hermes-daemon.js";
import {
  runManagedTeardown,
  defaultListProcesses as listManagedProcesses,
  defaultReadMarkers as readManagedMarkers,
  defaultKillTree as killManagedTree,
} from "./reap-managed-survivors.js";

// Tear down ONE managed-hermes agent's triad (gateway host, delivery loop,
// daemon, console PTY) — the agent-scoped reaper for a Dashboard STOP/REMOVE of
// a managed hermes agent (fix/hermes-leak P2). Scoped strictly to the single
// agentId passed in: enumeration keys on the delivery-loop cmdline + the agent's
// own port/daemon-pid markers, so another agent's or a resident operator's
// processes can NEVER be enumerated. async: awaits the port-kill/stopDaemon
// promises. Best-effort; never throws.
export async function runSingleAgentManagedTeardown(agentId, reason = "agent stop") {
  const id = String(agentId || "").trim();
  if (!id) return;
  try {
    const result = runManagedTeardown({
      ownedAgentIds: [id],
      cwdRoots: cwdRootsForEnvironment(),
      listProcesses: listManagedProcesses,
      readMarkers: () => readManagedMarkers(os.tmpdir()),
      // The console PTY is killed by the in-memory TERMINAL_MANAGER.stop on the
      // stop control itself; here we reap the DETACHED triad (gateway/loop/daemon)
      // that the PTY stop leaves behind.
      consolePtyPids: [],
      killByPort: defaultKillByPort,
      stopDaemon,
      killTree: killManagedTree,
    });
    if (Array.isArray(result?.pending) && result.pending.length) {
      await Promise.allSettled(result.pending);
    }
    const n =
      (result?.killed?.gatewayHosts?.length || 0) +
      (result?.killed?.deliveryLoops?.length || 0) +
      (result?.killed?.daemons?.length || 0);
    if (n) {
      console.error(`[aify] single-agent managed teardown (${reason}): reaped ${n} survivor(s) for agent ${id}`);
    }
    // Marker hygiene (P4): a STOP/REMOVE is the lifecycle end of this managed
    // session; clear its gateway port/key markers so they don't linger.
    try { hermesClearGatewayMarkers(id, os.tmpdir()); } catch { /* best effort */ }
    if (result?.errors?.length) {
      console.error(`[aify] single-agent managed teardown (${reason}) had ${result.errors.length} error(s):`, JSON.stringify(result.errors));
    }
  } catch (error) {
    console.error(`[aify] single-agent managed teardown (${reason}) failed:`, error?.message || error);
  }
}
