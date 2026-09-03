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

import { state } from './state.mjs';
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
    reason: endReason(terminal, status),
    // THE SESSION THIS TERMINAL BELONGS TO, which is what makes the console reachable from here.
    sessionId: String(terminal?.sessionId || terminal?.session_id || ''),
  };
}

/** Statuses that mean the terminal has ended, and so can have a reason for ending. */
const ENDED = new Set(['stopped', 'failed']);

/**
 * WHY a terminal ended, in the operator's terms.
 *
 * MEASURED BEFORE BUILDING: of 40 live rows, 36 had ended and 33 of those carried a reason -- an
 * `error` string, an `exitCode`, or a signal. The panel showed none of it and said only "stopped",
 * which is the status a terminal reaches whether it was reaped, refused, superseded or simply
 * finished. `service/terminal_diagnostics.py` exists because "what killed this one" is a real
 * question; this is the same question one layer out.
 *
 * THE ERROR TEXT WINS over the numbers when both are present, because it is the half a person can
 * act on: "this host is already running a worker for sc-coder" tells them what happened, and
 * `exit 1` beside it adds nothing.
 *
 * AN ENDED ROW WITH NO REASON SAYS SO, rather than rendering blank. Three of the 36 had none, and a
 * blank cell there is indistinguishable from a cell this panel forgot to fill.
 */
function endReason(terminal, status) {
  if (!ENDED.has(status)) return '';
  const error = String(terminal?.error || '').trim();
  if (error) return error;
  const signal = String(terminal?.exitSignal || '').trim();
  if (signal) return `killed by ${signal}`;
  const code = terminal?.exitCode;
  // `0` IS A REASON and a falsy one, so this tests for null/undefined rather than truthiness: a
  // terminal that exited cleanly is a different fact from one that recorded nothing.
  if (code !== null && code !== undefined && String(code) !== '') return `exit ${Number(code)}`;
  return 'no reason recorded';
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
    // THE REASON LIVES IN THE STATUS CELL IT EXPLAINS, on its own line.
    //
    // My first version made it a `colspan` row below, which needed a CSS rule to stop it reading as
    // a SEPARATE terminal with four empty columns -- and `no-unwatched-oversized-file` refused the
    // eight lines, because styles.css is already past the 1000-line limit and its ceiling may only
    // go down. That refusal was right and produced a better answer: `subtle` is already styled, so
    // this costs no CSS at all, and the reason sits against the status it qualifies rather than
    // below the row it belongs to.
    `<td>${esc(t.status || '—')}${t.reason ? `<br><span class="subtle">${esc(t.reason)}</span>` : ''}</td>`,
    `<td>${t.pid ? `<code>${esc(t.pid)}</code>` : '<span class="subtle">—</span>'}</td>`,
    `<td>${t.size ? esc(t.size) : '<span class="subtle" title="No resize control has completed, so the console snapshot is rendered at an inferred width">—</span>'}</td>`,
    // THE RESULT, not the input, decides. `relTimeHtml` returns '' for a timestamp it cannot parse
    // as well as for a missing one, so testing `t.updatedAt` alone leaves an empty cell whenever the
    // service sends something unexpected -- and an empty cell reads as "no data" rather than "the
    // value made no sense". Same fails-closed rule the drawer's own last-seen row documents.
    `<td>${relTimeHtml(t.updatedAt) || '<span class="subtle">—</span>'}</td>`,
    // REUSES THE DRAWER'S OWN HANDLER rather than adding a second one. `data-agent-open-sessions`
    // already means "select this session and show the Sessions page", and the delegated dispatcher
    // already serves it -- so this costs no new wiring, and there is one implementation of that jump
    // with two callers instead of two implementations that agree until one is edited.
    //
    // OMITTED WHEN THERE IS NO SESSION, not rendered disabled: a console terminal whose session row
    // has gone has nothing to open, and a button that looks clickable and addresses nothing is worse
    // than no button. `state.selectedSessionTab` defaults to 'console', so this lands on the console
    // unless the operator had already switched tabs -- which is their own state, not ours to reset.
    `<td>${t.sessionId
      ? `<button class="ghost" data-agent-open-sessions="${esc(t.sessionId)}" title="Open this terminal's session in the Sessions page">Open</button>`
      : '<span class="subtle">—</span>'}</td>`,
    '</tr>',
  ].join('')).join('');
  const liveCount = list.filter((t) => t.live).length;
  return [
    `<p class="subtle">${list.length} terminal(s), ${liveCount} live.</p>`,
    '<table class="agent-processes"><thead><tr>',
    '<th>Terminal</th><th>Status</th><th>PID</th><th>Size</th><th>Updated</th><th></th>',
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
    if (stillShowing(id)) host.innerHTML = renderAgentProcesses(answer?.terminals);
  } catch (err) {
    if (stillShowing(id)) host.innerHTML = renderAgentProcesses([], { error: String(err?.message || err) });
  }
}

/**
 * Whether the drawer is still open on the agent this read was started for.
 *
 * THE RACE THIS CLOSES. Open agent A, switch to B before A's fetch returns, and A's terminals paint
 * into B's drawer -- under B's name, with B's session and B's runs beside them. A wrong answer that
 * looks entirely right, which is worse than an empty panel. The container is reused across drawer
 * opens, so nothing else would notice.
 *
 * Found reviewing my own work: the sharing panel got this guard because it clears on every open, and
 * this one did not because it is the only ASYNC panel of the three. The synchronous ones cannot
 * race; this one always could.
 */
function stillShowing(agentId) {
  const open = String(state?.inspector?.agentId || '');
  // A drawer that has been closed entirely leaves no agent id, and writing into it would resurrect
  // a panel the operator dismissed.
  return open !== '' && open === agentId;
}
