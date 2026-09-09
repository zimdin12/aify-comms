// A console mount erases the frames that arrive while it is fetching the snapshot it paints over them.
//
// FOUND BY REVIEW 2026-09-09, driven through the real public mount and the real socket. The mount
// publishes its entry into `state.activeXterm` BEFORE three awaits -- the double rAF, the snapshot
// GET, and the 700ms resize settle with its second GET -- so `realtime-socket.mjs` paints every
// frame arriving in that window straight into this terminal and advances `lastSeq`. The mount then
// calls `term.reset()` and writes the OLDER snapshot over the top.
//
// TWO LOSSES, AND THE SECOND IS WHY THIS IS PERMANENT. The bytes are gone from the screen; and the
// cursor still says they were painted, so a retransmission would be refused as already consumed --
// and on a quiet stream nothing retransmits anyway. Review's trace: quiet null mount stays -1,
// frame 4 arrives during the GET and advances the cursor to 4, the reset wipes it, and the response
// carrying `outputSeq: null` leaves 4 in place because `outputSeq ?? seq ?? lastSeq` selects the
// FALLBACK on null rather than reading it as a value.
//
// THE REPAIR IS THE ONE THE RECOVERY ALREADY USES. A mount is a recovery in flight: it declares
// itself one with `resyncing`, the socket holds arriving frames instead of painting them, and
// `drainHeldFrames` places them against whatever the snapshot turned out to cover. Nothing new is
// invented here -- the point of the finding is that two code paths were doing the same thing and
// only one of them had been corrected.
//
// WHICH HALF IS LOAD-BEARING, MEASURED RATHER THAN ASSUMED, and the answer is not the one the
// finding suggests. Reverting the cursor read to `outputSeq ?? seq ?? lastSeq` reddens the
// RECOVERY's test and NOTHING here -- because with the frames held, `lastSeq` is still -1 when the
// snapshot lands, so the fallback and the value agree. The hold is what saves the frame; the
// explicit UNKNOWN is correctness the mount no longer depends on, kept because the two paths must
// read one sequence the same way and because a future edit could put a number there again. Nobody
// should read its presence here as covered behaviour: `console-actions.test.mjs` is what covers it.
//
// THIS DRIVES THE REAL FUNCTIONS. The mount, the socket's real event handler and the real drain,
// with a snapshot fetch whose timing the test controls. A test that read the source for a flag
// would pass on a comment and would say nothing about the ORDER the awaits resolve in, which is the
// entire defect.

import assert from "node:assert/strict";
import test from "node:test";

import { applyRealtimeEvent } from "./realtime-socket.mjs";
import { mountXtermForTerminal } from "./xterm-mount.mjs";
import { state } from "./state.mjs";

//: WRITTEN INTO THE TRANSCRIPT BY `reset()`, so an assertion can say WHERE the wipe happened rather
//: than only what survived it. A frame recorded before this marker was erased by the reset; the same
//: frame recorded after it is on the screen the operator sees.
const RESET = "<<reset>>";

/** Everything `mountXtermForTerminal` touches on a terminal, and nothing it does not. */
function fakeTerm() {
  const written = [];
  return {
    cols: 80, rows: 24, written,
    buffer: { active: { length: 0, cursorY: 0, getLine: () => null } },
    parser: { registerCsiHandler: () => {} },
    unicode: { activeVersion: "6" },
    textarea: { addEventListener() {}, focus() {} },
    element: { addEventListener() {} },
    loadAddon() {}, open() {}, focus() {}, dispose() {},
    reset() { written.push(RESET); },
    write(chunk) { written.push(String(chunk)); },
    paste() {}, getSelection: () => "", hasSelection: () => false,
    attachCustomKeyEventHandler() {},
    onData: () => ({ dispose() {} }),
    onResize: () => ({ dispose() {} }),
  };
}

function node() {
  return {
    className: "", textContent: "", innerHTML: "", style: {}, dataset: {},
    isConnected: true, offsetParent: {}, clientWidth: 800, clientHeight: 600,
    children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, removeAttribute() {}, appendChild() {}, remove() {},
    addEventListener() {}, removeEventListener() {},
    getBoundingClientRect: () => ({ width: 800, height: 600 }),
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => null,
  };
}

/**
 * Install a browser enough for the mount to run, with a snapshot fetch this test releases by hand.
 *
 * SEALED, so nothing passes because the host supplied it: every global is saved and removed again,
 * and `state`'s three console fields with them.
 *
 * THE TERMINAL IS RESIDENT. A managed one nudges the PTY size and waits for the acknowledgement,
 * which adds a second GET this test would then have to arbitrate. The window under test is the
 * FIRST snapshot fetch, and a resident console reaches it by the shortest path.
 */
function withBrowser(run) {
  const saved = {};
  for (const k of ["location", "window", "document", "fetch", "requestAnimationFrame",
    "ResizeObserver", "localStorage"]) {
    saved[k] = { had: k in globalThis, value: globalThis[k] };
  }
  const savedXterm = state.activeXterm;
  const savedAgents = state.agents;
  const savedSessions = state.sessions;
  const savedOwners = state.terminalOwners;
  state.sessions = [];
  state.agents = [{ id: "a-1", sessionMode: "resident", terminalId: "t-1" }];
  state.terminalOwners = new Map();

  let releaseSnapshot;
  const snapshotGate = new Promise((resolve) => { releaseSnapshot = resolve; });
  //: THE SERVER SAYING IT DOES NOT KNOW where the screen is, which is the case review drove. A test
  //: that only ever used a number could not detect a failed KNOWN-to-unknown transition -- the
  //: mistake the previous round's test made.
  let snapshotSeq = null;

  globalThis.location = { search: "", protocol: "http:", hostname: "127.0.0.1", origin: "http://127.0.0.1:8801", href: "http://127.0.0.1:8801/" };
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  globalThis.window = {
    Terminal: function () { return fakeTerm(); },
    FitAddon: { FitAddon: class { fit() {} proposeDimensions() { return { cols: 80, rows: 24 }; } dispose() {} } },
    WebLinksAddon: { WebLinksAddon: class { dispose() {} } },
    WebglAddon: { WebglAddon: class { onContextLoss() { return { dispose() {} }; } dispose() {} } },
    Unicode: { Unicode11Addon: class { dispose() {} } },
    ResizeObserver: globalThis.ResizeObserver,
    addEventListener() {}, removeEventListener() {},
  };
  globalThis.window.FitAddon.FitAddon.prototype.activate = () => {};
  globalThis.document = {
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
    createElement: () => node(), body: node(), addEventListener() {}, removeEventListener() {},
    activeElement: null,
  };
  const gets = [];
  globalThis.fetch = async (url) => {
    gets.push(String(url));
    await snapshotGate;
    return {
      ok: true, status: 200,
      text: async () => JSON.stringify({
        ok: true,
        terminal: {
          id: "t-1", snapshot: "SNAPSHOT", output: "SNAPSHOT", outputSeq: snapshotSeq,
          cols: 80, rows: 24, status: "running",
        },
      }),
    };
  };

  const restore = () => {
    state.activeXterm = savedXterm;
    state.agents = savedAgents;
    state.sessions = savedSessions;
    state.terminalOwners = savedOwners;
    for (const [k, s] of Object.entries(saved)) {
      if (s.had) globalThis[k] = s.value; else delete globalThis[k];
    }
  };
  return Promise.resolve()
    .then(() => run({
      releaseSnapshot: () => releaseSnapshot(),
      setSnapshotSeq: (n) => { snapshotSeq = n; },
      gets,
    }))
    .finally(restore);
}

const settle = () => new Promise((r) => setTimeout(r, 30));

/** Deliver one frame exactly as the WebSocket would. */
function frame(seq, output) {
  applyRealtimeEvent("terminal_output", { terminalId: "t-1", agentId: "a-1", seq, output });
}

test("POSITIVE CONTROL: an undisturbed mount paints its snapshot and then live frames", async () => {
  // Without this, every assertion below would also pass against a mount that painted NOTHING, and
  // against one whose held frames were never released.
  await withBrowser(async ({ releaseSnapshot, setSnapshotSeq }) => {
    setSnapshotSeq(3);
    const mounting = mountXtermForTerminal("t-1", "a-1", node(), {}, { resyncActiveConsole: async () => {} });
    releaseSnapshot();
    await mounting;
    await settle();
    frame(4, "AFTER");
    const written = state.activeXterm.term.written;
    assert.deepEqual(written, [RESET, "SNAPSHOT", "AFTER"]);
    assert.equal(state.activeXterm.lastSeq, 4);
    assert.equal(state.activeXterm.resyncing, false, "the mount left the socket holding every frame");
  });
});

test("A FRAME THAT ARRIVES DURING THE SNAPSHOT FETCH SURVIVES THE RESET", async () => {
  await withBrowser(async ({ releaseSnapshot, setSnapshotSeq }) => {
    setSnapshotSeq(3);
    const mounting = mountXtermForTerminal("t-1", "a-1", node(), {}, { resyncActiveConsole: async () => {} });
    await settle();                       // parked on the snapshot GET, entry already published

    frame(4, "DURING");
    releaseSnapshot();
    await mounting;
    await settle();

    const written = state.activeXterm.term.written;
    // ORDER IS THE ASSERTION. Painting DURING before the reset is exactly the defect: the bytes are
    // on the screen for a moment and then wiped by the snapshot that follows.
    assert.deepEqual(written, [RESET, "SNAPSHOT", "DURING"],
      `the frame did not survive the snapshot repaint: ${JSON.stringify(written)}`);
    assert.equal(state.activeXterm.lastSeq, 4, "the console did not resume from the frame it kept");
  });
});

test("AND AN UNKNOWN SNAPSHOT SEQUENCE SEEDS FROM THE FRAME RATHER THAN DISCARDING IT", async () => {
  // Review's own case: the server answers `outputSeq: null`, which is UNKNOWN. The old chain read
  // that as "ask somebody else" and kept whatever the frame had just set, so the cursor claimed a
  // position the reset had erased. Read as a value it is -1, and an unknown position cannot be
  // adjacent to anything -- so the drain seeds from the lowest held frame and adopts its number.
  await withBrowser(async ({ releaseSnapshot }) => {
    const mounting = mountXtermForTerminal("t-1", "a-1", node(), {}, { resyncActiveConsole: async () => {} });
    await settle();

    frame(4, "DURING");
    releaseSnapshot();
    await mounting;
    await settle();

    const written = state.activeXterm.term.written;
    assert.deepEqual(written, [RESET, "SNAPSHOT", "DURING"],
      `an unknown snapshot sequence lost the held frame: ${JSON.stringify(written)}`);
    assert.equal(state.activeXterm.lastSeq, 4);
  });
});

test("A FRAME THE SNAPSHOT ALREADY COVERS IS NOT PAINTED TWICE", async () => {
  // NEGATIVE CONTROL for the hold. Holding frames would be a defect of its own if the drain then
  // replayed bytes the snapshot already carries: for a TUI that is not a duplicated line, it is a
  // cursor somewhere nobody asked for.
  await withBrowser(async ({ releaseSnapshot, setSnapshotSeq }) => {
    setSnapshotSeq(7);
    const mounting = mountXtermForTerminal("t-1", "a-1", node(), {}, { resyncActiveConsole: async () => {} });
    await settle();

    frame(5, "ALREADY-IN-THE-SNAPSHOT");
    releaseSnapshot();
    await mounting;
    await settle();

    const written = state.activeXterm.term.written;
    assert.deepEqual(written, [RESET, "SNAPSHOT"],
      `a frame at or below the snapshot was replayed: ${JSON.stringify(written)}`);
    assert.equal(state.activeXterm.lastSeq, 7);
  });
});

test("A HELD FRAME THE SNAPSHOT CANNOT REACH ASKS THE RECOVERY TO FETCH AGAIN", async () => {
  // The drain places only an ADJACENT run, so a snapshot at 3 with frame 9 held leaves 9 queued.
  // Left there, a quiet agent means nothing ever arrives to notice it -- so the mount owes the same
  // second fetch a recovery owes, and asks the recovery rather than reimplementing one.
  await withBrowser(async ({ releaseSnapshot, setSnapshotSeq }) => {
    setSnapshotSeq(3);
    let asked = 0;
    //: WHAT THE REAL RECOVERY WOULD SEE. `resyncActiveConsole` opens with `if (entry.resyncing)
    //: return;` -- its own fan-out guard -- so asking it while the mount still holds the flag is a
    //: call that returns immediately and a console that waits for ever. The order is the assertion,
    //: and a fake that did not record this would pass against either order.
    let armedWhenAsked = null;
    const mounting = mountXtermForTerminal("t-1", "a-1", node(), {}, {
      resyncActiveConsole: async () => {
        asked += 1;
        armedWhenAsked = state.activeXterm?.resyncing;
      },
    });
    await settle();

    frame(9, "TOO-FAR-AHEAD");
    releaseSnapshot();
    await mounting;
    await settle();

    assert.equal(asked, 1, "the unplaceable frame was left queued with nobody coming back for it");
    assert.equal(armedWhenAsked, false,
      "the recovery was asked while the mount still held `resyncing`, so its own guard refuses it");
    assert.deepEqual(state.activeXterm.term.written, [RESET, "SNAPSHOT"]);
    assert.equal(state.activeXterm.lastSeq, 3, "the cursor advanced over bytes that were never written");
    assert.deepEqual(state.activeXterm.pendingFrames.map((f) => f.seq), [9],
      "the frame the drain could not place was thrown away");
  });
});
