// Keeping aify-env's AGENT column equal to the agent that owns each delegated terminal.
//
// WHY THIS IS A RECONCILER AND NOT A CALL AT SPAWN. The spawn already sends a label, and that was
// built first because the caller usually knows the answer by then. The operator's rule is stricter:
// "a wrapper who is auto registered should not differ from one that is registered later on (mid
// conversation)". Identity can arrive after the process exists, so a value written once at birth is
// correct for one path and wrong for every other -- and wrong here means a blank column, which reads
// on screen as broken rather than as unknown.
//
// This repo has the rule already, from a different incident: "cleanup that must hold for ALL paths
// keys on the STATE." A spawn is an event and there are several of them; "whose process is this"
// is a state, and there is one. So this compares what aify-env is displaying against what the bridge
// knows and pushes the difference, on a tick, for ever. Whichever way identity arrived, it converges.
//
// IT PUSHES DIFFERENCES ONLY. A reconciler that rewrote every label every tick would be N writes a
// second to say nothing, and would hide the one case worth seeing -- a label that keeps changing.
//
// WHAT IT DELIBERATELY DOES NOT DO: teach aify-env what an agent is. It sends a string that aify-env
// stores and displays and reads nothing into, exactly as at spawn. docs/AIFY_ENV_BOUNDARY.md owns
// that line and this does not move it.

/**
 * A process aify-env owns, as its `/processes` listing reports it.
 * @typedef {{id: string, label?: string}} EnvProcess
 */

/**
 * A terminal this bridge owns, as `TerminalManager.terminals` holds it.
 * @typedef {{envProcessId?: string, agentId?: string, kind?: string}} OwnedTerminal
 */

/**
 * Which labels are wrong, and what they should be.
 *
 * PURE, so the decision can be tested without an environment, an HTTP client or a clock. The caller
 * does the two I/O halves: read the listing, post the differences.
 *
 * @param {EnvProcess[]} processes   what aify-env says it is displaying
 * @param {Iterable<OwnedTerminal>} terminals  what this bridge believes it owns
 * @returns {{id: string, label: string}[]} one entry per process whose label must change
 */
export function labelsToPush(processes, terminals) {
  // BY ENV PROCESS ID, which is the only identifier both sides share. The terminal id is ours and the
  // pid belongs to the host; neither is a key aify-env indexes by.
  const wanted = new Map();
  for (const terminal of terminals ?? []) {
    // DELEGATED ONLY. A locally-spawned pty has no row in aify-env, and a `kind` we do not recognise
    // is not one we may claim to know the owner of.
    if (terminal?.kind !== "delegated") continue;
    const processId = String(terminal.envProcessId ?? "").trim();
    if (!processId) continue;
    wanted.set(processId, String(terminal.agentId ?? "").trim());
  }

  const pushes = [];
  for (const process_ of processes ?? []) {
    const id = String(process_?.id ?? "").trim();
    if (!id) continue;
    // A PROCESS WE DO NOT OWN IS LEFT ALONE. aify-env is a shared tier: another service's processes
    // appear in the same listing, and relabelling one would be this bridge asserting ownership it
    // does not have.
    if (!wanted.has(id)) continue;
    const label = wanted.get(id);
    const current = String(process_.label ?? "");
    if (current === label) continue;
    pushes.push({ id, label });
  }
  return pushes;
}

/**
 * Apply the differences. Best effort, and quiet about it.
 *
 * NEVER THROWS. This runs inside the terminal control loop, whose job is delivering work; a display
 * label that could abort that loop would trade a cosmetic problem for a functional one. Each push is
 * independent, so one process that has just exited (a 404) does not stop the rest.
 *
 * @param {{client: {list: Function, setLabel: Function}, terminals: Iterable<OwnedTerminal>}} deps
 * @returns {Promise<{pushed: number, failed: number, skipped?: string}>}
 */
export async function reconcileLabels({ client, terminals } = {}) {
  if (!client) return { pushed: 0, failed: 0, skipped: "no-client" };
  let listing;
  try {
    listing = await client.list();
  } catch {
    // aify-env being unreachable is reported by the checks that own that question. Here it simply
    // means there is nothing to reconcile this tick.
    return { pushed: 0, failed: 0, skipped: "unreachable" };
  }
  const processes = listing?.processes ?? (Array.isArray(listing) ? listing : []);
  const pushes = labelsToPush(processes, terminals);
  let pushed = 0;
  let failed = 0;
  for (const push of pushes) {
    try {
      const result = await client.setLabel(push.id, push.label);
      if (result?.ok === false) failed += 1;
      else pushed += 1;
    } catch {
      failed += 1;
    }
  }
  return { pushed, failed };
}
