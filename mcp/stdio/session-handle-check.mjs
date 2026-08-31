// The `session-handles` check: is any conversation claimed by more than one agent?
//
// THE IDS ARE ALREADY UNIQUE. hermes mints `20260715_001441_960b8f`, Claude Code a UUID; nothing
// collides. The failure is the other direction -- SEVERAL AGENTS POINTING AT ONE ID -- so the thing
// to enforce is the BINDING, and the thing to measure is how many agents claim each handle.
//
// TWO LIVE INSTANCES ON 2026-08-31, both found by hand hours apart, neither visible from any status:
//
//   651b895f-…  comms-claude, comms-tech-lead
//   20260715_…  guns-ab-planner, mc-senior-dev, pathweaver-activation-auditor, safety-gate-auditor
//
// The first cost most of a working day. One Claude Code session had been re-registered under a new
// agent id; the old row kept the same session handle with nothing heartbeating for it, so it read
// `offline` for ever while its `lastSeen` kept refreshing on every tool call. Every review verdict
// addressed to it was refused delivery and relayed through the other id, and the reviewer looked
// wedged. Nothing anywhere said "two agents, one session".
//
// The second is how a conversation reaches 1.1M tokens: four agents appending to one thread.
//
// WHY DETECTION AND NOT A REFUSAL. A guard belongs at the bind, but it must mean "another agent
// already holds this", never "this handle exists" -- an agent legitimately keeps its own handle
// across re-registration and every restart, and a naive uniqueness constraint would refuse it that
// and be worse than the defect. Writing that guard needs the mechanism by which the duplicates
// arise, and as of this commit the mechanism is NOT known: two candidate explanations were traced
// and both were disproved against hermes' own source. Reporting is what can be done honestly today,
// and it is not nothing -- both instances above went unnoticed for weeks.

/**
 * Every handle claimed by more than one agent.
 *
 * PURE, and it takes the whole population: a per-agent view cannot see a duplicate at all, which is
 * exactly why nothing caught these.
 *
 * EMPTY HANDLES ARE NOT DUPLICATES. Most of the fleet has none -- an agent that has never bound a
 * session, or a managed one waiting to cold-start -- and grouping those together would report the
 * healthy majority as one enormous collision.
 *
 * @param {Record<string, {sessionHandle?: string}>} agents
 * @returns {{handle: string, agentIds: string[]}[]} sorted, widest collision first
 */
export function duplicateSessionHandles(agents) {
  const byHandle = new Map();
  for (const [agentId, agent] of Object.entries(agents || {})) {
    if (!agent || typeof agent !== "object") continue;
    const handle = String(agent.sessionHandle || "").trim();
    if (!handle) continue;
    if (!byHandle.has(handle)) byHandle.set(handle, []);
    byHandle.get(handle).push(agentId);
  }

  return [...byHandle.entries()]
    .filter(([, ids]) => ids.length > 1)
    .map(([handle, ids]) => ({ handle, agentIds: [...ids].sort() }))
    .sort((a, b) => b.agentIds.length - a.agentIds.length || a.handle.localeCompare(b.handle));
}

/**
 * What the collisions mean, decided over all of them at once.
 *
 * @param {{handle: string, agentIds: string[]}[]} duplicates
 * @param {{measured: number}} counts how many agents carried a handle at all
 */
export function sessionHandleVerdict(duplicates, { measured = 0 } = {}) {
  const found = Array.isArray(duplicates) ? duplicates : [];
  if (!found.length) {
    return {
      ok: true,
      code: "ok",
      detail: measured
        ? `${measured} agent(s) carry a session handle, each one claimed by exactly one agent`
        : "no agent carries a session handle",
    };
  }

  const shared = found.reduce((total, row) => total + row.agentIds.length, 0);
  const lines = found.map((row) => `${row.handle} (${row.agentIds.join(", ")})`);
  return {
    ok: false,
    code: "shared",
    detail: `${found.length} session(s) claimed by more than one agent -- ${shared} agents involved: `
      + lines.join("; "),
    // NAMES THE TWO SHAPES, because they call for opposite actions and the row alone cannot say which
    // one an operator is looking at.
    fix: "Two different problems wear this shape. If the agents share a RESIDENT session, one of them "
      + "is a ghost: a session re-registered under a new id leaves the old row holding the same "
      + "handle with nothing heartbeating for it, so it reads `offline` for ever and every message "
      + "sent to it is refused and relayed. Check which id the process is really bound to -- the "
      + "binding file `aify-agent-<pid>` in TEMP, not the agent row -- and address that one. If they "
      + "share a MANAGED runtime session, they are appending to one conversation and will exhaust its "
      + "context together; see the `context-window` row above.",
  };
}

/**
 * Ask the control plane which agents claim which session.
 *
 * ONE READ. The whole question is answerable from the agent listing, which is why it is cheap enough
 * to run on every doctor invocation.
 */
/**
 * The agent map, or null when what came back was not one.
 *
 * `typeof [] === "object"`, so a bare `typeof` check accepts an ARRAY -- and an array of agents
 * yields zero entries from `Object.entries` in the shape this check wants, which reads as "nobody
 * holds a handle" rather than "that was not an agent map". A reviewer caught this on 2026-08-31, and
 * the sharper half of the catch is that the test below had ALREADY noticed the array reached the
 * counter and merely asserted it did not crash. Documenting a hole is not closing it.
 */
function agentMap(listing) {
  const agents = listing && listing.agents;
  if (!agents || typeof agents !== "object" || Array.isArray(agents)) return null;
  return agents;
}

export async function checkSessionHandles({ get, add }) {
  const listing = await get("/api/v1/agents");
  const agents = agentMap(listing);
  if (!agents) {
    // NO EVIDENCE IS NOT A PASS. An unreadable listing means nothing was compared, and a green row
    // here would be indistinguishable from a fleet with no collisions at all.
    return add("session-handles", false, "unknown",
      "the service did not answer with a readable agent map, so no session handle was compared "
        + "against any other.",
      "Check the `service` row above. A listing that came back in an unexpected shape reports here "
        + "too: an unreadable map and a fleet with no collisions must never render the same.");
  }

  const measured = Object.values(agents)
    .filter((agent) => agent && typeof agent === "object" && String(agent.sessionHandle || "").trim())
    .length;
  const verdict = sessionHandleVerdict(duplicateSessionHandles(agents), { measured });
  return add("session-handles", verdict.ok, verdict.code, verdict.detail, verdict.fix);
}
