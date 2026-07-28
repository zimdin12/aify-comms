// Which session rows the Sessions list should SHOW.
//
// Operator report 2026-07-28: "i see multiple sc-manager sessions why? (must be a bug)".
//
// Measured before changing anything: sc-manager has 10 rows in `agent_sessions`, but only ONE is
// live. `GET /sessions` already hides pure history (`SESSION_CLEAN_HISTORY_STATUSES` =
// ended/completed/cancelled), so 8 of them were correctly suppressed. What reached the dashboard was
// the live `running` row plus a `stopped` row from EIGHT WEEKS earlier.
//
// That `stopped` row is visible on purpose. Hiding stopped/failed/lost once broke `comms_restart`,
// `comms_compact` and the drawer's Restart/Reset/Compact, because a non-live session is exactly what
// those act on — the server comment records it. So the server is right to serve it.
//
// The missing rule is narrower: once an agent HAS a live session, its old non-live rows are not
// relaunch candidates any more — you would act on the live one. Keeping them listed under the same
// agent name is what reads as a duplicate. So filter for DISPLAY only, per agent, and only when a
// live row exists.
//
// Checked before writing this (the reuse-a-filter mistake that caused the breakage above):
// `comms_restart` resolves its target with
//     sessions.find(s => LIVE.has(s.status)) || sessions[0]
// so it PREFERS the live row — which this never removes — and falls back to the newest only when no
// live row exists, in which case this filter is a no-op. Client-side on purpose: the complaint is a
// display problem, and narrowing the server response would change every consumer.

// Mirrors the server's live set for agent_sessions rows (`_LIVE_SESSION_STATUSES`) plus the
// worker-detail statuses the sessions list also treats as live. Kept explicit rather than imported
// so this module stays pure and testable.
export const LIVE_SESSION_ROW_STATUSES = new Set([
  'starting', 'running', 'recovering', 'restarting', 'cli-takeover', 'attached', 'active', 'idle',
]);

const norm = (v) => String(v == null ? '' : v).trim().toLowerCase();

export function sessionRowIsLive(session) {
  return LIVE_SESSION_ROW_STATUSES.has(norm(session?.status));
}

// ONE AGENT = ONE ENTRY, which is how the operator reads this list: "for me i know only one
// sc-manager. this one identification is one specific agent / session for me.. seeing 2 makes me
// misunderstand". So per agent:
//
//   * has live row(s)  -> show the live ones, hide every non-live row.
//   * has none live    -> show the NEWEST non-live row only (that is the one Restart would act on).
//
// TWO LIVE ROWS ARE BOTH KEPT on purpose. That is not clutter, it is a duplicate-worker leak — a
// class this repo has been bitten by — and hiding it would be the dashboard lying about a real
// fault. Only dead rows collapse.
//
// Nothing is dropped silently: `countSupersededSessions` gives the UI the number it hid, so the
// list can say so (this repo's own rule — a silent cap reads as "that is everything").
//
// "Newest" prefers `lastSeen`/`last_seen`; when timestamps are absent or equal it falls back to the
// list's own order, which the server already returns as `ORDER BY last_seen DESC`.
const defaultAgentIdOf = (s) => s?.agentId ?? s?.agent_id;
const seenAt = (s) => {
  const raw = s?.lastSeen ?? s?.last_seen ?? '';
  const t = Date.parse(raw);
  return Number.isNaN(t) ? null : t;
};

export function collapseSupersededSessions(sessions, { agentIdOf = defaultAgentIdOf } = {}) {
  const list = Array.isArray(sessions) ? sessions : [];
  const agentsWithLive = new Set();
  for (const s of list) {
    if (sessionRowIsLive(s)) {
      const a = norm(agentIdOf(s));
      if (a) agentsWithLive.add(a);
    }
  }
  // For agents with nothing live, pick the single newest non-live row to represent them.
  const keptDeadByAgent = new Map();
  list.forEach((s, index) => {
    if (sessionRowIsLive(s)) return;
    const a = norm(agentIdOf(s));
    if (!a || agentsWithLive.has(a)) return;
    const prev = keptDeadByAgent.get(a);
    if (!prev) { keptDeadByAgent.set(a, { s, index, at: seenAt(s) }); return; }
    const at = seenAt(s);
    if (prev.at == null && at == null) return;           // both undatable -> keep the first
    if (prev.at == null || (at != null && at > prev.at)) keptDeadByAgent.set(a, { s, index, at });
  });
  const keptDead = new Set([...keptDeadByAgent.values()].map((v) => v.s));

  return list.filter((s) => {
    if (sessionRowIsLive(s)) return true;
    const a = norm(agentIdOf(s));
    if (!a) return true; // never hide a row we cannot attribute to an agent
    if (agentsWithLive.has(a)) return false;
    return keptDead.has(s);
  });
}

// How many rows `collapseSupersededSessions` would hide — so the list can show "N older hidden"
// instead of quietly shrinking.
export function countSupersededSessions(sessions, opts) {
  const list = Array.isArray(sessions) ? sessions : [];
  return list.length - collapseSupersededSessions(list, opts).length;
}
