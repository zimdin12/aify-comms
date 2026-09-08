// What the browser console does between noticing a sequence gap and finishing its recovery.
//
// THIS IS B_TRANSPORT'S REMAINING LEG. The operator reported "our browser terminal kind of lags
// sometimes". The two SERVER legs were measured and are fast; what was never examined is the
// browser's own recovery path, and the standing suspect was the gap-triggered resync in
// `realtime-socket.mjs` -- a full HTTP refetch plus `term.reset()` plus a whole-screen repaint.
//
// THE MECHANISM, read out of the code before any of these tests existed:
//
//   if (seq > entry.lastSeq + 1) { resyncActiveConsole().catch(() => {}); return; }
//   if (Number.isFinite(seq)) entry.lastSeq = seq;
//
// `lastSeq` is only advanced on the PAINTING path. So for as long as the resync is in flight -- an
// HTTP round trip -- every arriving frame still sees a gap, calls a resync that is already running,
// and RETURNS. The bytes are dropped. That is intended in the sense that the snapshot will carry
// them, and it is also a window during which the terminal shows nothing new.
//
// THE PART THAT IS NOT INTENDED is what `lastSeq` becomes afterwards. The resync sets it to the
// snapshot's `outputSeq` -- the sequence the buffer had when the SERVER answered -- while frames past
// that number arrived and were discarded. The next live frame is therefore ANOTHER gap, and on a
// continuously producing agent the console can enter a recovery loop: fetch, drop, gap, fetch. Each
// iteration costs a round trip and paints only snapshots.
//
// PASSES IN TESTS, NOT PROVEN IN A BROWSER. These drive the real modules with a stubbed `fetch` and
// a fake xterm; nothing here observes an actual browser painting. What they establish is that the
// bookkeeping permits the loop, which is the part a live repro would otherwise have to be lucky to
// catch -- "sometimes" is exactly the shape a bounded observation misses.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { initConsoleActions, resyncActiveConsole } from "./console-actions.mjs";
import { applyRealtimeEvent, initRealtimeSocket } from "./realtime-socket.mjs";
import { state } from "./state.mjs";

/** A console mounted on a visible pane, with a term that records what it was asked to paint. */
function mountedConsole({ lastSeq = 4 } = {}) {
  const painted = [];
  const entry = {
    terminalId: "t1",
    lastSeq,
    fitCols: 80,
    ownsPty: false,
    // `classList` IS LOAD-BEARING for this double: `applyRenderedWidth` calls it between the reset
    // and the snapshot write, and `resyncActiveConsole` swallows what it throws -- so a container
    // without it leaves the console RESET AND EMPTY and made this file's own first run misread a
    // harness gap as a product defect.
    container: { offsetParent: {}, classList: { add() {}, remove() {}, contains: () => false } },
    term: {
      cols: 80,
      rows: 24,
      write: (s) => painted.push(String(s)),
      reset: () => painted.push("<reset>"),
      resize: () => {},
    },
  };
  state.activeXterm = entry;
  state.terminalOwners = new Map([["t1", "a1"]]);
  return { entry, painted };
}

/**
 * Wire both modules for real, with `fetch` under this test's control.
 *
 * THE RESYNC IS THE REAL ONE. Injecting a stub would test the socket's decision to call it and
 * nothing about the bookkeeping that follows, which is where the loop lives.
 */
function withRealResync(run, { snapshotSeq = 5, snapshot = "SNAPSHOT", delayMs = 0 } = {}) {
  const saved = { fetch: globalThis.fetch, document: globalThis.document };
  const fetches = [];
  globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  globalThis.fetch = async (url) => {
    fetches.push(String(url));
    if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
    // `text()`, NOT `json()`. `api-client.mjs` reads the body as text and parses it itself, and a
    // double that stubbed `json()` returned an EMPTY object through the real helper -- so the resync
    // reset the screen, wrote "", left the sequence untouched, and this file's first run read that
    // as the product looping. Stub the method the code actually calls.
    const body = JSON.stringify({ terminal: { snapshot, outputSeq: snapshotSeq, renderedCols: 80 } });
    return { ok: true, status: 200, text: async () => body, json: async () => JSON.parse(body) };
  };
  setApiBase("");
  initConsoleActions({ closeInspector() {}, refresh: async () => {}, refreshSoon() {}, setPage() {} });
  initRealtimeSocket({
    dashboardNotifier: { handle() {} },
    evaluateFlowGates() {},
    refreshSoon() {},
    resyncActiveConsole,
    scheduleRenderAll() {},
  });
  return Promise.resolve(run({ fetches })).finally(() => {
    globalThis.fetch = saved.fetch;
    globalThis.document = saved.document;
  });
}

const frame = (seq, output) => applyRealtimeEvent("terminal_output",
  { terminalId: "t1", agentId: "a1", output, seq });

test("POSITIVE CONTROL: an in-order frame paints and advances the sequence", async () => {
  // Every assertion below is about bytes NOT being painted. Without this, a socket that painted
  // nothing at all would satisfy the whole file.
  await withRealResync(() => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(5, "hello");
    assert.deepEqual(painted, ["hello"]);
    assert.equal(entry.lastSeq, 5);
  });
});

test("FRAMES ARRIVING DURING A RECOVERY ARE HELD, not painted out of order", async () => {
  // They cannot be painted yet: the snapshot has not landed, so writing them would put bytes on a
  // screen that is about to be reset. Holding is what makes the replay below possible -- they used
  // to be DROPPED, which is what turned one stall into a loop.
  await withRealResync(async ({ fetches }) => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(9, "gap");                       // starts the recovery
    assert.deepEqual(painted, [], "the gapped frame itself must not paint out of order");

    for (const seq of [10, 11, 12]) frame(seq, `live-${seq}`);
    assert.deepEqual(painted, [],
      `frames arriving during the recovery were painted early: ${JSON.stringify(painted)}`);
    assert.equal(entry.lastSeq, 4, "the sequence advanced while nothing had been painted");
    assert.equal(entry.pendingFrames.length, 4, "the arriving frames were not held");

    await new Promise((r) => setTimeout(r, 40));
    // ONE fetch, not four: `entry.resyncing` coalesces the re-entrant calls.
    assert.equal(fetches.length, 1, `the recovery fanned out into ${fetches.length} fetches`);
  }, { delayMs: 15 });
});

test("AND THEY ARE REPLAYED AFTER THE SNAPSHOT, so the next live frame is in sequence", async () => {
  // THE LOOP THIS CLOSES. The snapshot carries the buffer as the SERVER had it; frames past that
  // arrived while the fetch was in flight. Resuming from the snapshot's sequence alone left the
  // console behind the stream, so the very next frame gapped again -- fetch, drop, gap, fetch, on a
  // continuously producing agent, painting only snapshots and showing nothing in between.
  await withRealResync(async ({ fetches }) => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(9, "gap");
    for (const seq of [10, 11, 12]) frame(seq, `live-${seq}`);
    await new Promise((r) => setTimeout(r, 40));

    assert.ok(painted.includes("<reset>"), "the recovery did not repaint the screen");
    assert.ok(painted.includes("SNAPSHOT"), "the recovery did not write the snapshot");
    // IN ORDER, AND AFTER the snapshot -- a replay before it would be wiped by the reset.
    const afterSnapshot = painted.slice(painted.indexOf("SNAPSHOT") + 1);
    assert.deepEqual(afterSnapshot, ["gap", "live-10", "live-11", "live-12"],
      `the held frames were not replayed in order: ${JSON.stringify(afterSnapshot)}`);
    assert.equal(entry.lastSeq, 12, "the console resumed behind the frames it had just painted");

    frame(13, "after");
    assert.ok(painted.includes("after"), "the next live frame did not paint");
    assert.equal(fetches.length, 1,
      `the next live frame started ${fetches.length - 1} further recoveries`);
  }, { snapshotSeq: 5, delayMs: 15 });
});

test("FRAMES THE SNAPSHOT ALREADY COVERS ARE DISCARDED, not painted twice", async () => {
  // The snapshot is a RENDERED SCREEN. Replaying a frame at or below its sequence does not append a
  // duplicate line -- it moves a cursor somewhere nobody asked for, which is the class of corruption
  // this whole feature exists to avoid.
  await withRealResync(async () => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(9, "gap");
    for (const seq of [10, 11]) frame(seq, `live-${seq}`);
    await new Promise((r) => setTimeout(r, 40));

    const afterSnapshot = painted.slice(painted.indexOf("SNAPSHOT") + 1);
    assert.deepEqual(afterSnapshot, ["live-11"],
      `frames already inside the snapshot were replayed: ${JSON.stringify(afterSnapshot)}`);
    assert.equal(entry.lastSeq, 11);
  }, { snapshotSeq: 10, delayMs: 15 });
});

test("AN OVERFLOWED QUEUE REPLAYS NOTHING and resumes from the snapshot alone", async () => {
  // A recovery that never returns must not grow memory. Past the cap the held frames are abandoned,
  // and painting the TAIL of what survived would put the screen out of order -- so the console
  // resumes from the snapshot, which is exactly what it did before frames were held at all. The
  // fallback is never worse than the behaviour it replaced.
  await withRealResync(async () => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(9, "gap");
    for (let seq = 10; seq < 10 + 600; seq += 1) frame(seq, `live-${seq}`);
    assert.equal(entry.pendingOverflowed, true, "the cap did not engage");
    await new Promise((r) => setTimeout(r, 40));

    const afterSnapshot = painted.slice(painted.indexOf("SNAPSHOT") + 1);
    assert.deepEqual(afterSnapshot, [], `an overflowed queue replayed: ${JSON.stringify(afterSnapshot)}`);
    assert.equal(entry.lastSeq, 5, "the console did not resume from the snapshot");
    assert.equal(entry.pendingOverflowed, false, "the overflow marker outlived the recovery it described");
  }, { snapshotSeq: 5, delayMs: 15 });
});

test("NEGATIVE CONTROL: a snapshot that is CURRENT ends the recovery in one pass", async () => {
  // The loop is not unconditional, and saying so is what keeps the finding honest: when the snapshot
  // is at or past the frames that were dropped, the next live frame is in order and paints. That is
  // the case on a QUIET terminal -- and the operator's word was "sometimes".
  await withRealResync(async ({ fetches }) => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(9, "gap");
    for (const seq of [10, 11, 12]) frame(seq, `live-${seq}`);
    await new Promise((r) => setTimeout(r, 40));
    assert.equal(entry.lastSeq, 12, "the snapshot did not carry the dropped frames' sequence");

    frame(13, "after");
    assert.ok(painted.includes("after"), "a current snapshot still left the next frame gapped");
    assert.equal(fetches.length, 1, "a current snapshot should not need a second recovery");
  }, { snapshotSeq: 12, delayMs: 15 });
});
