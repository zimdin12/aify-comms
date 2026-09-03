// The processes behind an agent, in the drawer that opens on it.
//
// B5, the operator's own words: "i cannot still check the processes themself? (like browse agent or
// something)". The drawer already answered runtime, mode, environment, workspace, session, machine
// and last-seen -- everything ABOUT the agent, and nothing about what is actually running for it. A
// pid was reachable only by reading the database by hand.
//
// STATUS=ALL, NOT `live`, and that is the whole point of the panel. A row that reads `stopped` while
// still holding a pid is precisely the orphan the operator went looking for -- aify-env owned a live
// PTY for `ef-manager` (pid 155844) while every recent session read `stopped` and the dashboard
// showed nothing. Filtering to live would hide the case this exists to surface.
//
// THE PID IS THE JOIN KEY, which is why it gets a column rather than a tooltip. `/terminals`'s own
// docstring says so: it is the field aify-env's process listing shares, so it is what lets a person
// match a row here against something actually alive on the host.
//
// COLS IS SHOWN because a zero there means something specific and otherwise invisible: no resize
// control has ever completed for that terminal, so the console snapshot is rendered at an INFERRED
// width and re-wraps every line. That is the "scrambled console" complaint (B3), and this is the one
// place a person can see which terminals are exposed to it.
//
// FETCHED WHEN THE DRAWER OPENS, never polled. The dashboard's periodic refresh is already nine
// endpoints; a tenth paid on every tick to fill a panel nobody has opened is the trade this avoids.
// "Browse" is a deliberate act, so the read is too.

import { esc, relTimeHtml } from './util.js';

/** Terminal statuses that mean the row is meant to be running. Mirrors the service's own set. */
const LIVE = new Set(['starting', 'running', 'attached']);

/**
 * One row's cells, already escaped.
 *
 * A MISSING VALUE RENDERS AN EM DASH AND CLAIMS NOTHING, rather than 0 or "unknown". A pid of 0 and
 * a pid nobody recorded are different facts, and the second one must not read as the first: an
 * operator hunting an orphan would try to kill it.
 */
function cells(terminal) {
  const status = String(terminal?.status || '').trim().toLowerCase();
  const pid = Number(terminal?.processId || terminal?.process_id || 0);
  const cols = Number(terminal?.cols || 0);
  const rows = Number(terminal?.rows || 0);
  return {
    id: String(terminal?.id || ''),
    status,
    live: LIVE.has(status),
    pid: pid > 0 ? String(pid) : '',
    // BOTH OR NEITHER. A width with no height is not a grid, and printing "157x0" invites somebody
    // to believe the height is real.
    size: cols > 0 && rows > 0 ? `${cols}x${rows}` : '',
    runtime: String(terminal?.runtime || ''),
    updatedAt: String(terminal?.updatedAt || terminal?.updated_at || ''),
  };
}

/**
 * The panel's body for a set of terminals.
 *
 * PURE, so the table can be tested without a DOM, a fetch or a service. The loader below is the
 * only part that needs any of those, and it is deliberately thin.
 *
 * @param {object[]} terminals as `/terminals` returns them
 * @param {{error?: string}} [options] a fetch that failed says so here rather than rendering empty
 */
export function renderAgentProcesses(terminals, { error = '' } = {}) {
  // AN ERROR IS NOT AN EMPTY LIST. Rendering "no processes" after a failed read tells the operator
  // something false about their fleet, which is worse than telling them the panel is broken.
  if (error) {
    return `<p class="subtle">Could not read this agent's processes: ${esc(error)}</p>`;
  }
  const list = Array.isArray(terminals) ? terminals.map(cells).filter((t) => t.id) : [];
  if (list.length === 0) {
    // SAYS WHY, never an absent section. The drawer already makes this argument for the CLI block:
    // "an absent section is indistinguishable from a broken feature".
    return '<p class="subtle">No terminals have been created for this agent.</p>';
  }
  const body = list.map((t) => [
    '<tr>',
    `<td><code>${esc(t.id)}</code></td>`,
    `<td>${esc(t.status || '—')}</td>`,
    `<td>${t.pid ? `<code>${esc(t.pid)}</code>` : '<span class="subtle">—</span>'}</td>`,
    `<td>${t.size ? esc(t.size) : '<span class="subtle" title="No resize control has completed, so the console snapshot is rendered at an inferred width">—</span>'}</td>`,
    // THE RESULT, not the input, decides. `relTimeHtml` returns '' for a timestamp it cannot parse
    // as well as for a missing one, so testing `t.updatedAt` alone leaves an empty cell whenever the
    // service sends something unexpected -- and an empty cell reads as "no data" rather than "the
    // value made no sense". Same fails-closed rule the drawer's own last-seen row documents.
    `<td>${relTimeHtml(t.updatedAt) || '<span class="subtle">—</span>'}</td>`,
    '</tr>',
  ].join('')).join('');
  const liveCount = list.filter((t) => t.live).length;
  return [
    `<p class="subtle">${list.length} terminal(s), ${liveCount} live.</p>`,
    '<table class="agent-processes"><thead><tr>',
    '<th>Terminal</th><th>Status</th><th>PID</th><th>Size</th><th>Updated</th>',
    '</tr></thead><tbody>',
    body,
    '</tbody></table>',
  ].join('');
}

/** The container the drawer leaves for this panel, so both sides name it once. */
export const AGENT_PROCESSES_ID = 'agent-drawer-processes';

/**
 * Fill the panel for one agent.
 *
 * DEPENDENCIES INJECTED rather than imported, so this is testable without standing up the api
 * client or a document. That is not ceremony here: the interesting behaviour is what it renders when
 * the fetch FAILS, and a test that cannot make a fetch fail cannot reach it.
 *
 * NEVER THROWS. It fills a panel; a rejected read must not take the drawer down with it, because
 * everything else in that drawer is still true and still useful.
 */
export async function loadAgentProcesses(agentId, { api, byId } = {}) {
  const id = String(agentId || '').trim();
  const host = typeof byId === 'function' ? byId(AGENT_PROCESSES_ID) : null;
  if (!host || !id || typeof api !== 'function') return;
  try {
    // `status=all` so a stopped row holding a pid is visible -- see the header.
    const answer = await api(`/terminals?agentId=${encodeURIComponent(id)}&status=all`);
    host.innerHTML = renderAgentProcesses(answer?.terminals);
  } catch (err) {
    host.innerHTML = renderAgentProcesses([], { error: String(err?.message || err) });
  }
}
