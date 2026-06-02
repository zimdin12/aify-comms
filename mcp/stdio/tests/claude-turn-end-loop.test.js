import assert from "assert";
import test from "node:test";
import { startClaudeTurnEndDetector } from "../claude-turn-end-detector.js";

// Tests for startClaudeTurnEndDetector (pure-event-status change #1, rewritten
// 2026-06-02 to a STRUCTURAL signal). The periodic loop reads a structural
// transcript-TAIL summary each tick ({ lastRole, lastStopReason, pendingToolUse })
// and POSTs /turn-end ONLY when the detector decides the turn ENDED (last
// assistant message yielded to the user). It is the hook-independent backstop for
// a missed Stop hook.
//
// WHY structural, not growth-based: the parent transcript is STATIC during a long
// blocking tool call (build/test >30s), a long generation, or a Task sub-agent
// dispatch (sub-agents write a SEPARATE subagents/*.jsonl), so "stopped growing"
// false-cleared turn_busy mid-turn. These tests cover exactly those false-clear
// cases the adversarial review found.
//
// ANTI-FEEDBACK-LOOP: the loop only reads transcript STRUCTURE and only ever POSTs
// /turn-end (a CLEAR). It never reads server status and never sets turn_busy.

const TOOL_USE = { lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true };
const END_TURN = { lastRole: "assistant", lastStopReason: "end_turn", pendingToolUse: false };
const USER_PENDING = { lastRole: "user", lastStopReason: null, pendingToolUse: false };

// Drive the loop over a fixed sequence of tail summaries (one per tick), then
// resolve with the number of /turn-end POSTs. The loop reads the LAST element
// repeatedly after the sequence is exhausted, so pick durations that consume the
// sequence (intervalMs small).
async function runLoop(seq, ms) {
  const posts = [];
  let i = 0;
  const stop = startClaudeTurnEndDetector({
    intervalMs: 10,
    readTranscript: async () => seq[Math.min(i++, seq.length - 1)],
    postTurnEnd: async () => { posts.push(Date.now()); },
  });
  await new Promise((r) => setTimeout(r, ms));
  stop();
  return posts.length;
}

test("long build / pending tool_use never POSTs /turn-end (no false-clear)", async () => {
  // Every tick the last assistant message is awaiting a tool result.
  const n = await runLoop([TOOL_USE, TOOL_USE, TOOL_USE, TOOL_USE, TOOL_USE], 120);
  assert.strictEqual(n, 0, `pending tool_use must never fire turn-end; got ${n}`);
});

test("sub-agent dispatch (parent static, tool_use pending) never POSTs", async () => {
  const seq = Array(8).fill(TOOL_USE);
  const n = await runLoop(seq, 140);
  assert.strictEqual(n, 0, `a Task sub-agent dispatch must never fire turn-end; got ${n}`);
});

test("a completed end_turn POSTs exactly once; a new turn re-arms and POSTs again", async () => {
  // turn 1 ends, lingers a tick, then turn 2 works (tool_use) and ends.
  const seq = [END_TURN, END_TURN, TOOL_USE, END_TURN, END_TURN];
  const n = await runLoop(seq, 140);
  assert.strictEqual(n, 2, `two distinct ended turns -> exactly two POSTs; got ${n}`);
});

test("null / unreadable tail never POSTs /turn-end (false-clear safety)", async () => {
  const n = await runLoop([null, null, undefined, null], 100);
  assert.strictEqual(n, 0, `null/unreadable tail must never fire turn-end; got ${n}`);
});

test("between-tool-calls (trailing user/tool_result) never POSTs", async () => {
  const n = await runLoop([TOOL_USE, USER_PENDING, TOOL_USE, USER_PENDING], 110);
  assert.strictEqual(n, 0, `a trailing user/tool_result is in-flight, not ended; got ${n}`);
});

test("an unreadable tick mid-turn does not lose arming; the eventual end_turn POSTs once", async () => {
  const n = await runLoop([TOOL_USE, null, END_TURN, END_TURN], 110);
  assert.strictEqual(n, 1, `ended after a transient unreadable tick -> exactly one POST; got ${n}`);
});

test("missing params return a no-op stop fn and never throw", () => {
  const stop1 = startClaudeTurnEndDetector({});
  assert.strictEqual(typeof stop1, "function");
  stop1();
  const stop2 = startClaudeTurnEndDetector({
    intervalMs: 0, readTranscript: async () => null, postTurnEnd: async () => {},
  });
  assert.strictEqual(typeof stop2, "function");
  stop2();
});
