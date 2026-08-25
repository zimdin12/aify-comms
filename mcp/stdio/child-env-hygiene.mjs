// What a spawned worker must NOT inherit from whatever started the bridge.
//
// Seven places in this bridge build a child environment by spreading the parent's, and until now each
// one guarded its own known-bad variable, separately, after that variable had caused a visible
// problem. Two have been caught that way so far:
//
//   * `AIFY_AGENT_ROLE` — a bridge with a role set gave every worker it launched that role. Re-register
//     is a full state refresh, so spawning a `tester` produced a `coder` and nothing reported a fault.
//   * `CLAUDE_CODE_CHILD_SESSION` — a bridge started from inside a Claude Code session made every
//     managed agent run with TRANSCRIPT SAVING OFF, announced by one line in a TUI nobody reads and
//     unrecoverable afterwards.
//
// The pattern is the defect, not either variable: a bridge's own ancestry reaches everything it starts,
// and it is discovered one symptom at a time. This is the inverse — the list is stated once, with the
// reason beside each entry, so the third one is refused by construction.
//
// A DENYLIST, DELIBERATELY, not an allowlist. A child genuinely needs most of what it inherits: PATH,
// HOME, the proxy variables, the runtime's own credentials, whatever an operator exported for it. An
// allowlist would be a list of everything a coding agent might ever read, maintained by people who
// cannot know it, and its failure mode is a worker that mysteriously cannot reach something. This
// list's failure mode is bounded: a variable nobody has been bitten by yet gets through.
//
// WHY STRIPPING, AND NOT SETTING A SAFE VALUE, IS THE FIX -- proven the hard way on 2026-08-25.
//
// terminal-env.js neutralises the ROLE FLAGS by setting them to "0", and that works: their consumer
// tests `["1","true","yes"].includes(value)`, for which "0" is a definitive no. It sets the ROLE
// STRING to "" for the same reason, and that does NOT work: its consumer is
// `AIFY_AGENT_ROLE || AIFY_COMMS_AGENT_ROLE || "coder"`, and "" is falsy, so the chain falls through
// to the inherited alias. Measured: a bridge holding AIFY_COMMS_AGENT_ROLE=manager spawned a worker
// with an unknown role, and the worker resolved its role as "manager" instead of its own default.
//
// So a value only neutralises inheritance when the CONSUMER treats that value as definitive. Removing
// the name is definitive for every consumer, which is why this list removes rather than assigns.
//
// STRIPPED, never blanked to "". An empty string is a value, and a runtime asking "is this set?" reads
// it as yes. `terminal-env.js` sets some of these to "" ON PURPOSE for a different reason -- it wants
// the child to fall back to its own default rather than inherit a wrong one -- and that stays, because
// it is setting a value it owns rather than removing an inherited one.

/**
 * Variables a managed worker must never inherit, and why. The reason is part of the entry: a bare list
 * of names invites the next reader to add one on suspicion, and to delete one that looks unused.
 */
export const NEVER_INHERITED = Object.freeze({
  CLAUDE_CODE_CHILD_SESSION:
    "marks the process as a child session, which disables transcript saving. A managed agent is never "
    + "a child of whatever launched the bridge, and a lost transcript cannot be recovered afterwards.",
  AIFY_AGENT_ROLE:
    "the bridge's own role would become every spawned worker's role. Re-register is a full state "
    + "refresh, so the spawn's real role is overwritten and nothing reports a fault.",
  AIFY_AGENT_ID:
    "the bridge's identity would become the worker's, so the worker reports as, and can be addressed "
    + "as, the process that started it.",
  AIFY_COMMS_AGENT_ROLE:
    "the same role by its other name, and the one this list originally missed. launch-identity reads "
    + "`AIFY_AGENT_ROLE || AIFY_COMMS_AGENT_ROLE || 'coder'`, so stripping only the first leaves the "
    + "bridge's role reachable through the alias.",
  AIFY_COMMS_AGENT_ID:
    "the same identity by its other name; stripping one and not the other leaves the child holding two "
    + "answers to who it is.",
});

/**
 * A copy of `env` with the inherited markers removed.
 *
 * Pure, and it copies: mutating the caller's object would reach `process.env` at most call sites here,
 * which is the kind of action-at-a-distance this module exists to end.
 *
 * @param {Record<string, string|undefined>} env
 * @returns {Record<string, string|undefined>}
 */
export function withoutInheritedMarkers(env = {}) {
  const out = { ...env };
  for (const name of Object.keys(NEVER_INHERITED)) delete out[name];
  return out;
}

/**
 * Which of the markers are present in an environment. For a caller that wants to SAY it dropped
 * something rather than drop it silently -- a spawn that quietly changes its child's configuration is
 * how both of the known cases stayed invisible for so long.
 */
export function inheritedMarkersIn(env = {}) {
  return Object.keys(NEVER_INHERITED).filter((name) => {
    const value = env[name];
    return typeof value === "string" && value.trim() !== "";
  });
}
