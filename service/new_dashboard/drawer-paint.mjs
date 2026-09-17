// Painting the agent drawer without making it blink.
//
// OPERATOR-REPORTED 2026-09-17: the details drawer of an agent that kept failing to start flickered
// fast while open. Every data refresh re-renders the open drawer (app.js `refreshOpenInspector`), and
// the render rewrote the whole drawer with its Processes panel reset to "Reading this agent's
// terminals..." before fetching them again. An agent retrying its start every couple of seconds made
// the service report a change every couple of seconds, so the panel alternated placeholder and content.
//
// Two rules close it:
//   * A render identical to what the drawer already shows for the same agent writes nothing.
//   * A render that does change keeps the panel the async read fills, so what was there stays on
//     screen until the fresh read replaces it, instead of blinking back to the placeholder.

const painted = new WeakMap();

/**
 * Write `html` into `host` as the drawer for `agentId`. Returns whether anything was written.
 *
 * @param {Element} host             the drawer content element
 * @param {string} agentId
 * @param {string} html
 * @param {string} keepId            the id of the panel an async read fills after the paint
 */
export function paintAgentDrawer(host, agentId, html, keepId) {
  if (!host) return false;
  const last = painted.get(host);
  // Still OUR paint only if the root we wrote is still there: the run and message drawers write into
  // the same element, and after one of them an identical agent render must paint again.
  const sameAgent = last && last.agentId === agentId && last.root && host.firstElementChild === last.root;
  if (sameAgent && last.html === html) return false;
  const kept = sameAgent && keepId ? host.querySelector(`#${keepId}`)?.innerHTML : undefined;
  host.innerHTML = html;
  if (kept !== undefined) {
    const panel = host.querySelector(`#${keepId}`);
    if (panel) panel.innerHTML = kept;
  }
  painted.set(host, { agentId, html, root: host.firstElementChild });
  return true;
}

const panelHtml = new WeakMap();

/**
 * Write `html` into `panel` only if it differs from what this function last wrote there and the panel
 * still holds it. Compared against the string written, not `innerHTML`: a browser re-serialises markup,
 * so the two rarely match even when nothing changed. Returns whether it wrote.
 */
export function paintIfChanged(panel, html) {
  if (!panel) return false;
  const last = panelHtml.get(panel);
  if (last && last.html === html && panel.firstElementChild === last.root) return false;
  panel.innerHTML = html;
  panelHtml.set(panel, { html, root: panel.firstElementChild });
  return true;
}
