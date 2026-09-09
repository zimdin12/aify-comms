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
import { cursorFromSnapshot, drainHeldFrames } from './console-cursor.mjs';
import { sessionAgentId, sessionId } from './record-fields.mjs';
import { sessionForRun } from './run-inspector-controls.mjs';
import { state } from './state.mjs';
import { forceTerminalRepaint } from './terminal-input.mjs';
import { applyRenderedWidth } from './terminal-width.mjs';
import { toast, uiConfirm } from './ui.js';
import { awaitTerminalSize, disposeActiveXterm } from './xterm-lifecycle.mjs';

//: How many fetches ONE recovery may take before it gives the held frames up. Three, because the
//: frames a second snapshot cannot cover a third will not either, and an unbounded retry is the
//: fan-out loop `entry.resyncing` exists to prevent wearing a different hat.
const MAX_RESYNC_PASSES = 3;

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
  //: Whether the drain left held frames it could not place. Declared out here because the decision
  //: it drives has to happen AFTER the `finally` clears `resyncing`, or the retry refuses itself.
  let unresolved = false;
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
    // WHERE THE CONSOLE IS AFTER THIS SNAPSHOT, read by the module that owns the question --
    // `console-cursor.mjs` -- because the MOUNT asks it too and had the uncorrected copy.
    const snapshotSeq = cursorFromSnapshot(data?.terminal, entry.lastSeq);
    // THE SEQUENCE DESCRIBES THE SCREEN, AND THE SCREEN WAS JUST RESET TO THE SNAPSHOT.
    //
    // THIS WAS A `Math.max` AND THAT LOST OUTPUT, found by the whole-diff review 2026-09-08. The
    // socket paints contiguous frames straight through while a recovery is in flight, so a manual
    // resync from 4 with 5 and 6 arriving painted both and left `lastSeq` at 6. Then the fetch
    // answered with a snapshot at 4, `reset()` wiped the screen, and the max kept the sequence at 6
    // -- so the screen showed the 4-snapshot while the bookkeeping claimed 6, and the retransmitted
    // 5 and 6 the reset made necessary were refused as already covered. Two frames gone, silently.
    //
    // A snapshot is a WHOLE SCREEN, so after painting it the console is exactly where the snapshot
    // says and nowhere else. Moving the sequence BACKWARDS is the correct answer when the picture
    // moved backwards; anything still missing arrives as a gap and recovers.
    if (Number.isFinite(snapshotSeq)) entry.lastSeq = snapshotSeq;
    unresolved = drainHeldFrames(entry);
  } catch { /* keep current buffer */ }
  finally { entry.resyncing = false; }

  // A SECOND FETCH, NOT A WAIT FOR ANOTHER FRAME. The drain stops at the first gap it cannot cross
  // and keeps the rest; if it kept anything, this recovery has NOT finished. Leaving it to the next
  // arriving frame to notice is exactly the case review named -- a snapshot at 5 with 9 and 10 held,
  // then silence -- where a quiet agent means no next frame and the held output is stranded on a
  // console that believes it is live.
  if (!unresolved) { entry.resyncPasses = 0; return; }
  entry.resyncPasses = (Number(entry.resyncPasses) || 0) + 1;
  if (entry.resyncPasses >= MAX_RESYNC_PASSES) {
    // BOUNDED, and the exhaustion is the same fallback an overflow takes: drop what cannot be
    // placed and leave the sequence where the screen really ends, so the next live frame reads as
    // the gap it is. That is the behaviour from before frames were held at all, never worse.
    entry.pendingFrames = [];
    entry.resyncPasses = 0;
    return;
  }
  await resyncActiveConsole();
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
