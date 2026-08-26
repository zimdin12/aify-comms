// Why a dead session's console is dead, said in the one place the operator looks.
//
// THE GAP. The service has recorded how a terminal ended since 2026-08-26 -- an exit code, a signal,
// the failure line from its last output, and which store answered -- and `GET /agents/{id}/console`
// returns all of it. The dashboard's Console tab did not ask. It rendered, from the SESSION row,
// `This session is stopped -- no live console`, which is the word for a deliberate shutdown and says
// nothing about a worker something killed. On the day the operator asked why their agents kept
// dropping, that sentence was on screen and the answer was one request away.
//
// WHY NOT JOIN IT INTO THE SESSION ROW INSTEAD. `agent_sessions` carries `terminal_status` and no exit
// columns, so serving it there means joining `terminal_sessions` into the sessions query -- a
// per-request join, on the hot poll, for a field that only matters once something has died. This asks
// the endpoint that already answers, on a branch that only renders for a dead session.
//
// PURE HALF AND FETCHING HALF, kept apart. The sentence is decided by a function that takes a payload
// and returns a string, so every case can be asserted without a network or a DOM. The fetching half is
// four lines and does nothing but hand the payload over.

/**
 * One sentence about how this agent's last terminal ended, or "" when there is nothing to add.
 *
 * FOUR ANSWERS, and they must not collapse into each other:
 *
 *   - a SIGNAL means something killed it. Named, because that is the answer.
 *   - a non-zero CODE means it fell over on its own. The number is the evidence.
 *   - code 0 means it exited cleanly, which under a session that should still be running is itself
 *     worth saying rather than leaving blank.
 *   - nothing recorded is a real answer too, and the honest one: an older bridge sends no exit fields
 *     at all, and inventing a cause for a death nobody described is the failure this whole column was
 *     added to end.
 *
 * `exitCode === 0` must print, so the test is for null/undefined rather than truthiness -- the same
 * trap the column, the model, the route and the bridge each had to avoid.
 */
export function deadConsoleCauseText(payload) {
  if (!payload || payload.live || !payload.historical) return '';
  const signal = String(payload.exitSignal || '').trim();
  if (signal) return `Killed by ${signal}.`;
  const code = payload.exitCode;
  if (code === null || code === undefined) return 'It did not report why it ended.';
  return Number(code) === 0 ? 'Exited cleanly (code 0).' : `Exited with code ${Number(code)}.`;
}

/**
 * The failure line the service extracted from the terminal's last output, trimmed for one line.
 *
 * Separate from the sentence above because they answer different questions -- HOW it ended versus
 * WHAT it was saying when it did -- and an operator wants both. Capped hard: this sits in a small
 * embed, and a 400-character stack trace would push the Start button off the card.
 */
export function deadConsoleFailureLine(payload, limit = 160) {
  const line = String(payload?.failureLine || '').replace(/\s+/g, ' ').trim();
  if (!line) return '';
  return line.length <= limit ? line : `${line.slice(0, limit - 1)}…`;
}

/**
 * Fill a placeholder with the cause, from the endpoint that already knows it.
 *
 * BEST EFFORT BY DESIGN: this decorates a card that is already correct without it. A failed fetch
 * leaves the placeholder empty rather than replacing a working message with an error -- the operator
 * came here to start the agent, not to read about the dashboard.
 */
export async function fillDeadConsoleCause(element, agentId, { api } = {}) {
  if (!element || !agentId || typeof api !== 'function') return '';
  let payload = null;
  try {
    payload = await api(`/agents/${encodeURIComponent(agentId)}/console?lines=1`);
  } catch {
    return '';
  }
  const cause = deadConsoleCauseText(payload);
  const line = deadConsoleFailureLine(payload);
  const text = [cause, line && `Last output: ${line}`].filter(Boolean).join(' ');
  if (text) element.textContent = ` ${text}`;
  return text;
}
