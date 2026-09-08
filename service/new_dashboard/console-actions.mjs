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
    // THE CONSOLE PROJECTION. This runs on every sequence gap. One live response, decomposed with
    // the server's own encoder and checked against its wire size: 147,250 bytes, of which the raw
    // tail is 93,430 (63.4%) and the event page 46,516 (31.6%) -- neither read here -- to write the
    // 6,442-byte snapshot two lines below. `view=console` returns what gets painted, and keeps the
    // tail whenever there is no snapshot to replace it, which is the fallback below.
    const data = await api(`/terminals/${encodeURIComponent(entry.terminalId)}?cols=${fetchCols}&rows=${entry.term.rows}&view=console`);
    // reset() (not clear()) wipes any scrambled scrollback/alt-screen state before we
    // repaint the clean server-rendered snapshot — so Refresh actually un-scrambles.
    entry.term.reset();
    applyRenderedWidth(entry, entry.term, entry.container, data, Boolean(entry.ownsPty));
    const snapshot = data?.terminal?.snapshot;
    entry.term.write(String(snapshot || data?.terminal?.output || ''));
    const snapshotSeq = Number(data?.terminal?.outputSeq ?? data?.terminal?.seq ?? entry.lastSeq);
    entry.lastSeq = Math.max(Number(entry.lastSeq) || -1, Number.isFinite(snapshotSeq) ? snapshotSeq : -1);
    drainHeldFrames(entry);
  } catch { /* keep current buffer */ }
  finally { entry.resyncing = false; }
}

// PLACED AFTER `resyncActiveConsole`, NOT BEFORE IT, and that is a constraint rather than a
// preference. `extraction-proof` reconstructs app.js from this module and requires the lines LEADING
// an extracted declaration to be comments or blanks -- a function body ending in `}` immediately
// above one would silently absorb it, and the gate says so by name. Declarations hoist, so the call
// site above reads fine.
/**
 * Paint the frames the socket held while this recovery was in flight, and resume from them.
 *
 * WITHOUT THIS THE RECOVERY LOOPS. The snapshot carries the buffer as it stood when the SERVER
 * answered; frames past that arrived during the fetch. Resuming from the snapshot's sequence leaves
 * the console behind the stream, so the next live frame is another gap, another fetch, another
 * window in which nothing paints. Reproduced against the real socket and this function in
 * `the-console-does-not-stall-while-it-resyncs.test.mjs`.
 *
 * ALREADY-COVERED FRAMES ARE DISCARDED rather than replayed. The snapshot is a RENDERED SCREEN, so a
 * frame at or below its sequence is already in the picture; writing it again would paint bytes twice
 * -- which for a TUI is not a duplicate line, it is a cursor somewhere nobody asked for.
 *
 * AN OVERFLOWED QUEUE REPLAYS NOTHING. Whatever was dropped is genuinely lost to this console, and
 * pretending otherwise by painting the tail would put the screen out of order. Resuming from the
 * snapshot alone is what happened before frames were held at all, so the fallback is never worse
 * than the behaviour it replaced -- one more gap, one more recovery, and it settles.
 */
function drainHeldFrames(entry) {
  const held = Array.isArray(entry.pendingFrames) ? entry.pendingFrames : [];
  entry.pendingFrames = [];
  if (entry.pendingOverflowed) { entry.pendingOverflowed = false; return; }
  const replay = held
    .filter((f) => Number.isFinite(f?.seq) && f.seq > entry.lastSeq)
    .sort((a, b) => a.seq - b.seq);
  for (const f of replay) {
    try { entry.term.write(f.output); } catch { break; }
    entry.lastSeq = f.seq;
  }
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
