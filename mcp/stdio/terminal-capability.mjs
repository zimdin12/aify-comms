// Whether this environment can actually open a terminal — asked of the tier that would open it.
//
// THE DEFECT, found 2026-08-28 by the operator asking a question I could not answer: "why do i see
// agents available if their env is down. shouldnt they be offline?"
//
// The status engine is right. `derive()` reads `if (i.mode == "managed" && !i.env_reachable) return
// "offline"`, and `_managed_env_reachable` gates on the environment row's effective status. What was
// wrong is what feeds it. The environment advertises `terminal` / `pty` / `terminalRuntimes` from
// `bridgeTerminalSupported()`, which answers ONE question: did node-pty load in the BRIDGE.
//
// That was the right question until v0.6 Phase 8 flipped on 2026-08-25. Delegation makes aify-env
// REQUIRED -- the bridge refuses to host a spawn itself rather than silently falling back, because two
// spawners on one host is the collision the environment tier exists to end. So since the flip, the
// bridge's own node-pty has had nothing to do with whether a managed agent can start, and the
// environment has been advertising a capability measured on a tier that no longer provides it.
//
// WHAT THAT COSTS, live on the operator's host the day this was written: aify-env down, 20 managed
// agents reading `available`, and the environment row still `online` because the BRIDGE is the thing
// heartbeating it. `available` is not a description, it is a PROMISE -- status_engine.py says so in
// its own words, "`available` PROMISES cold-start on the next send... saying `available` is a false
// promise that sends the operator hunting a delivery bug". Every one of those sends would have failed.
//
// UNKNOWN IS NOT YES. If delegation is on and aify-env did not answer, this reports NO terminal. That
// is this repo's own rule -- "a check that could not gather evidence must NOT report ok" -- and the
// direction of the error matters: advertising a terminal we cannot open sends work into a hole,
// while withholding one we could open costs a queued send that the next heartbeat releases.

/**
 * @typedef {{terminal: boolean, reason: string}} TerminalCapability
 *   `reason` is always populated, including when the answer is yes: an operator reading a row that
 *   says NO needs to know which tier said so, and a field only present on failure is one nobody
 *   builds a habit of reading.
 */

/**
 * Can this environment open a terminal, and who says so?
 *
 * PURE. The probe is the caller's job, so this can be driven through every combination without a
 * network, and so the decision does not quietly become "whatever the last request did".
 *
 * @param {object} input
 * @param {boolean} input.delegationEnabled  is aify-env the spawner for this bridge
 * @param {boolean|null} input.envHealthy    aify-env's answer: true, false, or null for "did not answer"
 * @param {boolean} input.localTerminal      did node-pty load in this process
 * @returns {TerminalCapability}
 */
export function terminalCapability({ delegationEnabled = false, envHealthy = null, localTerminal = false } = {}) {
  if (!delegationEnabled) {
    // The pre-Phase-8 answer, and still the right one when this bridge is the spawner.
    return localTerminal
      ? { terminal: true, reason: "this bridge hosts terminals and node-pty loaded" }
      : { terminal: false, reason: "this bridge hosts terminals and node-pty did not load" };
  }
  if (envHealthy === true) {
    return { terminal: true, reason: "aify-env answered and hosts terminals for this environment" };
  }
  if (envHealthy === false) {
    return { terminal: false, reason: "aify-env answered but reports no terminal support" };
  }
  // NULL. Not "assume the last known good", and not "fall back to the local pty" -- delegation means
  // the local pty is never used, so consulting it here would advertise a terminal that nothing would
  // ever open.
  return { terminal: false, reason: "spawns are delegated and aify-env did not answer" };
}

/**
 * Read aify-env's health as the tri-state above: true, false, or null for "did not answer".
 *
 * The three cases are genuinely different and collapsing any two of them is how this defect happened
 * in the first place. `terminals.available` is the field aify-env's own /health reports, and a body
 * that does not carry it is not a body saying no -- it is an older environment that cannot answer.
 *
 * @param {{terminals?: {available?: boolean}}|null|undefined} health
 * @returns {boolean|null}
 */
export function envTerminalHealth(health) {
  if (!health || typeof health !== "object") return null;
  const available = health.terminals?.available;
  return typeof available === "boolean" ? available : null;
}


/**
 * Ask an aify-env client whether it can open terminals, as the tri-state above.
 *
 * IT LIVES HERE RATHER THAN AT THE CALL SITE because a predicate proven in isolation leaves the CALL
 * to it unproven, and this repo has already paid for that once: `doctor.js`'s service check had a
 * verdict everybody tested and an early return nobody did. The first version of this reader was
 * written inline in server.js and read `result.body` -- a field `EnvClient` does not have; it returns
 * `{ ok, handle }`. Parsing succeeded, the bridge would have reported UNKNOWN for ever, and every
 * managed agent would have gone dark. Nothing could have tested it where it was.
 *
 * AND IT RETURNS THE PROCESS LIST WITH IT, because aify-env's `/health` already carries one. That
 * is not a performance argument -- a second `GET /processes` was measured at 0.3 ms median on
 * loopback, twelve runs, which is nothing. It is that the bridge can now answer "is aify-env
 * running something I do not know about" on every heartbeat WITHOUT adding a call, and a signal
 * that costs nothing is one nobody has to justify keeping.
 *
 * @param {{isEnabled?: () => boolean, client?: {health: () => Promise<any>}}|null} delegation
 * @returns {Promise<{terminal: boolean|null, processes: object[]|null}>} both null when delegation
 *   is off or aify-env did not answer -- and `processes: null` is "could not ask", never "none".
 */
export async function probeEnvTerminal(delegation) {
  const nothing = { terminal: null, processes: null };
  // Nothing to ask when this bridge is the spawner, and asking would spend a timeout every beat
  // against an endpoint nobody is serving.
  if (delegation?.isEnabled?.() !== true) return nothing;
  try {
    const result = await delegation.client.health();
    // `EnvClient.#request` returns { ok: true, handle: <body> } or { ok: false, error }. A refusal
    // is not a no: it is no answer, and the caller must be able to tell those apart.
    if (result?.ok !== true) return nothing;
    const body = result.handle;
    return {
      terminal: envTerminalHealth(body),
      // An absent or non-array `processes` is NOT an empty fleet. Saying "none" about a body we
      // could not read would report every terminal as unknown to this bridge.
      processes: Array.isArray(body?.processes) ? body.processes : null,
    };
  } catch {
    return nothing;
  }
}
