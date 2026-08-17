// The codex session's BUFFERED console feed, its fatal-log handling, and its spawn-error fast-fail.
//
// Twenty-fifth cluster off the V8-coverage census: `codex-session.js`'s `detachTerminalSink`, `steer`,
// `_dispatchRuntimeLog` and `_onSpawnError`. The constructor only assigns fields, so all four are reachable on a
// directly-constructed session with `_rpc` replaced on the instance. Nothing spawns a codex app-server.
//
// THE DIVERGENCE FROM HERMES IS THE INTERESTING PART, and it is deliberate rather than drift. The managed hermes
// gateway session DROPS frames pushed before a sink exists (see hermes-gateway-session-plumbing.test.js); this one
// BUFFERS them, caps the buffer by characters, and flushes on attach. That is why a dashboard console opened
// mid-turn shows a codex agent's earlier output and not a hermes agent's — a difference an operator sees, so it is
// pinned on both sides.
//
// NOT COVERED HERE, and the census will keep listing them: `onStderr` and `interruptAbandonedTurn` are closures
// built inside `ensureStarted` / `_runTurnInner`, so reaching them means acquiring a real app-server and running a
// turn through it. That needs a spawn harness and is its own slice.

import assert from "node:assert/strict";
import test from "node:test";

import { CodexSession } from "../codex-session.js";

const session = (opts = {}) => new CodexSession({ agentId: "codex-session-agent", ...opts });
const drain = () => new Promise((resolve) => setTimeout(resolve, 20));

// ── the buffered feed ───────────────────────────────────────────────────────

test("frames pushed BEFORE a sink exists are replayed into it, in order", async () => {
  // The opposite of the hermes gateway session, on purpose: attaching a console mid-turn shows what the agent has
  // already said. Dropping them would leave the operator staring at a blank console for a turn already running.
  const frames = [];
  const s = session();
  s._pushTerminalFrame("first");
  s._pushTerminalFrame("second");
  await drain();

  s.attachTerminalSink((text) => { frames.push(text); });
  await drain();
  assert.deepEqual(frames, ["first", "second"]);
});

test("frames pushed while a sink is attached arrive in order", async () => {
  const frames = [];
  const s = session();
  s.attachTerminalSink(async (text) => {
    await new Promise((resolve) => setTimeout(resolve, text === "slow" ? 40 : 1));
    frames.push(text);
  });

  s._pushTerminalFrame("slow");
  s._pushTerminalFrame("then");
  s._pushTerminalFrame("last");
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.deepEqual(frames, ["slow", "then", "last"], "the flush loop did not serialise");
});

test("the buffer is capped by CHARACTERS, dropping the oldest and keeping the newest", async () => {
  // A turn can emit megabytes. Buffering it all would grow the bridge's heap per agent; dropping the NEWEST
  // instead would show the operator the start of a turn and never its end.
  const s = session();
  const big = "x".repeat(20_000);
  for (let i = 0; i < 5; i += 1) s._pushTerminalFrame(`${i}:${big}`);
  s._pushTerminalFrame("newest");

  const frames = [];
  s.attachTerminalSink((text) => { frames.push(text) });
  await drain();

  assert.ok(frames.length < 6, `nothing was evicted (${frames.length} frames)`);
  assert.equal(frames[frames.length - 1], "newest", "the newest frame was evicted instead of the oldest");
  assert.ok(!frames.some((f) => f.startsWith("0:")), "the oldest frame survived the cap");
});

test("a single OVERSIZED frame is kept rather than evicted to nothing", async () => {
  // The eviction loop stops at one frame. Otherwise a turn whose first output exceeds the cap would show the
  // operator nothing at all — the worst outcome for the biggest event.
  const s = session();
  s._pushTerminalFrame("y".repeat(200_000));

  const frames = [];
  s.attachTerminalSink((text) => { frames.push(text); });
  await drain();
  assert.equal(frames.length, 1, "an oversized frame was dropped entirely");
  assert.equal(frames[0].length, 200_000, "the frame was truncated rather than passed through");
});

test("detaching mid-flush PAUSES the feed — the rest is kept, not discarded", async () => {
  // The flush loop re-checks the sink on every iteration, and that has to mean "stop", not "keep shifting frames
  // into a sink that is gone". A dashboard that navigated away must be able to come back and see what it missed.
  //
  // Asserting only that delivery STOPPED is not enough: without the re-check the loop drains every frame and the
  // failed calls are swallowed, so the first list looks identical. The survivor is what the second half catches.
  const frames = [];
  const s = session();
  s.attachTerminalSink(async (text) => {
    frames.push(text);
    await new Promise((resolve) => setTimeout(resolve, 30));
    if (text === "one") s.detachTerminalSink();
  });

  s._pushTerminalFrame("one");
  s._pushTerminalFrame("two");
  s._pushTerminalFrame("three");
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.deepEqual(frames, ["one"], "the flush kept delivering after the sink was detached");

  const resumed = [];
  s.attachTerminalSink((text) => { resumed.push(text); });
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.deepEqual(resumed, ["two", "three"],
    "the frames after the detach were consumed by a sink that no longer existed");
});

test("a NON-FUNCTION sink does not consume the backlog", async () => {
  // Stored rather than rejected, a bogus sink is truthy: the flush loop shifts each frame, the call throws, the
  // catch swallows it, and the buffer empties into nothing. The output is gone with no error anywhere.
  const s = session();
  s.attachTerminalSink("not a function");
  s._pushTerminalFrame("survives the bogus sink");
  await drain();

  const frames = [];
  s.attachTerminalSink((text) => { frames.push(text); });
  await drain();
  assert.deepEqual(frames, ["survives the bogus sink"], "the backlog was drained into a non-function sink");
});

test("a SECOND batch flushes after the first one finished", async () => {
  // `_flushing` is a re-entrancy guard, and it has to be cleared when the loop ends. Left set, the first batch of
  // a turn is delivered and every frame after it sits in the buffer forever — a console that shows the opening
  // moments of a turn and then freezes.
  const frames = [];
  const s = session();
  s.attachTerminalSink((text) => { frames.push(text); });

  s._pushTerminalFrame("batch one");
  await drain();
  assert.deepEqual(frames, ["batch one"]);

  s._pushTerminalFrame("batch two");
  await drain();
  assert.deepEqual(frames, ["batch one", "batch two"], "the flush never ran again after the first batch");
});

test("a sink that fails does not stop the frames behind it", async () => {
  const seen = [];
  const s = session();
  s.attachTerminalSink(async (text) => {
    if (text === "boom") throw new Error("sink failed");
    seen.push(text);
  });

  s._pushTerminalFrame("before");
  s._pushTerminalFrame("boom");
  s._pushTerminalFrame("after");
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert.deepEqual(seen, ["before", "after"]);
});

test("an empty frame is not buffered at all", async () => {
  const frames = [];
  const s = session();
  s._pushTerminalFrame("", "");
  s._pushTerminalFrame(null);
  s.attachTerminalSink((text, status) => { frames.push([text, status]); });
  await drain();
  assert.deepEqual(frames, [], "an empty frame was buffered");

  // A status-only frame is real and IS buffered.
  s._pushTerminalFrame("", "running");
  await drain();
  assert.deepEqual(frames, [["", "running"]]);
});

test("attaching a non-function clears the sink and buffers instead", async () => {
  const s = session();
  s.attachTerminalSink((text) => { throw new Error(`the cleared sink ran for ${text}`); });
  s.attachTerminalSink(null);
  s._pushTerminalFrame("while cleared");
  await drain();

  const frames = [];
  s.attachTerminalSink((text) => { frames.push(text); });
  await drain();
  assert.deepEqual(frames, ["while cleared"],
    "output produced while no sink was attached did not survive to the next one");
});

// ── runtime logs ────────────────────────────────────────────────────────────

test("a runtime log line is forwarded to the caller's event hook", async () => {
  const events = [];
  session()._dispatchRuntimeLog("  codex says   something  ", {
    onEvent: (kind, text) => events.push([kind, text]),
  });
  // `quoteForDisplay` collapses whitespace, so the console shows one tidy line per log.
  assert.deepEqual(events, [["stderr", "codex says something"]]);
});

test("a blank runtime log line is not forwarded", async () => {
  const events = [];
  for (const line of ["", "   ", "\t\n", null, undefined]) {
    session()._dispatchRuntimeLog(line, { onEvent: (kind, text) => events.push([kind, text]) });
  }
  assert.deepEqual(events, [], "an empty log line produced a console event");
});

test("a FATAL runtime log fails the active turn and records why", async () => {
  // "worker quit with fatal" / "Transport channel closed" are unrecoverable. Leaving the turn running would hang
  // the dispatch until a timeout with no explanation of what happened.
  const s = session();
  const turn = { settled: false };
  s._activeTurn = turn;
  s._dispatchRuntimeLog("codex worker quit with fatal error", { onEvent: () => {} });

  assert.equal(turn.settled, true, "a fatal runtime log left the turn running");
  assert.equal(turn.finalStatus, "failed");
  assert.match(turn.finalError, /worker quit with fatal/,
    "the failure does not carry the log line that caused it");
  assert.match(s._fatalRuntimeError, /Codex runtime fatal error/,
    "the session did not record the fatal error for later turns to report");
});

test("a TRANSIENT log is not treated as fatal", async () => {
  // Deliberate: a bare websocket close used to tear down the shared app-server and fail the turn on every
  // transient disconnect. Only genuinely unrecoverable signals are instant-fatal now.
  const s = session();
  const turn = { settled: false };
  s._activeTurn = turn;
  s._dispatchRuntimeLog("websocket closed with code 1006", { onEvent: () => {} });

  assert.equal(turn.settled, false, "a transient disconnect failed the turn");
  assert.equal(s._fatalRuntimeError, null);
});

test("a fatal log with no active turn still records the error and does not throw", async () => {
  const s = session();
  assert.doesNotThrow(() => s._dispatchRuntimeLog("Transport channel closed", {}));
  assert.match(s._fatalRuntimeError, /Transport channel closed/);
});

test("a fatal log does not re-settle a turn that has already settled", async () => {
  const s = session();
  const turn = { settled: true, finalStatus: "completed" };
  s._activeTurn = turn;
  s._dispatchRuntimeLog("Transport channel closed", { onEvent: () => {} });
  assert.equal(turn.finalStatus, "completed", "a completed turn was rewritten as failed");
});

// ── spawn errors ────────────────────────────────────────────────────────────

test("a spawn error REJECTS the startup barrier so concurrent callers fail fast", async () => {
  // Without this, every ensureStarted() waiting on the shared deferred hangs until the handshake timeout, and
  // the operator watches an agent do nothing for the length of that window instead of seeing the spawn failure.
  const events = [];
  const s = session({ onPoolEvent: (kind, payload) => events.push([kind, payload]) });
  s._state = "starting";
  let rejection = null;
  s._startupDeferred = { reject: (error) => { rejection = error; } };

  s._onSpawnError(new Error("spawn ENOENT codex"));

  assert.equal(s._state, "failed", "the session stayed in a startable state after a terminal spawn error");
  assert.equal(s._startupDeferred, null, "the barrier was left in place to be rejected twice");
  assert.match(rejection?.message, /codex spawn error: spawn ENOENT codex/);
  assert.deepEqual(events, [["spawn-error", { message: "spawn ENOENT codex" }]]);
});

test("a spawn error reports even when there is no barrier to reject", async () => {
  const events = [];
  const s = session({ onPoolEvent: (kind, payload) => events.push([kind, payload]) });
  s._state = "ready";
  s._startupDeferred = null;

  assert.doesNotThrow(() => s._onSpawnError(new Error("boom")));
  assert.equal(s._state, "failed");
  assert.deepEqual(events, [["spawn-error", { message: "boom" }]]);
});

test("a spawn error on an idle or stopped session leaves its state alone", async () => {
  // The flip is scoped to starting/ready. A stray error after stop() must not resurrect the session as "failed"
  // and make the pool's heal-on-lookup evict something that already went away cleanly.
  for (const state of ["idle", "stopped", "failed"]) {
    const s = session();
    s._state = state;
    s._onSpawnError(new Error("late error"));
    assert.equal(s._state, state, `${state} was overwritten by a late spawn error`);
  }
});

test("a spawn error with no Error object still reports something readable", async () => {
  const events = [];
  const s = session({ onPoolEvent: (kind, payload) => events.push([kind, payload]) });
  s._onSpawnError("just a string");
  assert.deepEqual(events, [["spawn-error", { message: "just a string" }]]);
});

// ── steer ───────────────────────────────────────────────────────────────────

test("steer sends turn/steer for the ACTIVE turn id", async () => {
  const sent = [];
  const s = session();
  s.threadId = "th_9";
  s._activeTurn = { activeTurnId: "turn_3" };
  s._rpc = { request: async (method, params, timeout) => { sent.push([method, params, timeout]); } };

  await s.steer("more context");
  assert.deepEqual(sent, [[
    "turn/steer",
    { threadId: "th_9", input: [{ type: "text", text: "more context" }], expectedTurnId: "turn_3" },
    30000,
  ]]);
});

test("steer refuses when there is no turn to steer", async () => {
  const s = session();
  s._rpc = { request: async () => { throw new Error("must not be called"); } };
  await assert.rejects(() => s.steer("text"), /No active Codex turn to steer/);

  s._activeTurn = { activeTurnId: "" }; // a turn that has not been assigned an id yet
  await assert.rejects(() => s.steer("text"), /No active Codex turn to steer/);
});

test("steer refuses an empty body before reaching the app-server", async () => {
  const sent = [];
  const s = session();
  s._activeTurn = { activeTurnId: "turn_3" };
  s._rpc = { request: async (method) => { sent.push(method); } };

  for (const body of ["", "   ", "\t\n", null, undefined]) {
    await assert.rejects(() => s.steer(body), /Steer body is required/, `${JSON.stringify(body)} was accepted`);
  }
  assert.deepEqual(sent, [], "an empty steer reached the app-server");
});
