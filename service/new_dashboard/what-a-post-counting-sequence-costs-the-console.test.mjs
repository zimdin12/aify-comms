// What the lag's mechanism actually cost, measured on the real socket and the real recovery.
//
// THE CLAIM THIS BLOCK HAS CARRIED WITHOUT A NUMBER. `5d6fbabe` fixed a sequence that counted POSTS
// while its only reader counts FRAMES, and the consequence was written down as "the browser saw a
// gap that nothing had dropped and recovered from it". True, and it says nothing about HOW OFTEN.
// A defect that costs one recovery an hour and one that costs a recovery per frame are the same
// sentence and different products.
//
// SO THIS COUNTS THEM. `realtime-socket.mjs` treats `seq > lastSeq + 1` as a dropped frame, so a
// server whose counter advances by the number of POSTS a flush coalesced presents every flush as a
// gap. The stride is the only variable: at 1 the stream is contiguous and nothing recovers; at 2 or
// more, every frame is a gap and every gap is an HTTP refetch, a `term.reset()` and a whole-screen
// repaint.
//
// THE NOUN, NAMED BEFORE THE NUMBER: this is RECOVERIES STARTED per frames delivered, counted by
// the fetches the real `resyncActiveConsole` makes. It is not a duration and no milliseconds appear
// here. What one of those recoveries costs the browser is measured separately, in
// `scripts/browser-paint/` -- about 7ms of repaint at 132x40, plus a round trip this does not time.
//
// MEASURED, NOT ARITHMETIC. The alternative was multiplying a flush rate by a repaint cost, which
// would have been two measured numbers and one assumed relationship. This drives the real consumer
// and counts what it actually did.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { initConsoleActions, resyncActiveConsole } from "./console-actions.mjs";
import { applyRealtimeEvent, initRealtimeSocket } from "./realtime-socket.mjs";
import { state } from "./state.mjs";

const FRAMES = 40;

/** A console the socket will paint into, with the collaborators the recovery reaches. */
function mountedConsole() {
  const painted = [];
  const entry = {
    terminalId: "t1",
    lastSeq: 0,
    fitCols: 80,
    ownsPty: false,
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
 * Deliver `FRAMES` frames whose sequence advances by `stride`, and count the recoveries.
 *
 * THE SERVER IS ANSWERING HONESTLY THROUGHOUT. Its snapshot carries the sequence the last frame
 * had, so a recovery that happens is one the CLIENT decided it needed -- which is the whole point:
 * the frames were never dropped, and the stride alone is what makes them look dropped.
 */
async function deliver(stride) {
  const saved = { fetch: globalThis.fetch, document: globalThis.document };
  const fetches = [];
  let answerSeq = 0;
  globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  globalThis.fetch = async (url) => {
    fetches.push(String(url));
    const body = JSON.stringify({
      terminal: { snapshot: "SNAPSHOT", outputSeq: answerSeq, renderedCols: 80 },
    });
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

  const { entry, painted } = mountedConsole();
  try {
    for (let i = 1; i <= FRAMES; i += 1) {
      answerSeq = i * stride;
      applyRealtimeEvent("terminal_output",
        { terminalId: "t1", agentId: "a1", output: `f${i}`, seq: i * stride });
      // Let a recovery this frame started finish before the next arrives, so the count is
      // recoveries STARTED rather than recoveries that happened to overlap.
      await new Promise((r) => setTimeout(r, 2));
    }
    return { fetches: fetches.length, painted, entry };
  } finally {
    globalThis.fetch = saved.fetch;
    globalThis.document = saved.document;
    state.activeXterm = null;
  }
}

test("POSITIVE CONTROL: a contiguous stream recovers NOT AT ALL, and paints every frame", async () => {
  // Without this the measurement below would also be satisfied by a console that recovers on
  // everything, including correct input -- which would make the comparison meaningless.
  const { fetches, painted, entry } = await deliver(1);
  // REPORTED BY THE CODE THAT ASSERTS IT. A figure quoted elsewhere has to come from the run
  // that judged it, or the two drift and the written one is the survivor.
  console.log(`  stride 1: ${fetches} recoveries, ${painted.length} of ${FRAMES} frames painted`);
  assert.equal(fetches, 0, `a contiguous stream started ${fetches} recoveries`);
  assert.equal(painted.filter((p) => p === "<reset>").length, 0, "the screen was reset");
  assert.equal(painted.length, FRAMES, `only ${painted.length} of ${FRAMES} frames painted`);
  assert.equal(entry.lastSeq, FRAMES);
});

test("A SEQUENCE COUNTING POSTS MAKES EVERY FRAME LOOK LIKE A GAP", async () => {
  // THE MEASUREMENT. Stride 2 is the mildest version of the defect -- a flush that coalesced two
  // posts -- and it is enough: `seq > lastSeq + 1` is true for every frame after the first, so
  // every frame is a drop that never happened.
  const { fetches, painted } = await deliver(2);
  console.log(`  stride 2: ${fetches} recoveries for ${FRAMES} frames, `
    + `${painted.filter((p) => p === "<reset>").length} screen resets`);
  assert.ok(fetches >= FRAMES - 1,
    `a post-counting sequence started ${fetches} recoveries for ${FRAMES} frames; `
    + "if this is low the console is not recovering per frame and the mechanism is milder than "
    + "this block has claimed");
});

test("AND A BUSIER FLUSH DOES NOT MAKE IT WORSE, because one gap per frame is already the ceiling",
  async () => {
    // WORTH PINNING BECAUSE IT IS COUNTER-INTUITIVE. A flush coalescing five posts does not cost
    // five times a flush coalescing two: the client recovers on the FIRST gap it sees and there is
    // one per frame either way. The defect's cost scales with the FRAME RATE, not with how much
    // each flush coalesced -- which is why it presented as "lags sometimes" rather than "lags more
    // when busy" in a way anybody could have graphed.
    const mild = await deliver(2);
    const busy = await deliver(5);
    console.log(`  stride 5: ${busy.fetches} recoveries against stride 2 at ${mild.fetches}`);
    assert.ok(Math.abs(busy.fetches - mild.fetches) <= 2,
      `stride 2 started ${mild.fetches} recoveries and stride 5 started ${busy.fetches}; `
      + "if these differ the ceiling claim above is wrong");
  });
