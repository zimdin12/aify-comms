// Can this bridge get a terminal AT ALL — from here, or from aify-env?
//
// THE SAME DEFECT AS THE ENVIRONMENT CAPABILITY, in a second consumer, and found by checking a claim
// rather than restating it. Two of this bridge's loops gate on `bridgeTerminalSupported()`:
//
//     ensureTerminalControlLoop  -- whether the loop is ever STARTED
//     runTerminalControlLoop     -- whether each pass runs
//
// That function answers exactly one question: did node-pty load in THIS process. It was the right
// question until v0.6 Phase 8 flipped on 2026-08-25, and since then the bridge does not use its own
// node-pty for a managed spawn at all -- it delegates and refuses rather than falling back.
//
// WHAT IT COSTS ON A HOST WHERE node-pty DOES NOT BUILD, which is an ordinary thing: it is a native
// module and needs a toolchain. Delegation makes such a host perfectly workable -- aify-env opens the
// terminals -- and the loop that claims terminal controls never starts. No spawns, no console input,
// no label reconcile, no stream re-attach. Every part of the delegated path is present and unreachable,
// with nothing saying why.
//
// It is inert on a host where node-pty DOES load, which is why nobody has hit it: this one loads, so
// the loop runs and the wrong gate never shows. That is the same shape as the capability defect --
// correct-looking because the two answers coincide on the machine it was written on.
//
// WHETHER aify-env IS UP RIGHT NOW IS NOT THIS QUESTION. That is runtime, it changes minute to minute,
// and the loop is exactly the thing that recovers when the environment comes back. A gate that stopped
// the loop while aify-env was down would make the outage permanent.

/**
 * Is a terminal reachable from this bridge, by any route?
 *
 * PURE, and both inputs are passed: the caller reads the local pty and the delegation setting, so this
 * can be driven through all four combinations without a native module or an environment variable.
 *
 * @param {object} input
 * @param {boolean} input.localTerminal      did node-pty load in this process
 * @param {boolean} input.delegationEnabled  is aify-env the spawner for this bridge
 * @returns {boolean}
 */
export function terminalsArePossible({ localTerminal = false, delegationEnabled = false } = {}) {
  return Boolean(localTerminal) || Boolean(delegationEnabled);
}


/**
 * Is this process the one that should be running the terminal control loop at all?
 *
 * THE WHOLE CONDITION, in one place, because it is asked in TWO: `ensureTerminalControlLoop`
 * decides whether the loop is ever started and `runTerminalControlLoop` decides whether each pass
 * runs. They were two hand-written copies of one predicate, which is how they would come to
 * disagree -- a loop that starts and then skips every pass is indistinguishable from one that never
 * started, and neither says so.
 *
 * @param {object} input
 * @param {boolean} input.isRemote
 * @param {boolean} input.isEnvironmentBridge
 * @param {boolean} input.localTerminal
 * @param {boolean} input.delegationEnabled
 */
export function terminalLoopEligible({
  isRemote = false, isEnvironmentBridge = false, localTerminal = false, delegationEnabled = false,
} = {}) {
  return Boolean(isRemote) && Boolean(isEnvironmentBridge)
    && terminalsArePossible({ localTerminal, delegationEnabled });
}
