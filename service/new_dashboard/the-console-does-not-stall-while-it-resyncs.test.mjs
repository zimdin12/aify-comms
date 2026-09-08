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
function withRealResync(run, { snapshotSeq = 5, snapshotSeqs = null, snapshot = "SNAPSHOT", delayMs = 0 } = {}) {
  const saved = { fetch: globalThis.fetch, document: globalThis.document };
  const fetches = [];
  globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  globalThis.fetch = async (url) => {
    fetches.push(String(url));
    // A SERVER THAT MOVES BETWEEN FETCHES, which a constant snapshot cannot express. The recovery
    // may now take a second pass, and the whole point of the second pass is that the server has
    // more of the stream by then -- so the double has to be able to answer differently the second
    // time or the test is asking one question twice.
    const answerSeq = Array.isArray(snapshotSeqs)
      ? (snapshotSeqs[fetches.length - 1] ?? snapshotSeqs[snapshotSeqs.length - 1])
      : snapshotSeq;
    if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
    // `text()`, NOT `json()`. `api-client.mjs` reads the body as text and parses it itself, and a
    // double that stubbed `json()` returned an EMPTY object through the real helper -- so the resync
    // reset the screen, wrote "", left the sequence untouched, and this file's first run read that
    // as the product looping. Stub the method the code actually calls.
    const body = JSON.stringify({ terminal: { snapshot, outputSeq: answerSeq, renderedCols: 80 } });
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

    // ASKED WHILE THE FIRST FETCH IS STILL IN FLIGHT, which is what this test is about: four frames
    // arriving during ONE recovery must not start four recoveries. It used to ask after a 40ms wait
    // and read the same number by accident, because a recovery could only ever fetch once. A
    // recovery may now take a second pass when it cannot place what it held, so a total taken after
    // the fact would be counting a different thing -- the coalescing claim is about re-entry, and
    // re-entry happens here, before anything has settled.
    assert.equal(fetches.length, 1, `the recovery fanned out into ${fetches.length} fetches`);
    await new Promise((r) => setTimeout(r, 60));
  }, { delayMs: 15 });
});

test("AND THEY ARE REPLAYED AFTER THE SNAPSHOT, so the next live frame is in sequence", async () => {
  // THE LOOP THIS CLOSES. The snapshot carries the buffer as the SERVER had it; frames past that
  // arrived while the fetch was in flight. Resuming from the snapshot's sequence alone left the
  // console behind the stream, so the very next frame gapped again -- fetch, drop, gap, fetch, on a
  // continuously producing agent, painting only snapshots and showing nothing in between.
  //
  // THE SNAPSHOT COVERS UP TO 8 HERE, AND IT USED TO SAY 5, WHICH ENCODED THE DEFECT the whole-diff
  // review found. With a snapshot at 5 and 9..12 held, frames 6, 7 and 8 exist on the server and are
  // in NEITHER the snapshot nor the held list -- so replaying 9..12 paints over a hole and commits a
  // sequence the screen never contained. This test asserted that as correct. The case the feature
  // actually exists for is a snapshot that MEETS the held run, which is what it now sets up; the
  // non-adjacent case has its own test below and must NOT replay.
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
  }, { snapshotSeq: 8, delayMs: 15 });
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

test("A NON-ADJACENT HELD FRAME MUST NOT COMMIT A SEQUENCE THE SCREEN NEVER GOT", async () => {
  // FOUND BY THE WHOLE-DIFF REVIEW, and it is the worst shape a console defect can take: silent,
  // permanent, and self-concealing.
  //
  // The replay filtered the held frames against the snapshot's floor and wrote whatever was above
  // it, in order, advancing `lastSeq` to the LAST one written. With a snapshot at 5 and 9/10 held,
  // it painted 9 and 10 and committed 10 -- while 6, 7 and 8 had never been painted at all. Every
  // one of them then arrives with `seq <= lastSeq` and is DISCARDED as already covered, and frame 11
  // looks perfectly adjacent to 10, so no gap is ever detected and no second recovery happens. The
  // screen is missing three frames for the life of the console.
  //
  // A HELD FRAME IS ONLY REPLAYABLE IF IT CONTINUES THE SCREEN. Anything else has to leave `lastSeq`
  // where the picture actually ends, so the next arriving frame reads as the gap it is and recovers.
  await withRealResync(async () => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(9, "nine");                      // starts the recovery; 5..8 never arrive
    frame(10, "ten");
    await new Promise((r) => setTimeout(r, 40));

    assert.ok(!painted.includes("nine") && !painted.includes("ten"),
      `frames past the snapshot's own sequence were painted over a gap: ${JSON.stringify(painted)}`);
    assert.equal(entry.lastSeq, 5,
      `the sequence was advanced to ${entry.lastSeq} while the screen ends at the snapshot's 5`);

    // AND THE CONSOLE STILL RECOVERS: the next frame is a gap again, which is the correct outcome.
    const before = entry.lastSeq;
    frame(11, "eleven");
    assert.equal(entry.lastSeq, before, "frame 11 was accepted as adjacent to a sequence nobody painted");
  });
});

test("A LATE SNAPSHOT MOVES THE SEQUENCE BACK, because the SCREEN moved back", async () => {
  // FOUND BY REVIEW, and it is the same defect class as the non-adjacent replay above wearing the
  // other face: the bookkeeping claiming a screen that is not there.
  //
  // The socket paints CONTIGUOUS frames straight through -- there is no check on `entry.resyncing`
  // in the painting path -- so a manual resync from 4 with 5 and 6 arriving paints both and leaves
  // `lastSeq` at 6. The fetch then answers with a snapshot at 4, `reset()` wipes the screen, and the
  // old `Math.max` KEPT the sequence at 6. So the console showed a 4-snapshot and claimed 6, and the
  // retransmitted 5 and 6 that the reset made necessary were refused as already covered.
  await withRealResync(async ({ fetches }) => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(5, "five");
    frame(6, "six");
    assert.deepEqual(painted, ["five", "six"], "the contiguous frames did not paint live");
    assert.equal(entry.lastSeq, 6);

    await resyncActiveConsole();
    assert.equal(entry.lastSeq, 4,
      `the screen was reset to a snapshot at 4 and the sequence stayed at ${entry.lastSeq}`);
    assert.equal(fetches.length, 1, "nothing was held, so one fetch should have finished it");

    // AND THE BYTES COME BACK. This is the consequence the sequence exists to protect: after the
    // reset those two frames are no longer on the screen, so the retransmission has to be accepted.
    const afterSnapshot = painted.slice(painted.lastIndexOf("SNAPSHOT") + 1);
    frame(5, "five-again");
    frame(6, "six-again");
    assert.deepEqual(painted.slice(painted.lastIndexOf("SNAPSHOT") + 1), ["five-again", "six-again"],
      `the retransmitted frames were refused as already covered: ${JSON.stringify(afterSnapshot)}`);
    assert.equal(entry.lastSeq, 6);
  }, { snapshotSeq: 4 });
});

test("CONTIGUOUS FRAMES ARRIVING DURING A FETCH ARE HELD TOO, not painted onto a screen about to reset", async () => {
  // REVIEW'S TRACE, and the one the first version of this fix did not close. A recovery ends with
  // `term.reset()` and the snapshot alone, so ANY frame painted while the fetch is outstanding is
  // about to be wiped -- and a CONTIGUOUS one took the painting path, advanced `lastSeq`, and was
  // gone. Making the sequence follow the snapshot made its retransmission ADMISSIBLE; nothing
  // causes a retransmission, so on a terminal that then falls quiet those bytes were lost for the
  // life of the console. Actual writes were FRAME_5, FRAME_6, reset, SNAPSHOT_4, and that was all.
  //
  // Classification belongs to the recovery, which is the only thing that knows what the snapshot
  // covers. The socket's job while a fetch is outstanding is to keep the bytes.
  await withRealResync(async ({ fetches }) => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    resyncActiveConsole().catch(() => {});
    await new Promise((r) => setTimeout(r, 5));      // the fetch is now in flight
    frame(5, "five");
    frame(6, "six");
    assert.deepEqual(painted, [],
      `frames arriving during the fetch were painted onto a screen about to reset: ${JSON.stringify(painted)}`);
    assert.equal(entry.pendingFrames.length, 2, "the contiguous frames were not held");

    await new Promise((r) => setTimeout(r, 60));
    assert.equal(fetches.length, 1, `one snapshot covered it; ${fetches.length} fetches were made`);
    const afterSnapshot = painted.slice(painted.lastIndexOf("SNAPSHOT") + 1);
    assert.deepEqual(afterSnapshot, ["five", "six"],
      `the held frames were not replayed after the snapshot: ${JSON.stringify(painted)}`);
    assert.equal(entry.lastSeq, 6, "the console resumed behind the frames it had just painted");
  }, { snapshotSeq: 4, delayMs: 25 });
});

test("HELD FRAMES SURVIVE AN UNKNOWN POSITION, which cannot be adjacent to anything", async () => {
  // REVIEW'S TRACE, and it is the other half of serving `outputSeq: null`. An unknown position is
  // `lastSeq = -1`, the adjacency rule then demands the first held frame be seq 0, no real frame
  // ever is, and the drain CLEARS the queue at the end regardless. Three fetches, nothing painted,
  // the output gone, and nothing left to notice -- `resyncing` is false and no further frame comes.
  //
  // WITH NO POSITION THERE IS NOTHING FOR A FRAME TO CONTRADICT. The run is seeded from its lowest
  // sequence and the console adopts that position; after the first frame the adjacency rule resumes.
  await withRealResync(async ({ fetches }) => {
    const { entry, painted } = mountedConsole({ lastSeq: -1 });
    resyncActiveConsole().catch(() => {});
    await new Promise((r) => setTimeout(r, 5));
    frame(5, "five");
    frame(6, "six");
    await new Promise((r) => setTimeout(r, 80));

    const afterSnapshot = painted.slice(painted.lastIndexOf("SNAPSHOT") + 1);
    assert.deepEqual(afterSnapshot, ["five", "six"],
      `an unknown position threw the held frames away: ${JSON.stringify(painted)}`);
    assert.equal(entry.lastSeq, 6, "the console did not adopt the position it just painted");
    assert.deepEqual(entry.pendingFrames, [], "the queue outlived the recovery that resolved it");
    assert.equal(fetches.length, 1, `one snapshot covered it; ${fetches.length} fetches were made`);
  }, { snapshotSeqs: [null], delayMs: 25 });
});

test("THE SEED IS ONE FRAME, and the rest of that pass is held to adjacency as always", async () => {
  // NEGATIVE CONTROL for the seed: it is a seed, not a mode. Once the first held frame has given
  // the console a position, the rest of THAT PASS are held to the same adjacency rule -- otherwise
  // an unknown position would license painting over a hole inside a single recovery.
  //
  // ACROSS PASSES IS A DIFFERENT QUESTION AND THE ANSWER IS DELIBERATE. Each pass resets the screen
  // to a fresh snapshot, and a snapshot that again says UNKNOWN puts the position back to unknown --
  // truthfully, because the screen it just painted has no known position either. So the next pass
  // seeds again from what it still holds. That can put a later frame on a screen that may or may not
  // contain the ones between; with no position, nothing can say which, and the alternative is
  // dropping output. The bounded retry is what stops it repeating.
  await withRealResync(async () => {
    const { entry, painted } = mountedConsole({ lastSeq: -1 });
    resyncActiveConsole().catch(() => {});
    await new Promise((r) => setTimeout(r, 5));
    frame(5, "five");
    frame(9, "nine");                        // 6, 7 and 8 exist on the server and are nowhere here
    await new Promise((r) => setTimeout(r, 200));

    // THE FIRST PASS'S OWN SEGMENT, between its snapshot and the next reset. Reading the LAST
    // segment would be reading a later pass and calling it this one.
    const firstSnapshot = painted.indexOf("SNAPSHOT");
    const nextReset = painted.indexOf("<reset>", firstSnapshot);
    const firstPass = painted.slice(firstSnapshot + 1, nextReset === -1 ? undefined : nextReset);
    assert.deepEqual(firstPass, ["five"],
      `the seed painted past a gap inside one pass: ${JSON.stringify(painted)}`);
    assert.ok(entry.lastSeq >= 5, "the console never adopted a position at all");
  }, { snapshotSeqs: [null], delayMs: 25 });
});

test("A RECOVERY THAT CANNOT PLACE WHAT IT HELD FETCHES AGAIN, without waiting for another frame", async () => {
  // FOUND BY REVIEW, and the quiet agent is what makes it permanent. With a snapshot at 5 and 9/10
  // held, the replay correctly refuses to cross the gap -- and the first version of that fix then
  // EMPTIED the queue anyway and let the caller mark the recovery finished. On a terminal that then
  // goes silent there is no next frame to notice, so the console sits believing it is live with two
  // frames it was handed and threw away.
  //
  // A recovery owes its own next step. The server has more of the stream by the time the second
  // fetch lands -- here a snapshot at 8 -- and 9 and 10 are then adjacent and paint.
  await withRealResync(async ({ fetches }) => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(9, "nine");                      // 5..8 never arrive on the socket
    frame(10, "ten");
    await new Promise((r) => setTimeout(r, 80));   // NOTHING ELSE IS SENT. The recovery is on its own.

    assert.equal(fetches.length, 2,
      `the unresolved recovery took ${fetches.length} fetch(es); it must not wait for a frame`);
    assert.equal(entry.lastSeq, 10, "the second snapshot's replay did not resume the stream");
    const afterLastSnapshot = painted.slice(painted.lastIndexOf("SNAPSHOT") + 1);
    assert.deepEqual(afterLastSnapshot, ["nine", "ten"],
      `the held frames were lost rather than replayed: ${JSON.stringify(painted)}`);
    assert.deepEqual(entry.pendingFrames, [], "the queue outlived the recovery that resolved it");
  }, { snapshotSeqs: [5, 8], delayMs: 10 });
});

test("AND IT GIVES UP AFTER A BOUNDED NUMBER OF PASSES, rather than fetching forever", async () => {
  // The retry is not a loop with no floor. A server that never advances would otherwise be asked
  // again for as long as the console is open, which is the fan-out `entry.resyncing` exists to
  // prevent wearing a different hat. Past the bound the held frames are given up and the sequence
  // stays where the screen really ends -- the same fallback an overflow takes, and the behaviour
  // from before frames were held at all.
  await withRealResync(async ({ fetches }) => {
    const { entry } = mountedConsole({ lastSeq: 4 });
    frame(9, "nine");
    frame(10, "ten");
    await new Promise((r) => setTimeout(r, 120));

    assert.equal(fetches.length, 3, `a stuck recovery took ${fetches.length} fetches`);
    assert.equal(entry.lastSeq, 5, "the sequence left the screen the snapshot actually painted");
    assert.deepEqual(entry.pendingFrames, [], "the unplaceable frames were kept forever");
  }, { snapshotSeq: 5, delayMs: 10 });
});

test("A DUPLICATE HELD FRAME IS WRITTEN ONCE", async () => {
  // The held list is whatever arrived, and the socket holds every frame it could not paint --
  // including a retransmit. Replaying both copies paints the same bytes twice, which for a TUI is
  // not a doubled line but a cursor movement nobody asked for.
  await withRealResync(async () => {
    const { entry, painted } = mountedConsole({ lastSeq: 4 });
    frame(6, "six");                       // 6 is adjacent to the snapshot's 5, so it replays
    frame(6, "six-again");
    frame(7, "seven");
    await new Promise((r) => setTimeout(r, 40));

    const afterSnapshot = painted.slice(painted.indexOf("SNAPSHOT") + 1);
    assert.deepEqual(afterSnapshot, ["six", "seven"],
      `the replay did not paint each held sequence exactly once: ${JSON.stringify(painted)}`);
    assert.equal(entry.lastSeq, 7);
  });
});
