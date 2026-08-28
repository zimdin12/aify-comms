// What a delegated terminal does when its output stream ends, and how it gets one back.
//
// FUNCTIONS TAKING THE MANAGER, not methods on it. Two reasons, and the second is the one that
// matters: `terminal-runtime.js` was 29 lines over the 1000-line gate with these inside it, and
// the gate is right -- this is delegated-environment POLICY, while that file is terminal-process
// mechanics. The first is that a function taking its collaborator can be called directly by a test,
// where a method needs the whole class stood up.
//
// The pure decision lives one module further out, in `delegated-exit.mjs`, with the incident that
// produced it written down there.

import { delegatedExitVerdict, processStillListed } from "./delegated-exit.mjs";


/**
 * Decide whether a delegated stream ending was an EXIT, and finalise only if it was.
 *
 * An observed `event: exit` frame is finalised immediately -- there is nothing to verify, aify-env
 * watched it happen. Anything else ASKS the environment whether it still owns the process, and
 * holds the terminal open unless the answer is a clear no.
 *
 * HOLDING IS THE SAFE DIRECTION and it is not free: a held terminal has no output stream, which
 * `terminal-runtime.js` calls the worst shape available. `reattachLostStreams` below is what pays
 * that cost back. The asymmetry decides it anyway -- a stale
 * `attached` row is what `terminal_consistency.py`, `terminal_runs.py` and `managed_workers.py`
 * exist to heal, and NOTHING collects a process the control plane has already called stopped.
 */
export async function settleDelegatedExit(manager, id, state, { code, signal, meta = {} } = {}) {
  const verdict = delegatedExitVerdict({
    observedExitFrame: meta.observedExitFrame === true,
    stillListed: meta.observedExitFrame === true
      ? null
      : await processStillListed(manager.envDelegation?.client ?? null, state.envProcessId),
  });
  if (verdict.finalise) {
    await manager._handleExit(id, state, { code, signal });
    return verdict;
  }
  // SAID OUT LOUD, into the console the operator is looking at. A terminal that silently stops
  // producing output and never ends is the one thing worse than either wrong answer.
  try {
    await manager.onOutput(
      id,
      `${String.fromCharCode(10)}[aify-comms] lost the output stream for this terminal: `
      + `${verdict.reason}. The terminal is left attached rather than reported as stopped, `
      + `because a process reported stopped is one nothing will ever collect.${String.fromCharCode(10)}`,
    );
  } catch {
    // A console that cannot be written to does not get to change the decision.
  }
  state.streamLost = verdict.kind;
  return verdict;
}

/**
 * Re-open the stream for any terminal we held because we lost sight of it.
 *
 * THE OTHER HALF OF `settleDelegatedExit`, and without it that fix trades one defect for
 * another. Holding a terminal open rather than calling a live process dead is the right choice --
 * a stale row heals, an orphaned process does not -- but a held terminal has NO output stream,
 * which `terminal-runtime.js` calls the worst shape available: attached, registered, and deaf.
 *
 * STATE-BASED, ON A TICK, rather than a retry timer armed at the moment of loss. The repo's own
 * rule, from an unrelated incident: cleanup that must hold for ALL paths keys on the STATE. The
 * environment usually comes back seconds later -- the operator restarts aify-env and it re-owns
 * the same pids -- and a tick that simply tries again needs no backoff, no timer to leak, and no
 * decision about when to give up.
 *
 * @returns {Promise<{reattached: string[], stillLost: string[], finalised: string[]}>}
 */
export async function reattachLostStreams(manager) {
  const reattached = [];
  const stillLost = [];
  const finalised = [];
  if (!manager.envDelegation?.isEnabled?.()) return { reattached, stillLost, finalised };
  for (const [id, state] of manager.terminals) {
    if (!state?.streamLost || state.finalized) continue;
    const envProcessId = String(state.envProcessId ?? "").trim();
    if (!envProcessId) continue;
    let unsubscribe = null;
    try {
      unsubscribe = await manager._attachDelegatedStream(id, state, envProcessId);
    } catch {
      // Same answer as a refused subscription: still lost, try again next tick.
    }
    if (!unsubscribe) {
      // COULD NOT RE-OPEN IT. That is two different situations and only one of them is worth
      // waiting on, so ask which: an environment that is down will be back, a process that DIED
      // while we were blind never will.
      //
      // WITHOUT THIS THE HOLD IS PERMANENT, and I asserted otherwise two commits ago -- "a stale
      // `attached` row is what the reconcilers exist to heal". Checked instead of assumed:
      // `listOwnedSessions` EXCLUDES delegated terminals by design, correctly, because their pid
      // is not on this host. So nothing reports a held delegated terminal dead, and a process that
      // ended during an aify-env outage would have been held `attached` for ever.
      const stillThere = await processStillListed(manager.envDelegation?.client ?? null, envProcessId);
      if (stillThere === false) {
        state.streamLost = null;
        await manager._handleExit(id, state, { code: null, signal: "" });
        finalised.push(id);
        continue;
      }
      stillLost.push(id);
      continue;
    }
    state.unsubscribeOutput = unsubscribe;
    state.streamLost = null;
    reattached.push(id);
    try {
      await manager.onOutput(
        id,
        `${String.fromCharCode(10)}[aify-comms] output stream re-attached; this terminal is live `
        + `again.${String.fromCharCode(10)}`,
      );
    } catch {
      // A console that cannot be written to does not get to undo the re-attach.
    }
  }
  return { reattached, stillLost, finalised };
}
