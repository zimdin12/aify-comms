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
    const snapshotSeq = Number(data?.terminal?.outputSeq ?? data?.terminal?.seq ?? entry.lastSeq);
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
 *
 * @returns {boolean} whether frames are still held that this pass could not place, which is the
 *   caller's signal that the recovery is UNRESOLVED and owes another fetch.
 */
function drainHeldFrames(entry) {
  const held = Array.isArray(entry.pendingFrames) ? entry.pendingFrames : [];
  entry.pendingFrames = [];
  if (entry.pendingOverflowed) { entry.pendingOverflowed = false; return false; }
  const replay = held
    .filter((f) => Number.isFinite(f?.seq) && f.seq > entry.lastSeq)
    .sort((a, b) => a.seq - b.seq);
  // ONLY AN ADJACENT RUN, and this is the whole correctness of the replay.
  //
  // THE DEFECT THIS CLOSES, found by the whole-diff review 2026-09-08, and it is the worst shape a
  // console defect can take: silent, permanent, and self-concealing. The filter above admits every
  // held frame ABOVE the floor, so a snapshot at 5 with 9 and 10 held painted both and advanced
  // `lastSeq` to 10 -- while 6, 7 and 8 had never been painted. Each of them then arrives with
  // `seq <= lastSeq` and is discarded as already covered, and frame 11 looks adjacent to 10, so no
  // gap is ever detected and no second recovery happens. The screen is missing three frames for the
  // life of the console, and nothing anywhere reports it.
  //
  // SO THE SEQUENCE MAY ONLY ADVANCE OVER BYTES THAT WERE ACTUALLY WRITTEN. A held frame that does
  // not continue the picture stops the replay and leaves `lastSeq` where the screen really ends --
  // the next arriving frame then reads as the gap it is and recovers, which is one more round trip
  // and a correct console instead of a fast wrong one.
  //
  // A DUPLICATE IS WRITTEN ONCE. The socket holds whatever arrived, retransmits included, and for a
  // TUI painting the same bytes twice is not a doubled line -- it is a cursor somewhere nobody asked
  // for.
  //
  // AND WHAT IT COULD NOT PLACE IS KEPT, which the first version of this fix destroyed. It emptied
  // `pendingFrames` at the top and then broke out of the loop, so the un-replayed tail was gone --
  // review's case is a snapshot at 5 with 9 and 10 held: the break was correct, dropping 9 and 10
  // was not, and the caller then marked the recovery finished. On a quiet agent no further frame
  // ever arrives to notice, so the console sits believing it is live with output it was handed and
  // threw away. The remainder stays queued and the caller fetches again.
  let index = 0;
  for (; index < replay.length; index += 1) {
    const f = replay[index];
    if (f.seq <= entry.lastSeq) continue;
    if (f.seq !== entry.lastSeq + 1) break;
    try { entry.term.write(f.output); } catch { break; }
    entry.lastSeq = f.seq;
  }
  const remainder = replay.slice(index).filter((f) => f.seq > entry.lastSeq);
  entry.pendingFrames = remainder;
  return remainder.length > 0;
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
