// What this bridge tells the hub about its environment, decided in one place.
//
// THREE FACTS TRAVEL TOGETHER and they come from different tiers, which is exactly why they kept
// disagreeing. Whether a terminal can be opened is aify-env's answer since v0.6 Phase 8; the reason
// behind it is what an operator reads when the answer is no; and how many processes aify-env is
// running that this bridge does not know about is the number that would have shown the orphan the
// operator found by hand. All three come off the SAME `/health` response, so none of them costs a
// call the others did not already pay for.
//
// A FUNCTION TAKING ITS INPUTS, not a method reading module state: `server.js` was 10 lines over the
// 1000-line gate with this inline, and the gate was right about the reason as well as the number --
// this is a decision, and that file is wiring. It is also the difference between a rule a test can
// drive through every combination and one that needs a running bridge.

import { environmentHeartbeatPayload } from "./environment-identity.mjs";
import { processesThisBridgeDoesNotKnow } from "./env-process-reconciliation.mjs";
import { terminalCapability } from "./terminal-capability.mjs";

/**
 * @param {object} input
 * @param {boolean} input.delegationEnabled  is aify-env the spawner for this bridge
 * @param {boolean|null} input.envHealthy    aify-env's terminal answer; null = it did not answer
 * @param {object[]|null} input.envProcesses aify-env's process list; null = could not ask
 * @param {boolean} input.localTerminal      did node-pty load in this process
 * @param {Iterable<object>} input.ownedTerminals  the terminals this bridge holds
 * @returns {{terminal: boolean, reason: string, unknownProcesses: number|null}}
 */
export function advertisedEnvironmentState({
  delegationEnabled = false,
  envHealthy = null,
  envProcesses = null,
  localTerminal = false,
  ownedTerminals = [],
} = {}) {
  const capability = terminalCapability({ delegationEnabled, envHealthy, localTerminal });
  return {
    terminal: capability.terminal,
    reason: capability.reason,
    // NULL WHEN WE COULD NOT ASK, never 0. A bridge that never reached aify-env accounts for nothing,
    // and reporting zero would say the opposite with confidence -- the false green this whole family
    // of checks exists to avoid.
    unknownProcesses: envProcesses === null
      ? null
      : processesThisBridgeDoesNotKnow(envProcesses, ownedTerminals).length,
  };
}


/**
 * The heartbeat this bridge would send now, decision and payload together.
 *
 * TAKES THE MANAGER, like `delegated-stream.mjs`, because the alternative is server.js spelling out
 * five arguments and the shape of two of them -- wiring that then has to be kept in step with this
 * module by hand. It also put server.js over the 1000-line gate, which is the second time today
 * that gate has been right about WHERE something belongs and not merely about its size.
 *
 * @param {object} input
 * @param {{envDelegation?: object, terminals?: Map}} input.terminalManager
 * @param {boolean|null} input.envHealthy    aify-env's terminal answer; null = it did not answer
 * @param {object[]|null} input.envProcesses aify-env's process list; null = could not ask
 * @param {boolean} input.localTerminal      did node-pty load in this process
 * @param {boolean} input.envAdvertising     is aify-env describing this host; false unless it says so
 */
export function buildEnvironmentPayload({
  terminalManager, envHealthy = null, envProcesses = null, localTerminal = false,
  envAdvertising = false,
} = {}) {
  const state = advertisedEnvironmentState({
    delegationEnabled: terminalManager?.envDelegation?.isEnabled?.() === true,
    envHealthy,
    envProcesses,
    localTerminal,
    ownedTerminals: terminalManager?.terminals?.values?.() ?? [],
  });
  return environmentHeartbeatPayload({
    terminalSupported: state.terminal,
    terminalReason: state.reason,
    unknownProcesses: state.unknownProcesses,
    // Standing down needs a POSITIVE answer from the tier taking the job over. Everything else --
    // no answer, an old daemon, a false -- leaves this bridge describing the host.
    hostDescribedByEnvironment: envAdvertising === true,
  });
}
