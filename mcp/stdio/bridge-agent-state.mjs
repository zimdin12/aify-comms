// What this bridge remembers about each agent it serves.
//
// Three Maps and the one operation that keeps them consistent. v0.5.4 layer 0 of the server.js
// decomposition; they had 41 references between them and no owner, which made each of them look like a
// dependency of whichever tool group was being measured.
//
// THEY ARE ONE INVARIANT, NOT THREE NAMES THAT HAPPEN TO BE ADJACENT — and that is the whole argument for
// a single owner. `comms_clear` resets all three together, and `forgetRemoteAgent` deletes one agent from
// all three together. Either operation applied to a subset would leave the bridge believing in a run
// whose agent it has forgotten, or backing off for an agent it no longer serves. So the state and the two
// shapes of "forget" belong in one place, where a future edit that adds a fourth Map has an obvious
// question to answer: does it join the reset?
//
// `LOCAL_RUNTIME_STATE` deliberately did NOT come along, though it is also a per-agent `Map` declared two
// lines from these. It is local-mode auto-start state, it has one writer, and it is NOT part of either
// reset. Type is not subject; the reset set is.
//
// A STATE OWNER, NOT A SERVICE LAYER. Around thirty functions read these Maps and every one of them
// stayed where it was. This module exports the state and the invariant operation over it, and nothing
// else — adding the readers would recreate the monolith at a new address.
//
// WHY EXPORTING A MUTABLE Map IS SAFE HERE. ESM module state is a per-process singleton and an imported
// binding refers to the same object, so `ACTIVE_RUNS.set(...)` in `server.js` mutates the one instance
// exactly as it did when the declaration lived there. That holds because these are `const` bindings whose
// CONTENTS mutate — measured, zero reassignments. A reassignment could not work at all (an importer
// cannot rebind an imported name), and state captured in a closure rather than declared at module scope
// could not move; both were checked before this slice.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

export const REMOTE_AGENT_STATE = new Map();
export const ACTIVE_RUNS = new Map();
export const CONSECUTIVE_FAILURES = new Map();

// The per-agent form of the reset: stop tracking one agent everywhere at once.
//
// The `console.error` stays inside rather than becoming an injected logger. It is part of the operation —
// an operator reading bridge stderr during an incident wants to see the agent it stopped tracking and
// why — and a deps object for one log line would be more machinery than the function contains.
export function forgetRemoteAgent(agentId, reason = "") {
  REMOTE_AGENT_STATE.delete(agentId);
  ACTIVE_RUNS.delete(agentId);
  CONSECUTIVE_FAILURES.delete(agentId);
  if (reason) {
    console.error(`[aify] stopped tracking "${agentId}": ${reason}`);
  }
}

// ---------------------------------------------------------------------------------------------------
// Interrupting everything in flight, appended in a later v0.5.4 slice.
//
// A second operation over the same state, which is what this module is for: it iterates `ACTIVE_RUNS` and
// nothing else, so keeping it beside the Map spares every caller from importing the Map to do it.
//
// BEST EFFORT BY CONSTRUCTION — `Promise.allSettled`, and each interrupt wrapped. It runs while the bridge
// is going down, so one controller that throws or hangs must not stop the others being told. A `Promise.all`
// here would abandon the remaining runs on the first rejection, which is the opposite of what shutdown
// wants.

export async function interruptActiveRuns(reason = "Bridge shutdown") {
  const active = Array.from(ACTIVE_RUNS.values());
  if (!active.length) return;
  await Promise.allSettled(active.map(async (run) => {
    try {
      await run?.controller?.interrupt?.(reason);
    } catch {
      // Best effort. The process is going down.
    }
  }));
}
