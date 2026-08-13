// Telling the service that a RESIDENT session is gone.
//
// A resident session is one a human launched and owns — the mode the bridge deliberately does NOT stop,
// restart or reap. So when one ends cleanly, nothing else POSTs on its behalf: there is no supervising
// worker to time out and no spawn lifecycle to close the loop. Without this the agent keeps showing
// `available` for the full ~150s heartbeat lease, taking dispatches into a session that no longer exists.
//
// TWO GATES, AND BOTH ARE REFUSALS TO ACT ON WEAK EVIDENCE:
//   * RESIDENT ONLY. Managed teardown is handled by terminal reaping, and a managed bridge flipping its own
//     agent off `available` would fight the very lifecycle that owns it.
//   * `lifecycleOwner` MUST BE "bridge". Some harnesses spawn this MCP bridge as a short-lived per-turn
//     child whose wrapper owns the real TUI lifecycle. That child exiting is not evidence the operator's
//     session disappeared, and reporting it would take a live agent offline mid-turn.
//
// `httpCall` IS A PARAMETER, not an import, and that is what made this testable before it had an owner: the
// caller supplies the transport so a test can hand it a recorder. Preserving that seam is the reason this
// moves rather than gets rewritten — the existing test already drove it this way, it just had to reach
// through `server.js`, the bin entry point, to do so.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { defaultMachineId, normalizeRuntime } from "./runtimes.js";
import { normalizeSessionMode } from "./session-mode.mjs";

// Pure function of env and hostname — the same derivation half a dozen other bridge modules make, so it
// cannot disagree with the copy `server.js` holds.
const MACHINE_ID = defaultMachineId();

// Clean-exit resident-lost signal. When an operator cleanly closes a RESIDENT
// *-aify session (Ctrl-D / window close / SIGTERM), nothing else POSTs a
// "resident is leaving" signal, so the agent keeps showing `available` for the
// full ~150s heartbeat lease until bridge_instances.last_seen ages out. This
// long-lived bridge IS the resident MCP bridge that owns
// runtime_state.bridgeInstanceId, so it can self-correct on the way out by
// POSTing the SAME /agents/{id}/resident-lost signal the reactive paths use
// (reportResidentRuntimeLost above). It carries bridgeId — unlike the
// managed-host's bridgeId-less variant — because this bridge id matches the
// owning runtime_state and passes the server's bridge_not_current guard.
//
// STRICTLY gated to RESIDENT sessions only: managed teardown is handled by
// terminal reaping, and a managed bridge must never flip its own agent off
// `available`. Pure + dependency-injected so it's unit-testable: it does NOT
// POST unless (resident AND an agent id is bound). Best-effort: never throws.
export async function reportResidentLost({
  httpCall: call,
  agentId,
  bridgeId,
  sessionMode,
  lifecycleOwner = "bridge",
  machineId = MACHINE_ID,
  runtime = "generic",
  reason = "Resident *-aify session closed cleanly; self-correcting off 'available' (resident-lost).",
} = {}) {
  const id = String(agentId || "").trim();
  // Resident gate: managed sessions must NOT POST resident-lost.
  if (normalizeSessionMode(sessionMode) !== "resident") return false;
  // Some harnesses spawn this MCP bridge as a short-lived per-turn child. Their
  // wrapper/sidecar owns the actual resident TUI lifecycle and reports the real
  // close; a child exit is not evidence that the operator's TUI disappeared.
  if (String(lifecycleOwner || "bridge").trim().toLowerCase() !== "bridge") return false;
  if (!call || !id) return false;
  try {
    await call("POST", `/agents/${encodeURIComponent(id)}/resident-lost`, {
      bridgeId,
      machineId,
      runtime: normalizeRuntime(runtime || "generic"),
      reason,
    });
    return true;
  } catch (error) {
    console.error(
      `[aify] clean-exit resident-lost for "${id}" failed (best-effort): ${error?.message || String(error)}`,
    );
    return false;
  }
}
