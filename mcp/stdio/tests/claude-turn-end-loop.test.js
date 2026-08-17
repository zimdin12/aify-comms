import assert from "assert";
import test from "node:test";
import { startClaudeTurnEndDetector } from "../claude-turn-end-detector.js";

// Tests for startClaudeTurnEndDetector (pure-event-status change #1, rewritten
// 2026-06-02 to a STRUCTURAL signal; made BIDIRECTIONAL 2026-06-02). The periodic
// loop reads a structural transcript-TAIL summary each tick ({ lastRole,
// lastStopReason, pendingToolUse }) and, edge-triggered:
//   POSTs /turn-start when the tail transitions into IN-FLIGHT (set working) —
//     the fix for RESIDENT claude under-report, where a channel-woken / scheduled
//     turn never fires UserPromptSubmit→/turn-start so turn_busy stays 0;
//   POSTs /turn-end when the tail transitions into ENDED (clear) — the existing
//     hook-independent backstop for a missed Stop hook.
//
// WHY structural, not growth-based: the parent transcript is STATIC during a long
// blocking tool call (build/test >30s), a long generation, or a Task sub-agent
// dispatch (sub-agents write a SEPARATE subagents/*.jsonl), so "stopped growing"
// false-cleared turn_busy mid-turn. These tests cover exactly those cases.
//
// ANTI-FEEDBACK-LOOP: the loop only reads transcript STRUCTURE (process truth) and
// only ever POSTs /turn-start or /turn-end. It never reads server status.

const TOOL_USE = { lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true };
const END_TURN = { lastRole: "assistant", lastStopReason: "end_turn", pendingToolUse: false };
const USER_PENDING = { lastRole: "user", lastStopReason: null, pendingToolUse: false };

// Drive the loop over a fixed sequence of tail summaries (one per tick), then resolve with the
// number of /turn-start and /turn-end POSTs. The loop reads the LAST element repeatedly once the
// sequence is exhausted, so extra ticks can never add a transition — which is what makes the
// counts exact rather than timing-dependent.
//
// DRIVEN BY THE SEQUENCE, NOT BY THE CLOCK (2026-08-17). Each test used to wait a fixed number of
// milliseconds and assume that bought `seq.length` ticks. It did not: `TICK_MS` is 5, and this
// file's own KEEP-FRESH tests record the ~15ms Windows setInterval floor 70 lines below. A 6-entry
// sequence therefore needs ~90ms of real ticks and was given 80 — the margin the old comment
// claimed was "~2x" was actually NEGATIVE, and the shortfall showed up as a reviewer's full-suite
// failure ("two distinct ended turns -> two /turn-end; got 1") that passed when the file ran alone.
// Waiting for the READS to happen removes the assumption entirely: under any scheduler, on any
// platform, the assertions now see the whole sequence or fail saying how much of it they saw.
const TICK_MS = 5;
const SETTLE_TICKS = 3;      // ticks past the last entry, so its transition has certainly posted
const CONSUME_BOUND_MS = 20_000;

async function runLoop(seq) {
  const starts = [];
  const ends = [];
  let reads = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: TICK_MS,
    readTranscript: async () => {
      const tail = seq[Math.min(reads, seq.length - 1)];
      reads += 1;
      return tail;
    },
    postTurnStart: async () => { starts.push(Date.now()); },
    postTurnEnd: async () => { ends.push(Date.now()); },
  });
  const wanted = seq.length + SETTLE_TICKS;
  const deadline = Date.now() + CONSUME_BOUND_MS;
  try {
    while (reads < wanted) {
      if (Date.now() > deadline) {
        throw new Error(`the detector read ${reads} of ${wanted} tails in ${CONSUME_BOUND_MS}ms — it is not ticking`);
      }
      await new Promise((r) => setTimeout(r, TICK_MS));
    }
  } finally {
    stop();
  }
  return { starts: starts.length, ends: ends.length };
}

test("long build / pending tool_use POSTs /turn-start once, never /turn-end (no false-clear)", async () => {
  const { starts, ends } = await runLoop([TOOL_USE, TOOL_USE, TOOL_USE, TOOL_USE, TOOL_USE]);
  assert.strictEqual(ends, 0, `pending tool_use must never fire turn-end; got ${ends}`);
  assert.strictEqual(starts, 1, `in-flight sets working exactly once (no spam); got ${starts}`);
});

test("sub-agent dispatch (parent static, tool_use pending) STARTs once, never ENDs", async () => {
  const seq = Array(8).fill(TOOL_USE);
  const { starts, ends } = await runLoop(seq);
  assert.strictEqual(ends, 0, `a Task sub-agent dispatch must never fire turn-end; got ${ends}`);
  assert.strictEqual(starts, 1, `sub-agent dispatch sets working once; got ${starts}`);
});

test("in-flight -> ended -> new-in-flight: /turn-start, /turn-end, /turn-start (re-arm both directions)", async () => {
  const seq = [TOOL_USE, END_TURN, END_TURN, TOOL_USE, END_TURN, END_TURN];
  const { starts, ends } = await runLoop(seq);
  assert.strictEqual(starts, 2, `two distinct in-flight turns -> two /turn-start; got ${starts}`);
  assert.strictEqual(ends, 2, `two distinct ended turns -> two /turn-end; got ${ends}`);
});

test("null / unreadable tail never POSTs either way (false-clear / false-set safety)", async () => {
  const { starts, ends } = await runLoop([null, null, undefined, null]);
  assert.strictEqual(starts, 0, `null/unreadable tail must never fire turn-start; got ${starts}`);
  assert.strictEqual(ends, 0, `null/unreadable tail must never fire turn-end; got ${ends}`);
});

test("between-tool-calls (trailing user/tool_result) stays working: one START, no END", async () => {
  const { starts, ends } = await runLoop([TOOL_USE, USER_PENDING, TOOL_USE, USER_PENDING]);
  assert.strictEqual(starts, 1, `one in-flight turn -> one /turn-start; got ${starts}`);
  assert.strictEqual(ends, 0, `a trailing user/tool_result is in-flight, not ended; got ${ends}`);
});

test("an unreadable tick mid-turn does not lose state; START once, the eventual end_turn ENDs once", async () => {
  const { starts, ends } = await runLoop([TOOL_USE, null, END_TURN, END_TURN]);
  assert.strictEqual(starts, 1, `in-flight -> one /turn-start; got ${starts}`);
  assert.strictEqual(ends, 1, `ended after a transient unreadable tick -> one /turn-end; got ${ends}`);
});

test("RESIDENT channel-woken turn (first observation already mid-tool, no hook) POSTs /turn-start", async () => {
  // The under-report repro: the turn's only start signal would be the detector.
  const { starts, ends } = await runLoop([TOOL_USE, TOOL_USE, END_TURN, END_TURN]);
  assert.strictEqual(starts, 1, `channel-woken in-flight turn sets working; got ${starts}`);
  assert.strictEqual(ends, 1, `then yields -> one /turn-end; got ${ends}`);
});

test("missing params return a no-op stop fn and never throw", () => {
  const stop1 = startClaudeTurnEndDetector({});
  assert.strictEqual(typeof stop1, "function");
  stop1();
  const stop2 = startClaudeTurnEndDetector({
    intervalMs: 0, readTranscript: async () => null, postTurnStart: async () => {}, postTurnEnd: async () => {},
  });
  assert.strictEqual(typeof stop2, "function");
  stop2();
});

test("KEEP-FRESH: a sustained in-flight turn re-stamps /turn-start every workingRefreshMs", async () => {
  // The server's delivery-completion clear can wipe a LIVE turn's turn_busy (a steered
  // message lands mid-turn → no reply-owing run → clear); edge-triggered start never
  // re-fires, so the resident read `online` while hard at work (2026-06-12). While the
  // transcript stays in-flight the loop must keep re-stamping.
  // Intervals sit ABOVE the Windows ~15ms setInterval floor (see the hermes detector
  // tests): 25ms ticks + a proportional refresh window keep the count deterministic.
  let starts = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 25,
    workingRefreshMs: 50, // refresh ~every 2 ticks while in-flight
    readTranscript: async () => TOOL_USE, // one long, uninterrupted turn
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => {},
  });
  await new Promise((r) => setTimeout(r, 300));
  stop();
  assert.ok(starts >= 3, `a sustained in-flight turn must keep re-stamping turn-start (got ${starts})`);
});

test("KEEP-FRESH: workingRefreshMs=0 keeps edge-only /turn-start (back-compat)", async () => {
  let starts = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 5,
    workingRefreshMs: 0,
    readTranscript: async () => TOOL_USE,
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => {},
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.strictEqual(starts, 1, `refresh disabled → exactly one edge /turn-start; got ${starts}`);
});

test("KEEP-FRESH: refresh never fires after the turn ENDS, and the next turn re-arms cleanly", async () => {
  let starts = 0, ends = 0, i = 0;
  const seq = [TOOL_USE, TOOL_USE, END_TURN, END_TURN, END_TURN, END_TURN, END_TURN, END_TURN];
  const stop = startClaudeTurnEndDetector({
    intervalMs: 25,
    workingRefreshMs: 50,
    readTranscript: async () => seq[Math.min(i++, seq.length - 1)],
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 350));
  stop();
  assert.strictEqual(ends, 1, `one ended turn → one /turn-end; got ${ends}`);
  // 1 edge + at most 1 refresh before the end at tick 3 — and NO refresh after the end.
  assert.ok(starts <= 2, `no re-stamp after turn-end (got ${starts} starts)`);
});

test("KEEP-CLEARED: a sustained ENDED transcript re-asserts /turn-end every idleRefreshMs (stray clear)", async () => {
  // The stuck-working fix: a stray in_turn set OUTSIDE this detector (a hook/sidecar /turn-start
  // whose end-event was lost) is invisible to the edge clear. While the transcript PROVES ended,
  // the loop must keep re-asserting /turn-end so the stray heals within a window, not the 30-min
  // ceiling (operator: general-manager stuck working infinitely, 2026-07-13).
  let ends = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 25,
    idleRefreshMs: 50, // re-assert ~every 2 ticks while ended
    readTranscript: async () => END_TURN, // steadily idle/terminal
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 300));
  stop();
  assert.ok(ends >= 3, `a proven-ended transcript must keep re-asserting turn-end (got ${ends})`);
});

test("KEEP-CLEARED: never fires while the transcript is IN-FLIGHT (only keep-fresh does)", async () => {
  let ends = 0, starts = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 25,
    workingRefreshMs: 50,
    idleRefreshMs: 50,
    readTranscript: async () => TOOL_USE, // sustained in-flight
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 300));
  stop();
  assert.strictEqual(ends, 0, `keep-cleared must never fire during a live turn; got ${ends}`);
  assert.ok(starts >= 3, `keep-fresh still re-stamps working while in-flight; got ${starts}`);
});

test("KEEP-CLEARED: a null/unknown tail never re-asserts /turn-end (false-clear safety)", async () => {
  let ends = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 25,
    idleRefreshMs: 50,
    readTranscript: async () => null, // unreadable → UNKNOWN, not proven-ended
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 200));
  stop();
  assert.strictEqual(ends, 0, `an unknown tail is not proof of idle → never keep-clears; got ${ends}`);
});

test("KEEP-CLEARED: idleRefreshMs=0 keeps edge-only /turn-end (back-compat)", async () => {
  let ends = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 5,
    idleRefreshMs: 0,
    readTranscript: async () => END_TURN,
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.strictEqual(ends, 1, `clear-refresh disabled → exactly one edge /turn-end; got ${ends}`);
});

test("a loop with only postTurnEnd (no postTurnStart) still ENDs and never throws on START", async () => {
  // Back-compat: a caller that wires only the clear path must not crash when the
  // detector wants to START. It should simply skip the unwired START.
  const ends = [];
  let i = 0;
  const seq = [TOOL_USE, END_TURN, END_TURN];
  const stop = startClaudeTurnEndDetector({
    intervalMs: TICK_MS,
    readTranscript: async () => seq[Math.min(i++, seq.length - 1)],
    postTurnEnd: async () => { ends.push(1); },
  });
  await new Promise((r) => setTimeout(r, 45));
  stop();
  assert.strictEqual(ends.length, 1, `still fires /turn-end once even without postTurnStart; got ${ends.length}`);
});
