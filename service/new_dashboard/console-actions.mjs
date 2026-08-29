// Console actions: resync, stop, start, and the jump from a run to the console that ran it.
//
// The operator watches a managed agent through this pane, so the failure that matters is a console
// that LOOKS live and is not. `resyncActiveConsole` is the recovery for exactly that — it re-fetches
// the authoritative buffer and repaints — and it is reached from three places that each mean something
// different: the Refresh button, a detected sequence gap in the live stream, and a WebSocket
// reconnect. Its sequence bookkeeping is what stops the repaint from being undone by frames that were
// already painted.
//
// Four injected names, each of which reaches `refresh`.

import { api } from './api-client.mjs';
import { sessionAgentId, sessionId } from './record-fields.mjs';
import { sessionForRun } from './run-inspector-controls.mjs';
import { state } from './state.mjs';
import { forceTerminalRepaint } from './terminal-input.mjs';
import { applyRenderedWidth } from './terminal-width.mjs';
import { toast, uiConfirm } from './ui.js';
import { awaitTerminalSize, disposeActiveXterm } from './xterm-lifecycle.mjs';

let closeInspector = () => {};
let refresh = async () => {};
let refreshSoon = () => {};
let setPage = () => {};

/** Supply the app.js-side dependencies. Throws on a partial bag. */
export function initConsoleActions(deps) {
  const REQUIRED = ['closeInspector', 'refresh', 'refreshSoon', 'setPage'];
  const missing = REQUIRED.filter((k) => typeof deps?.[k] !== 'function');
  if (missing.length) throw new TypeError(`initConsoleActions requires ${missing.join(', ')}`);
  ({ closeInspector, refresh, refreshSoon, setPage } = deps);
}


// Re-fetch the authoritative buffer and repaint (used by the Refresh button and on a
// detected seq gap, mirroring the old dashboard's resync path).
export async function resyncActiveConsole({ forceRepaint = false } = {}) {
  const entry = state.activeXterm;
  if (!entry || !entry.term) return;
  // A PTY resize produces a burst of repaint frames. Those frames can themselves
  // expose a transient seq gap, which used to start another resync and another
  // -1/+1 resize pair. Coalesce recovery so one gap cannot fan out into the
  // observed 153↔154-cols resize/flicker loop.
  if (entry.resyncing) return;
  entry.resyncing = true;
  try {
    // Fetch at the pane's FITTED width (not the possibly-widened current width) so the server
    // can re-infer the source width and hand back the correct renderedCols.
    const fetchCols = Math.max(20, entry.fitCols || entry.term.cols);
    // REFRESH MUST ACTUALLY FIX IT. The operator's complaint was "refresh does not actually fix
    // it" — and they were right: it re-rendered the SAME poisoned screen. These TUIs repaint only
    // what changed, so a screen with wrong rows keeps them forever; the ONLY thing that forces a
    // full repaint is a genuine PTY resize (verified live). So Refresh now nudges the size (-1
    // col, then back), which makes the app redraw everything, and THEN pulls the clean snapshot.
    if (forceRepaint && entry.ownsPty) {
      try {
        await forceTerminalRepaint({
          cols: fetchCols,
          rows: entry.term.rows,
          resize: (nextCols, nextRows) => api(`/terminals/${encodeURIComponent(entry.terminalId)}/resize`, {
            method: 'POST',
            body: JSON.stringify({ cols: nextCols, rows: nextRows, requestedBy: 'dashboard-refresh' }),
          }),
          waitForSize: (nextCols, nextRows) => awaitTerminalSize(entry.terminalId, nextCols, nextRows),
        });
        await new Promise((res) => setTimeout(res, 700));
      } catch { /* best-effort */ }
    }
    const data = await api(`/terminals/${encodeURIComponent(entry.terminalId)}?cols=${fetchCols}&rows=${entry.term.rows}`);
    // reset() (not clear()) wipes any scrambled scrollback/alt-screen state before we
    // repaint the clean server-rendered snapshot — so Refresh actually un-scrambles.
    entry.term.reset();
    applyRenderedWidth(entry, entry.term, entry.container, data, Boolean(entry.ownsPty));
    const snapshot = data?.terminal?.snapshot;
    entry.term.write(String(snapshot || data?.terminal?.output || ''));
    const snapshotSeq = Number(data?.terminal?.outputSeq ?? data?.terminal?.seq ?? entry.lastSeq);
    entry.lastSeq = Math.max(Number(entry.lastSeq) || -1, Number.isFinite(snapshotSeq) ? snapshotSeq : -1);
  } catch { /* keep current buffer */ }
  finally { entry.resyncing = false; }
}

export async function stopConsoleTerminal(terminalId) {
  if (!terminalId) return;
  if (!await uiConfirm('Stop this terminal? The agent returns to messenger ownership.', { tone: 'danger' })) return;
  try {
    await api(`/terminals/${encodeURIComponent(terminalId)}/stop`, { method: 'POST', body: JSON.stringify({ requestedBy: 'dashboard', body: '' }) });
    disposeActiveXterm();
    toast('Console stopped', 'ok');
    refreshSoon();
  } catch (err) { toast(`Stop failed: ${err?.message || err}`, 'error'); }
}

export async function startConsoleForSession(sessionId, freshContext = false) {
  if (!sessionId) return;
  try {
    await api(`/sessions/${encodeURIComponent(sessionId)}/console/start`, { method: 'POST', body: JSON.stringify({ requestedBy: 'dashboard', freshContext }) });
    toast(freshContext ? 'Starting fresh console…' : 'Starting console…', 'ok');
    refreshSoon();
  } catch (err) { toast(`Start console failed: ${err?.message || err}`, 'error'); }
}

export function openRunConsole(run) {
  const session = sessionForRun(run);
  if (!session) return;
  state.selectedSessionId = sessionId(session);
  state.selectedConversation = sessionAgentId(session) || 'dashboard';
  state.selectedSessionTab = 'console';
  setPage('sessions');
  renderSessionWorkspace();
  closeInspector();
}
