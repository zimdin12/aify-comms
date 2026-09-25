// What aify-env is running, against what the control plane believes is running.
//
// THE OPERATOR ASKED FOR THIS BY NAME, 2026-08-28: "i have 1 agent running in env but dashboard does
// not show him (that is why i wanted aify-env side running process visibility, to catch orphans like
// that)".
//
// MEASURED THE SAME EVENING. aify-env owned one PTY, pid 155844, `claude-aify --aify-agent ef-manager
// --auto --resume ...`. The control plane's terminal row for that pid read `stopped`, and all 80 most
// recent sessions read `stopped` too. Nothing on any screen connected the two, and nothing could have:
// there was no way to LIST terminals at all. `GET /api/v1/terminals` was added in the same change for
// exactly this reason -- a join needs both sides enumerable.
//
// THE KEY IS THE OS PID, and it is already on both sides. `terminal_sessions.process_id` holds it (99
// of 103 rows numeric, measured) and aify-env's `/processes` reports `pid` per entry. Nothing new had
// to be recorded to make this answerable; it was two lists nobody had put beside each other.
//
// TWO DIRECTIONS, AND THEY MEAN DIFFERENT THINGS:
//
//   unaccounted -- aify-env is running it and the control plane has no live terminal for it. Work
//                  cannot reach that agent, its console is unreachable, and nothing will reap the
//                  process. This is the operator's case.
//   phantom     -- the control plane says a terminal is live and aify-env is not running its pid.
//                  Dispatches route to it and wait for a turn that cannot start.
//
// SCOPED TO ONE ENVIRONMENT, deliberately. A doctor run probes the aify-env on ITS host; terminals
// belonging to another machine's environment are not missing, they are elsewhere. Comparing across
// them would report every other host's terminals as phantoms, which is a check that cries wolf on a
// healthy fleet -- and an alarm that fires when nothing is wrong is one nobody reads on the day
// something is.

/** Terminal statuses that assert a process should exist right now. Mirrors the service's own set. */
export const LIVE_TERMINAL_STATUSES = Object.freeze(["starting", "attached", "running", "active", "idle"]);

/** A pid as a comparable string, or "" when there is nothing to compare. */
function pidKey(value) {
  const text = String(value ?? "").trim();
  // NUMERIC ONLY. `terminal_sessions.process_id` holds an OS pid for 99 of 103 rows and something
  // else for the rest; a non-numeric value is not a pid we can match, and coercing it would
  // manufacture a join between two unrelated strings.
  return /^\d+$/.test(text) ? text : "";
}

/**
 * Compare aify-env's processes against the control plane's live terminals.
 *
 * PURE, and both lists are inputs: the two reads are the caller's job, so every combination is
 * drivable without a host, a container or a clock.
 *
 * @param {object} input
 * @param {{id?: string, pid?: number|string, label?: string, service?: string}[]} input.envProcesses
 * @param {{id?: string, agentId?: string, status?: string, processId?: number|string,
 *          environmentId?: string}[]} input.terminals
 * @param {string} input.environmentId  the environment this aify-env serves; phantoms are scoped to it
 * @param {string} [input.service]      only judge processes started for this service
 * @returns {{unaccounted: object[], phantom: object[], matched: number}}
 */
export function reconcileEnvProcesses({
  envProcesses = [],
  terminals = [],
  environmentId = "",
  service = "aify-comms",
} = {}) {
  const livePidToTerminal = new Map();
  for (const terminal of terminals ?? []) {
    const status = String(terminal?.status ?? "").trim().toLowerCase();
    if (!LIVE_TERMINAL_STATUSES.includes(status)) continue;
    const pid = pidKey(terminal?.processId);
    if (!pid) continue;
    livePidToTerminal.set(pid, terminal);
  }

  const runningPids = new Set();
  const unaccounted = [];
  for (const process_ of envProcesses ?? []) {
    // ONLY OUR OWN. aify-env is a shared tier; another service's process is not this bridge's to
    // call an orphan, and saying so would be asserting knowledge of records we cannot read.
    if (String(process_?.service ?? "") !== service) continue;
    const pid = pidKey(process_?.pid);
    if (!pid) continue;
    runningPids.add(pid);
    if (livePidToTerminal.has(pid)) continue;
    unaccounted.push({
      id: String(process_.id ?? ""),
      pid,
      // The label is aify-env's copy of who this is. It is often EMPTY -- that is the defect the
      // label reconciler exists to fix -- so a reader must not treat its absence as "no agent".
      label: String(process_.label ?? ""),
    });
  }

  // AN UNKNOWN ENVIRONMENT LOSES THIS DIRECTION ENTIRELY, and the first version of this said so in
  // a comment while doing the opposite: an empty id skipped the guard below, so EVERY live terminal
  // on every host was compared and reported. A caller that cannot say which environment is its own
  // cannot tell a missing terminal from somebody else's, and losing one direction is the safe way
  // to be unsure. Caught by the test for the CALL, not by this module's own suite.
  const phantom = [];
  for (const [pid, terminal] of environmentId ? livePidToTerminal : []) {
    // Only this environment's terminals. Another host's live terminal is not missing here.
    if (String(terminal.environmentId ?? "") !== environmentId) continue;
    if (runningPids.has(pid)) continue;
    phantom.push({
      terminalId: String(terminal.id ?? ""),
      agentId: String(terminal.agentId ?? ""),
      pid,
      status: String(terminal.status ?? ""),
    });
  }

  const matched = [...livePidToTerminal.keys()].filter((pid) => runningPids.has(pid)).length;
  return { unaccounted, phantom, matched };
}

/**
 * The check's verdict, in the shape `aify-comms doctor` reports.
 *
 * NO EVIDENCE IS NOT A PASS. If aify-env did not answer, or the terminal listing was TRUNCATED, this
 * reports `unknown` rather than ok -- a truncated list would show its missing rows as orphans, and a
 * check that cannot gather evidence must not read as a clean bill of health. That rule is this repo's,
 * written after `env-bridge` reported "2 connected" with zero bridges alive.
 *
 * @param {object} input
 * @param {ReturnType<typeof reconcileEnvProcesses>|null} input.result
 * @param {boolean} input.envAnswered
 * @param {boolean} input.listingTruncated
 */
export function envProcessVerdict({ result = null, envAnswered = false, listingTruncated = false } = {}) {
  if (!envAnswered) {
    return {
      ok: false,
      code: "unknown",
      detail: "aify-env did not answer, so nothing could be compared against what it is running.",
      fix: "Start aify-env, or check `aify-env doctor` on this host.",
    };
  }
  if (listingTruncated) {
    return {
      ok: false,
      code: "unknown",
      detail: "the terminal listing was truncated, so rows beyond the limit would read as orphans.",
      fix: "Raise the limit on GET /api/v1/terminals and re-run.",
    };
  }
  const unaccounted = result?.unaccounted ?? [];
  const phantom = result?.phantom ?? [];
  if (!unaccounted.length && !phantom.length) {
    return {
      ok: true,
      code: "ok",
      detail: `${result?.matched ?? 0} process(es) matched a live terminal; nothing unaccounted for`,
      fix: "",
    };
  }
  const parts = [];
  if (unaccounted.length) {
    // NAMED, with whatever identity is available. "1 orphan" sends an operator looking; "p1 (pid
    // 155844)" tells them which window to close.
    parts.push(
      `${unaccounted.length} process(es) aify-env is running have NO live terminal: `
      + unaccounted.map((p) => `${p.id} (pid ${p.pid}${p.label ? `, ${p.label}` : ""})`).join(", "),
    );
  }
  if (phantom.length) {
    parts.push(
      `${phantom.length} live terminal(s) name a pid aify-env is not running: `
      + phantom.map((t) => `${t.agentId || t.terminalId} (pid ${t.pid})`).join(", "),
    );
  }
  return {
    ok: false,
    code: unaccounted.length ? "unaccounted" : "phantom",
    detail: parts.join(". ") + ".",
    fix: unaccounted.length
      ? "Those processes hold a session nothing can address and nothing will reap. Stop them from the "
        + "aify-env view, or restart the named agent so the wrapper reaps its predecessor."
      : "Those terminals will take dispatches and never start a turn. Restart the named agents.",
  };
}
