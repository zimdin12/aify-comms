// Who ELSE claims this agent's session, in the drawer that opens on it.
//
// B4, the operator's complaint: "i never go to that path... i have container that should give me
// that info. some random path for aify-comms doctor... no. will never use it." `session-handles` is
// one of the four doctor checks answerable from the service's own data, and the dashboard already
// polls `/agents` -- the whole population, which is exactly what this needs. So it costs no new
// endpoint, no extra poll and no service change: the data was already on the page, unread.
//
// THE PER-AGENT FORM IS THE ACTIONABLE ONE. The doctor reports the fleet-wide list, which is right
// for a fleet-wide report and useless when you are looking at one agent wondering why its replies
// go missing. "This session is also claimed by comms-tech-lead" is the sentence that explains it.
//
// WHAT IT COSTS TO NOT KNOW, measured 2026-08-31 and STILL TRUE on 2026-09-03: three handles claimed
// by eight agents. One Claude Code session had been re-registered under a new agent id; the old row
// kept the same handle with nothing heartbeating for it, so it read `offline` for ever while its
// `lastSeen` refreshed on every tool call. Every verdict addressed to it was refused and relayed
// through the other id, and the reviewer looked wedged. That cost most of a working day, and nothing
// anywhere said "two agents, one session".
//
// THE IDS ARE ALREADY UNIQUE -- hermes mints `20260715_001441_960b8f`, Claude Code a UUID, nothing
// collides. The failure is the other direction: several agents pointing at ONE id. So the thing to
// measure is the binding, which is why this takes the whole population rather than one row.
//
// AGREEMENT, NOT A SECOND OPINION. `mcp/stdio/session-handle-check.mjs` owns this question for the
// doctor and cannot be imported into a browser bundle. `agent-session-sharing.agreement.test.mjs`
// drives both over one corpus and fails on any disagreement -- this repo's standing answer to
// duplication it cannot remove, the same one `credential-ref.mjs` gets.

import { esc } from './util.js';

/**
 * The OTHER agents claiming this agent's session handle.
 *
 * EMPTY HANDLES ARE NOT SHARING. Most of the fleet has none -- 11 of 44 on this host -- and treating
 * "no handle" as a shared value would report the healthy majority as one enormous collision, which
 * is the mistake that makes a check like this get switched off.
 *
 * @param {object[]|Record<string, object>} agents the whole population, as a list or an id map
 * @param {string} agentId whose drawer this is
 * @returns {string[]} other agent ids, sorted; empty when this session is this agent's alone
 */
export function sessionSharers(agents, agentId) {
  const id = String(agentId || '').trim();
  if (!id) return [];
  const rows = Array.isArray(agents)
    ? agents.map((agent) => [String(agent?.id || ''), agent])
    : Object.entries(agents || {});

  const mine = rows.find(([rowId]) => rowId === id);
  const handle = String((mine && mine[1] && mine[1].sessionHandle) || '').trim();
  if (!handle) return [];

  return rows
    .filter(([rowId, agent]) => rowId && rowId !== id
      && String(agent?.sessionHandle || '').trim() === handle)
    .map(([rowId]) => rowId)
    .sort();
}

/**
 * The warning, or '' when there is nothing to warn about.
 *
 * SILENT WHEN HEALTHY, deliberately. A row reading "this session belongs to this agent alone" on 36
 * of 44 agents is noise that teaches the reader to skip the whole panel -- and then the eight that
 * matter are skipped with it.
 */
export function renderSessionSharing(agents, agentId) {
  const others = sessionSharers(agents, agentId);
  if (others.length === 0) return '';
  const who = others.map((other) => `<code>${esc(other)}</code>`).join(', ');
  return `<p class="subtle"><strong>This session is also claimed by ${others.length} other agent`
    + `${others.length === 1 ? '' : 's'}:</strong> ${who}. Messages addressed to one of them may be `
    + `refused and relayed, and every one of them appends to the same conversation.</p>`;
}

/** The container the drawer leaves for this, so both sides name it once. */
export const AGENT_SHARING_ID = 'agent-drawer-sharing';

/**
 * Fill it for one agent from the population already on the page.
 *
 * WRITES '' WHEN THERE IS NOTHING TO SAY, rather than leaving whatever the previous agent's drawer
 * put there. The container is reused across drawer opens, so a stale warning would name the wrong
 * agent's neighbours -- which is worse than saying nothing, because it reads as current.
 */
export function fillSessionSharing(agentId, { byId, agents } = {}) {
  const host = typeof byId === 'function' ? byId(AGENT_SHARING_ID) : null;
  if (!host) return;
  host.innerHTML = renderSessionSharing(agents, agentId);
}
