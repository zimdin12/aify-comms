// What this agent has been ASKED TO DO, in the drawer that opens on it.
//
// B5's second half. The Processes panel beside this one answers "what is running for it"; this
// answers "what work reached it, and did any of it close". The two look like one subject and are
// not: a terminal is a process on a host, a run is a piece of dispatched work, and an agent can
// easily have three of one and none of the other.
//
// NO FETCH. `state.runs` is already polled by the refresh cycle -- unlike terminals, which nothing
// loaded until `agent-processes.mjs` asked for them. Adding a per-agent query here would be a second
// read of rows the page is already holding, so this filters what is there.
//
// WHICH MEANS THE WINDOW IS PARTIAL, and the panel has to say so rather than imply it is a history.
// `/dispatch/runs` is a limited page and `state.runsTruncated` records whether it was cut; measured
// on the live database 2026-08-29, a limit=80 page reached back only to 26 August. An agent whose
// last run fell off that page renders here as an agent with no runs, and the difference between "no
// work ever reached this agent" and "none in the loaded page" is the whole value of the panel.
//
// REPLY-PENDING IS CALLED OUT because it is the actionable state. A run that finished and owes a
// reply nobody sent is the shape that strands a requester, and it is invisible in a status column
// that reads `completed`.

import { runTargetAgent } from './record-fields.mjs';
import { state } from './state.mjs';
import { esc, relTimeHtml } from './util.js';

/** Statuses that mean the run has not settled yet. */
const OPEN = new Set(['queued', 'claimed', 'running', 'delivered']);

/**
 * The runs targeting one agent, newest first.
 *
 * SORTED HERE rather than trusted from the caller. `state.runs` is ordered for the runs PAGE, and a
 * panel that shows five rows out of a page ordered for something else shows an arbitrary five.
 */
export function runsForAgent(runs, agentId) {
  const id = String(agentId || '').trim();
  if (!id) return [];
  const list = Array.isArray(runs) ? runs : [];
  return list
    .filter((run) => runTargetAgent(run) === id)
    .slice()
    .sort((a, b) => String(b?.requestedAt || '').localeCompare(String(a?.requestedAt || '')));
}

/**
 * The panel's body.
 *
 * PURE, and `truncated` is a parameter rather than a read of `state`, so the partial-window case can
 * be tested without arranging global state to be in it.
 *
 * @param {object[]} runs every run the page is holding
 * @param {string} agentId whose drawer this is
 * @param {{truncated?: boolean, limit?: number}} [options]
 */
export function renderAgentRuns(runs, agentId, { truncated = false, limit = 5 } = {}) {
  const mine = runsForAgent(runs, agentId);
  if (mine.length === 0) {
    // THE TWO EMPTY STATES ARE DIFFERENT ANSWERS. "Nothing ever reached this agent" and "nothing in
    // the page we loaded" call for opposite reactions, and `run-inspector.mjs` already makes this
    // distinction for the runs list -- the same reasoning, one panel over.
    return truncated
      ? '<p class="subtle">No runs for this agent in the loaded page. Older runs are not loaded.</p>'
      : '<p class="subtle">No dispatch runs have targeted this agent.</p>';
  }
  const shown = mine.slice(0, Math.max(1, limit));
  const rows = shown.map((run) => {
    const status = String(run?.status || '').trim().toLowerCase();
    // A REPLY THAT IS OWED AND MISSING, on a run that has otherwise finished. `replyPending` on an
    // open run is just "not yet"; on a settled one it is somebody waiting.
    const owed = Boolean(run?.replyPending) && !OPEN.has(status);
    return [
      '<tr>',
      `<td>${esc(status || '—')}${owed ? ' <span class="subtle" title="This run owes a reply that has not been sent">· reply owed</span>' : ''}</td>`,
      `<td><strong class="clip">${esc(String(run?.subject || run?.id || ''))}</strong></td>`,
      `<td>${relTimeHtml(run?.requestedAt) || '<span class="subtle">—</span>'}</td>`,
      '</tr>',
    ].join('');
  }).join('');
  // SAYS WHAT IT IS SHOWING OUT OF WHAT, always. "3 runs" beside a page that holds five of this
  // agent's is a count of the panel, not of the agent, and nothing on screen would say which.
  const more = mine.length > shown.length ? ` of ${mine.length} loaded` : '';
  const older = truncated ? ' Older runs are not loaded.' : '';
  return [
    `<p class="subtle">Showing ${shown.length}${more}.${older}</p>`,
    '<table class="agent-runs"><thead><tr>',
    '<th>Status</th><th>Subject</th><th>Requested</th>',
    '</tr></thead><tbody>',
    rows,
    '</tbody></table>',
  ].join('');
}

/** The container the drawer leaves for this panel, so both sides name it once. */
export const AGENT_RUNS_ID = 'agent-drawer-runs';

/**
 * Fill the panel for one agent from the rows the page already holds.
 *
 * SYNCHRONOUS, unlike the processes panel, and that difference is the point: there is nothing to
 * wait for. A caller that made this async would invite a loading placeholder for data already in
 * memory.
 */
export function fillAgentRuns(agentId, { byId } = {}) {
  const host = typeof byId === 'function' ? byId(AGENT_RUNS_ID) : null;
  if (!host) return;
  host.innerHTML = renderAgentRuns(state.runs, agentId, { truncated: state.runsTruncated });
}
