// Which of aify-env's processes have no owner left, and which of those anyone may act on.
//
// THE SITUATION, measured on the operator's fleet 2026-08-31. Ten `apg-pilot` agents were spawned,
// one of them twice, the agents were then intentionally removed -- and ELEVEN Claude Code processes
// carried on running with no live agent and nothing that would ever collect them. `env-processes`
// reports them correctly; nothing acts on the report.
//
// WHAT IS *NOT* CLAIMED HERE, because it cannot be read after the fact: whether those agents ever
// bound a session handle or ever had a terminal. `agents` cascades to `agent_sessions` and on to
// `terminal_sessions`, so after the delete, "none exist now" and "none ever existed" look identical.
// An earlier draft of this comment asserted the second. It was asserted in the same round in which I
// had just corrected exactly that reasoning, which is the way a retracted claim gets back in: the
// retraction goes in a message and the sentence stays in the file.
//
// THIS MODULE REPORTS. IT DOES NOT AUTHORISE A STOP.
//
// That was a design decision, and it was not mine. The first version of this returned a `reap` list on
// `tombstone + label`, and the reviewer refused it on 2026-09-01 for a reason that survives every
// argument I had: the ONLY join from a process to an agent is `label`, which is a display string the
// caller hands aify-env at spawn time. It is mutable, it is reusable, and it is not an identity. A
// cross-tier stop is irreversible from this side. So `candidates` here means "an operator should look
// at these", never "these may be killed", and the thing that eventually stops a process must carry
// authority this module cannot supply: an immutable service-scoped owner id recorded at process
// creation, the exact env process id, and the tombstone epoch revalidated at the moment of action.
//
// STATE, NEVER AN EVENT OR A CLOCK. What is reported is derived from what IS, not from what happened or
// how long ago. That makes it converge for every path at once -- removal, a crash mid-removal, a
// control the bridge never executed, a bridge restart -- instead of being correct only for the sequence
// somebody thought of. DECISIONS.md settled this after a spawn sat `running` for 97 minutes because
// cleanup keyed on one of ~26 writers calling it.
//
// THE DANGEROUS AMBIGUITY. "The agent is not in the listing" means two opposite things: intentionally
// removed, or not registered YET. The second is an agent mid-spawn. The service already separates them
// and this is the only reason the question can be answered at all:
//
//     410  the tombstone branch, `Agent '<id>' was intentionally removed`  -> removed
//     404  never existed, or not registered yet                            -> SAY NOTHING ABOUT IT
//     200  alive                                                           -> owned
//
// Verified against the live service: apg-pilot-07 answers 410, comms-tech-lead 200, and a fabricated
// id 404.
//
// THOSE STATUSES ARE NOT THE INTENDED CONTRACT, though, and an earlier version of this comment said
// no new endpoint was needed because of them. Eleven routes raise that 410 with independently-worded
// human prose; reading ownership by classifying it means parsing text nobody promised to keep stable,
// and error prose cannot carry completeness or version semantics at all -- a truncated answer and a
// complete one look the same. The agreed shape is a typed batch read keyed by the owner ids actually
// observed, returning one explicit row per request and a typed refusal, with the consumer rejecting
// partial input. `ownerFromStatus` stays because it is a pure mapping worth pinning, not because the
// status codes are the interface.
//
// EVERYTHING NOT UNDERSTOOD IS KEPT. An unrecognised status, a service that did not answer, a process
// whose label names nothing, a removal whose time is unknown -- all `keep`. The cost of a wrong
// `candidate` is an operator pointed at a working agent; the cost of a wrong `keep` is a process that
// shows up in the next report. Those are not comparable, so this fails toward saying nothing.
//
// AND THE ORDERING IS ACROSS TWO CLOCKS. `startedAtMs` comes from aify-env's host, `removed_at` from
// aify-comms'; the architecture permits separate machines, so the comparison below is advisory and
// cannot become stop authority however carefully it is written. See the note at the guard itself for
// the generation-equality design that replaces it. `service/api_core/registration_gates.py:322` uses a
// comparison of the same SHAPE for bridge relaunch freshness -- which shows this codebase already
// treats temporal generation as a bounded heuristic, NOT that the heuristic is sufficient. Its
// boundary is not even the same as this one: registration counts only STRICTLY NEWER as a fresh
// relaunch, while this keeps a process whose start is not strictly older. Both lean away from acting,
// in opposite directions, and neither is the single exact rule.

/** What the service said about an agent, reduced to the only three answers that matter here. */
export const OWNER_ALIVE = "alive";
export const OWNER_REMOVED = "removed";
export const OWNER_UNKNOWN = "unknown";

/**
 * Turn an HTTP status into an ownership fact.
 *
 * ONLY 410 MEANS REMOVED. Not 4xx, not "any error" -- a 403, a 500 or a proxy's 502 say nothing about
 * whether the agent exists, and treating them as removal is how a service blip becomes a fleet-wide
 * reap. 404 is explicitly NOT removal: an agent that has not registered yet answers 404, and it is the
 * case that must never be acted on.
 */
export function ownerFromStatus(status) {
  const code = Number(status);
  if (code === 200) return OWNER_ALIVE;
  if (code === 410) return OWNER_REMOVED;
  return OWNER_UNKNOWN;
}

/**
 * A timestamp, or null when the input does not carry one.
 *
 * `Number("")` IS 0, AND 0 IS FINITE. So an empty start time passed a `Number.isFinite` guard as the
 * first millisecond of 1970 -- which orders BEFORE every possible removal, making the least
 * informative input produce the most permissive answer. My own test caught it; the guard had looked
 * right and was backwards where it mattered. Booleans, empty arrays and whitespace all coerce the same
 * way, so the type is checked before the value.
 */
function epochMs(value) {
  if (typeof value !== "number") return null;
  if (!Number.isFinite(value) || value <= 0) return null;
  return value;
}

/**
 * Which owned processes an operator should look at, and which to leave alone.
 *
 * PURE: the process list, the ownership answers and the removal times are all handed in, so every rule
 * here is testable without a service, a daemon or a process to kill.
 *
 * @param {{id:string, pid:number, label:string, startedAtMs:number}[]} processes  what aify-env reports
 * @param {Record<string,string>} owners  agentId -> OWNER_*, from `ownerFromStatus`
 * @param {Record<string,number>} removedAt  agentId -> epoch ms the tombstone was written
 * @returns {{candidates:object[], keep:object[], invalid:object[]}} EVERY input row appears in exactly
 *   one of the three lists -- including one that is not a process row at all, which goes to `invalid`
 */
export function unownedProcessDecision(processes = [], owners = {}, removedAt = {}) {
  const rows = Array.isArray(processes) ? processes : [];
  const ownerOf = owners && typeof owners === "object" ? owners : {};
  const removedAtOf = removedAt && typeof removedAt === "object" ? removedAt : {};
  const candidates = [];
  const keep = [];
  const invalid = [];

  for (const entry of rows) {
    if (!entry || typeof entry !== "object") {
      // A TYPED BUCKET, NOT A `continue`. This used to drop the row and the JSDoc above used to
      // promise a complete partition anyway -- a false claim that a test of mine actively blessed by
      // asserting junk was "skipped". A malformed row means the listing is not what this code thinks
      // it is, which is a fact about the instrument and belongs in the output where a caller can see
      // it. Silently discarding it is how a truncated or renamed listing reads as a clean fleet.
      invalid.push({ raw: entry, why: "not a process row" });
      continue;
    }
    const label = String(entry.label ?? "").trim();
    const row = { id: entry.id, pid: entry.pid, label };

    if (!label) {
      // UNCLAIMED. The label is the only link from a process to an agent, so a process without one
      // cannot be attributed and must not be guessed at. `gateway-orphans` reports this case the same
      // way, and reporting is where it stops.
      keep.push({ ...row, why: "no label, so no agent can be named" });
      continue;
    }

    const owner = ownerOf[label];
    if (owner === OWNER_ALIVE) {
      keep.push({ ...row, why: "its agent is alive" });
      continue;
    }
    if (owner !== OWNER_REMOVED) {
      // Includes an agent that answered 404 -- which may simply not have registered yet -- and every
      // case where the service did not answer at all.
      keep.push({ ...row, why: "ownership could not be established" });
      continue;
    }

    // GENERATION, NOT JUST INTENT. A tombstone says the agent was removed. It does not say that THIS
    // process predates the removal, and those are different claims.
    //
    // RESTORE DELETES THE TOMBSTONE, so an agent can be removed, restored, and started again. A
    // reconcile snapshot taken before that restore and acted on after it would name the NEW process
    // while reading the OLD tombstone. The reviewer found this on 2026-09-01 and my design had it
    // exactly: it keyed on whether a tombstone existed and never on when.
    //
    // So a process is only a candidate if it appears to have STARTED BEFORE its owner was removed. One
    // that began afterwards belongs to a life this tombstone knows nothing about.
    //
    // "APPEARS TO" IS LOAD-BEARING, and it is the second thing I got wrong. These two numbers come
    // from DIFFERENT CLOCKS: `startedAtMs` is minted by aify-env's host, `removed_at` by aify-comms',
    // and the architecture permits those to be separate machines. Under skew the comparison can order
    // them wrongly in either direction, so this NARROWS the restore race rather than closing it. It is
    // a bounded heuristic for a report a human reads, and it is not stop authority.
    //
    // WHAT WOULD BE. An opaque lifecycle generation minted by aify-comms: the spawn carries
    // `{ownerAgentId, ownerGeneration}` through aify-env unchanged, the tombstone records the
    // generation it removed, and eligibility is EXACT GENERATION EQUALITY plus current tombstone state
    // plus exact env process identity. No clock is compared at all, and a restore mints a new
    // generation, so a stale snapshot cannot name the restored process. That is the design; it is not
    // built, and until it is, nothing here may drive an automatic stop.
    const startedAt = epochMs(entry.startedAtMs);
    const removed = epochMs(removedAtOf[label]);
    if (startedAt === null || removed === null) {
      // An ordering that cannot be established is not a licence to name the process.
      keep.push({ ...row, why: "removed, but this process cannot be placed against the removal" });
    } else if (startedAt >= removed) {
      // NOT PROVEN STRICTLY BEFORE. This branch covers two different situations and deliberately gives
      // them one answer: a process that started after the removal (a later life this tombstone knows
      // nothing about), and one whose start EQUALS it. Equal is not after, and it is not before
      // either -- it is the case where the ordering carries no information, which is exactly when a
      // report must not name something. Across two clocks an equal reading is not even evidence of
      // simultaneity, only of resolution.
      keep.push({ ...row, why: "not proven to have started strictly before the removal (at or after it)" });
    } else {
      candidates.push({ ...row, startedAt, removedAt: removed, why: "started before its agent was removed" });
    }
  }

  return { candidates, keep, invalid };
}

/**
 * The sentence an operator reads.
 *
 * NAMES EVERY PROCESS, with pid and label, and says what it is asking for. A report that gives a count
 * is asking to be trusted; one that names its subjects can be argued with. The wording commits to
 * reporting on purpose -- this module has no stop authority and its output must not read as though it
 * does.
 */
export function describeDecision({ candidates = [], keep = [], invalid = [] } = {}) {
  // A MALFORMED ROW IS REPORTED FIRST, and separately. It says the listing is not the shape this code
  // expects, which is a fact about the instrument rather than about the fleet -- and an instrument
  // this code cannot read makes every count below it a partial one.
  const malformed = invalid.length
    ? `. ${invalid.length} row(s) in the listing were not process rows and could not be classified, so `
      + "these counts are of what was readable"
    : "";
  if (!candidates.length) {
    // "CLASSIFIED", NOT "OWNED". `keep` holds rows whose ownership was never established and rows with
    // no label to attribute at all, so calling them owned asserts the one thing this module could not
    // determine about them.
    return `${keep.length} process(es) classified, none to report: each has a live agent, an owner that `
      + `could not be established, or a start not proven before its agent was removed${malformed}`;
  }
  const named = candidates.map((r) => `${r.label} pid ${r.pid}`).join(", ");
  // BOTH LIMITS, EVERY TIME. The reviewer allowed this classification to stand only on condition that
  // its output names them, and they are the two ways a candidate here can be wrong: the join is a
  // label rather than an identity, and the ordering is between two clocks that need not agree.
  return `${candidates.length} of ${candidates.length + keep.length} process(es) appear to have started `
    + `before their agent was intentionally removed and are still running: ${named}. Stopping one is an `
    + "operator action. Two limits on this list: the process-to-agent join is a mutable label, not an "
    + "identity; and the ordering compares aify-env's host clock against aify-comms' clock, which are "
    + `not the same clock, so it is advisory rather than proof${malformed}.`;
}
