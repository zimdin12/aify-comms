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

// Drive the loop over a fixed sequence of tail summaries (one per tick), then
// resolve with the number of /turn-start and /turn-end POSTs. The loop reads the
// LAST element repeatedly after the sequence is exhausted, so pick durations that
// consume the sequence (intervalMs small).
async function runLoop(seq, ms) {
  const starts = [];
  const ends = [];
  let i = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 10,
    readTranscript: async () => seq[Math.min(i++, seq.length - 1)],
    postTurnStart: async () => { starts.push(Date.now()); },
    postTurnEnd: async () => { ends.push(Date.now()); },
  });
  await new Promise((r) => setTimeout(r, ms));
  stop();
  return { starts: starts.length, ends: ends.length };
}

test("long build / pending tool_use POSTs /turn-start once, never /turn-end (no false-clear)", async () => {
  const { starts, ends } = await runLoop([TOOL_USE, TOOL_USE, TOOL_USE, TOOL_USE, TOOL_USE], 120);
  assert.strictEqual(ends, 0, `pending tool_use must never fire turn-end; got ${ends}`);
  assert.strictEqual(starts, 1, `in-flight sets working exactly once (no spam); got ${starts}`);
});

test("sub-agent dispatch (parent static, tool_use pending) STARTs once, never ENDs", async () => {
  const seq = Array(8).fill(TOOL_USE);
  const { starts, ends } = await runLoop(seq, 140);
  assert.strictEqual(ends, 0, `a Task sub-agent dispatch must never fire turn-end; got ${ends}`);
  assert.strictEqual(starts, 1, `sub-agent dispatch sets working once; got ${starts}`);
});

test("in-flight -> ended -> new-in-flight: /turn-start, /turn-end, /turn-start (re-arm both directions)", async () => {
  const seq = [TOOL_USE, END_TURN, END_TURN, TOOL_USE, END_TURN, END_TURN];
  const { starts, ends } = await runLoop(seq, 160);
  assert.strictEqual(starts, 2, `two distinct in-flight turns -> two /turn-start; got ${starts}`);
  assert.strictEqual(ends, 2, `two distinct ended turns -> two /turn-end; got ${ends}`);
});

test("null / unreadable tail never POSTs either way (false-clear / false-set safety)", async () => {
  const { starts, ends } = await runLoop([null, null, undefined, null], 100);
  assert.strictEqual(starts, 0, `null/unreadable tail must never fire turn-start; got ${starts}`);
  assert.strictEqual(ends, 0, `null/unreadable tail must never fire turn-end; got ${ends}`);
});

test("between-tool-calls (trailing user/tool_result) stays working: one START, no END", async () => {
  const { starts, ends } = await runLoop([TOOL_USE, USER_PENDING, TOOL_USE, USER_PENDING], 110);
  assert.strictEqual(starts, 1, `one in-flight turn -> one /turn-start; got ${starts}`);
  assert.strictEqual(ends, 0, `a trailing user/tool_result is in-flight, not ended; got ${ends}`);
});

test("an unreadable tick mid-turn does not lose state; START once, the eventual end_turn ENDs once", async () => {
  const { starts, ends } = await runLoop([TOOL_USE, null, END_TURN, END_TURN], 110);
  assert.strictEqual(starts, 1, `in-flight -> one /turn-start; got ${starts}`);
  assert.strictEqual(ends, 1, `ended after a transient unreadable tick -> one /turn-end; got ${ends}`);
});

test("RESIDENT channel-woken turn (first observation already mid-tool, no hook) POSTs /turn-start", async () => {
  // The under-report repro: the turn's only start signal would be the detector.
  const { starts, ends } = await runLoop([TOOL_USE, TOOL_USE, END_TURN, END_TURN], 110);
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

test("a loop with only postTurnEnd (no postTurnStart) still ENDs and never throws on START", async () => {
  // Back-compat: a caller that wires only the clear path must not crash when the
  // detector wants to START. It should simply skip the unwired START.
  const ends = [];
  let i = 0;
  const seq = [TOOL_USE, END_TURN, END_TURN];
  const stop = startClaudeTurnEndDetector({
    intervalMs: 10,
    readTranscript: async () => seq[Math.min(i++, seq.length - 1)],
    postTurnEnd: async () => { ends.push(1); },
  });
  await new Promise((r) => setTimeout(r, 90));
  stop();
  assert.strictEqual(ends.length, 1, `still fires /turn-end once even without postTurnStart; got ${ends.length}`);
});
